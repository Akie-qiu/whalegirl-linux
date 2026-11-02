"""A delayed image acknowledgement must never create a false failure notice."""
import json
import time
import unittest

import test_image_tools as fixture
from whalegirl_linux.delivery import reconcile_outbound
from whalegirl_linux.gateway import Receipt


class PictureReceiptTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ImageToolTests.asyncTearDown

    async def asyncSetUp(self):
        (fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        await fixture.ImageToolTests.asyncSetUp(self)

    async def submitted_image(self):
        self.config.save(self.config.settings.model_copy(update={'min_send_interval': 0, 'chat_cooldown': 0}))
        self.asset = self.images.save_asset(self.raw, 'generated', scope='chat', scope_chat='private')
        job_id = self.images.enqueue(kind='send', asset_id=self.asset['id'], chat='private', destination='chat')
        self.gateway.status_for_send = self.gateway.status
        self.submissions = 0
        async def send_image(chat, asset_id):
            self.submissions += 1
            return Receipt(False, error='已提交微信，回执待核实', details={
                'asset_id': asset_id, 'before_ids': ['local:5'], 'local_id': 6, 'submitted_at': time.time()})
        self.gateway.send_image = send_image
        await self.images.process_once()
        return job_id

    def row(self, confirmed=True):
        return {'type': 3, 'isSelf': True, 'localId': 6, 'serverId': '1234' if confirmed else 0,
                'nativeStatus': 2 if confirmed else 5, 'timestamp': time.time()}

    async def test_pending_delivery_has_durable_details_no_notice_and_late_ack_settles_both_records(self):
        job_id = await self.submitted_image()
        self.assertEqual(self.images.get(job_id)['state'], 'uncertain')
        self.assertIsNone(self.images.next_notice())
        action = self.store.actions('picture:' + job_id)[0]
        self.assertEqual(json.loads(action['details_json'])['asset_id'], self.asset['id'])
        settled = reconcile_outbound(self.store, 'account', 'private', [self.row()])
        self.assertEqual(len(settled), 1)
        self.assertEqual(self.images.get(job_id)['state'], 'sent')
        self.assertEqual(self.images.get(job_id)['receipt'], '1234')
        self.assertEqual(self.images.get(job_id)['error'], '')
        self.assertIsNone(self.images.next_notice())
        self.assertFalse(reconcile_outbound(self.store, 'account', 'private', [self.row()]))
        self.assertEqual(self.submissions, 1)
        self.assertFalse(self.gateway.sent)

    async def test_definite_late_failure_can_notify_but_does_not_resubmit(self):
        job_id = await self.submitted_image()
        reconcile_outbound(self.store, 'account', 'private', [self.row(False)])
        self.assertEqual(self.images.get(job_id)['state'], 'failed')
        self.assertEqual(self.images.next_notice()['id'], job_id)
        self.assertEqual(self.submissions, 1)

    async def test_wrong_chat_or_local_row_cannot_confirm_image(self):
        job_id = await self.submitted_image()
        self.assertFalse(reconcile_outbound(self.store, 'account', 'other-chat', [self.row()]))
        self.assertFalse(reconcile_outbound(self.store, 'account', 'private', [{**self.row(), 'localId': 9}]))
        self.assertEqual(self.images.get(job_id)['state'], 'uncertain')

    async def test_generation_interruption_still_has_a_truthful_result_notice(self):
        job_id = self.images.enqueue(kind='generate', prompt='蓝鲸', chat='private', destination='chat')
        self.images.change(job_id, state='uncertain', error='供应商结果中断')
        self.assertEqual(self.images.next_notice()['id'], job_id)


if __name__ == '__main__':
    unittest.main()

"""A neighbouring favorite that WeChat actually sent is reported, never resent."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from whalegirl_linux.delivery import STICKER_ANCHOR_CACHE, misdelivered_stickers, send_native
from whalegirl_linux.gateway import Receipt

ROOT = Path(__file__).resolve().parents[1]
WANTED = 'a' * 32


class FakeGateway:
    def __init__(self, after_rows):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.ui_jobs_directory = Path(tempfile.mkdtemp(prefix='misdelivery-', dir=runs))
        (self.ui_jobs_directory / STICKER_ANCHOR_CACHE).write_text(json.dumps({
            'updated': 1, 'anchors': {'tile:old': 3}, 'grid': {'xs': [0], 'shift': 0}}))
        self.ui_lock = asyncio.Lock()
        self.after_rows = after_rows
        self.submitted = 0
        self.reads = 0

    async def messages(self, chat):
        self.reads += 1
        earlier = {'isSelf': False, 'type': 1, 'localId': 100, 'serverId': '900', 'content': 'hi'}
        return [earlier] if self.submitted == 0 else [earlier, *self.after_rows]

    async def ui_job(self, operation, chat, **values):
        self.submitted += 1
        return {'state': 'submitted', 'job_id': 'job-1'}


def sticker_row(md5, server_id='901'):
    return {'isSelf': True, 'type': 47, 'localId': 101, 'serverId': server_id,
            'timestamp': __import__('time').time(), 'sticker': {'md5': md5}}


class StickerMisdeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_favorite_is_reported_and_marks_anchors_stale(self):
        gateway = FakeGateway([sticker_row('b' * 32)])
        receipt = await send_native(gateway, 'sticker', 'chat', md5=WANTED)
        self.assertFalse(receipt.confirmed)
        self.assertTrue(receipt.definite_failure)
        self.assertFalse(receipt.retryable)
        self.assertTrue(receipt.sticker_misdelivered)
        self.assertIn('发错表情', receipt.error)
        self.assertEqual(receipt.details['wrong_sticker_md5'], 'b' * 32)
        self.assertTrue(receipt.details['anchors_invalidated'])
        cache = json.loads((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).read_text())
        self.assertTrue(cache['invalidated'])
        self.assertIn('invalidated_at', cache)
        self.assertEqual(gateway.submitted, 1, 'a visible wrong sticker must never be followed by a resend')

    async def test_requested_favorite_is_confirmed_and_keeps_anchors(self):
        gateway = FakeGateway([sticker_row(WANTED)])
        receipt = await send_native(gateway, 'sticker', 'chat', md5=WANTED)
        self.assertTrue(receipt.confirmed)
        self.assertFalse(receipt.sticker_misdelivered)
        self.assertTrue((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).exists())

    async def test_correct_receipt_wins_over_another_new_own_sticker(self):
        gateway = FakeGateway([sticker_row(WANTED), sticker_row('b' * 32, '902')])
        receipt = await send_native(gateway, 'sticker', 'chat', md5=WANTED)
        self.assertTrue(receipt.confirmed)
        self.assertFalse(receipt.sticker_misdelivered)
        cache = json.loads((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).read_text())
        self.assertFalse(cache.get('invalidated'))
        self.assertEqual(gateway.submitted, 1)

    def test_old_unacknowledged_and_baseline_rows_are_not_misdelivery(self):
        now = time.time()
        details = {'md5': WANTED, 'submitted_at': now, 'before_local_id': 100,
                   'before_ids': ['server:known']}
        rows = [
            {**sticker_row('b' * 32), 'timestamp': now - 60},
            {**sticker_row('b' * 32), 'localId': 99},
            sticker_row('b' * 32, '0'),
        ]
        self.assertEqual(misdelivered_stickers(details, rows), [])

    async def test_missing_row_stays_uncertain_not_misdelivered(self):
        gateway = FakeGateway([])
        receipt = await send_native(gateway, 'sticker', 'chat', md5=WANTED)
        self.assertFalse(receipt.confirmed)
        self.assertFalse(receipt.definite_failure)
        self.assertFalse(receipt.sticker_misdelivered)
        self.assertTrue((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).exists())

    async def test_text_actions_never_report_sticker_misdelivery(self):
        gateway = FakeGateway([sticker_row('b' * 32)])
        receipt = await send_native(gateway, 'text', 'chat', text='好')
        self.assertFalse(receipt.sticker_misdelivered)
        self.assertTrue((gateway.ui_jobs_directory / STICKER_ANCHOR_CACHE).exists())

    def test_property_requires_sticker_kind_and_wrong_md5(self):
        self.assertFalse(Receipt(False, definite_failure=True, details={'kind': 'sticker'}).sticker_misdelivered)
        self.assertTrue(Receipt(False, definite_failure=True,
                                details={'kind': 'sticker', 'wrong_sticker_md5': 'b' * 32}).sticker_misdelivered)


if __name__ == '__main__':
    unittest.main()

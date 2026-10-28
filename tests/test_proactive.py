"""Focused regression checks; gateways and models are fakes, no chat is sent."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from whalegirl_linux.app import create_app
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]
CHAT = 'group@chatroom'


class Gateway:
    def __init__(self):
        self.sent = []
        self.login = {}
        self.confirmed = True
        self.rows = []

    async def status(self):
        return {'status': 'logged_in', 'loggedInUser': 'account'}

    async def status_for_send(self):
        return await self.status()

    async def send(self, chat, text, **options):
        self.sent.append((chat, text))
        return Receipt(self.confirmed, str(len(self.sent)))

    async def send_sticker(self, chat, md5):
        return await self.send(chat, md5)

    async def close(self):
        pass

    async def messages(self, chat, **kwargs):
        return self.rows if chat == CHAT else []


class Engine:
    def __init__(self, reply, hook=None):
        self.reply, self.hook = reply, hook
        self.last_error = ''

    async def chat(self, messages, **kwargs):
        if self.hook:
            self.hook()
        return json.dumps(self.reply, ensure_ascii=False)

    async def close(self):
        pass


class ProactiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runs = ROOT/'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='proactive-', dir=runs))
        self.config = Config(self.folder)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=[CHAT, 'friend'],
            proactive_enabled=True, proactive_chat_ids=[CHAT], proactive_start_hour=0, proactive_end_hour=0,
            proactive_idle_minutes=2, proactive_cooldown_minutes=10,
            proactive_decision_minutes=3, sticker_after_text='off'), 'test-key')
        self.store = Store(self.folder/'state.sqlite3')
        self.gateway = Gateway()
        self.reply = {'should_reply': True, 'reason': '接续本群游戏话题',
                      'actions': [{'type': 'text', 'text': '昨天那关后来过了没'}]}
        self.hook = None
        self.calls = 0

        def factory(**kwargs):
            self.calls += 1
            return Engine(self.reply, self.hook)

        self.runtime = Runtime(self.config, self.store, self.gateway, ROOT/'styles', engine_factory=factory)
        self.runtime.account_id = 'account'
        self.runtime.gateway_state = {'status': 'logged_in'}
        self.service = self.runtime.proactive
        self.service.started_at = 0
        self.event = self.message(1)
        self.store.observe([self.event])
        self.store.baseline('account', CHAT)

    def message(self, number, **changes):
        return dict(key=f'account/{CHAT}/{number}', account='account', chat=CHAT, sender='群友',
                    sender_id='person', content='昨天游戏这关没过', timestamp=time.time()-3600,
                    kind=1, is_self=0, local_id=number, payload_json='{}', **changes)

    def configure(self, **values):
        self.config.save(self.config.settings.model_copy(update=values))

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_disabled_private_and_strict_permissions_do_not_call_model(self):
        self.configure(proactive_enabled=False)
        self.assertFalse(await self.service.run_once())
        self.assertEqual(self.calls, 0)
        self.configure(proactive_enabled=True, trigger_only_chat_ids=[CHAT])
        self.assertFalse(self.service.permitted(CHAT))
        self.configure(proactive_strict_chat_ids=[CHAT])
        self.assertTrue(self.service.permitted(CHAT))
        self.configure(proactive_chat_ids=[CHAT, 'friend'])
        self.assertFalse(self.service.permitted('friend'))
        self.configure(proactive_private_enabled=True)
        self.assertTrue(self.service.permitted('friend'))

    async def test_default_pacing_leaves_timing_to_model(self):
        defaults = Settings()
        self.assertEqual((defaults.proactive_chat_per_day, defaults.proactive_cooldown_minutes,
                          defaults.proactive_idle_minutes), (0, 0, 0))

    async def test_repeated_tail_reads_do_not_extend_a_stop_request(self):
        self.gateway.rows = [{'serverId':2, 'localId':2, 'type':1, 'content':'先别聊了', 'sender':'person',
            'senderName':'群友', 'isSelf':False, 'isMentioned':True, 'timestamp':time.time()}]
        await self.runtime.poll_once()
        first_pause = self.service.chat_state(CHAT)['paused_until']
        self.assertGreater(first_pause, time.time())
        await self.runtime.poll_once()
        self.assertEqual(self.service.chat_state(CHAT)['paused_until'], first_pause)

    async def test_sent_opener_uses_ledger_and_is_not_a_human_memory_source(self):
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.gateway.sent, [(CHAT, '昨天那关后来过了没')])
        run = self.service.snapshot()['runs'][0]
        self.assertEqual(run['state'], 'sent')
        actions = self.store.actions('proactive:'+run['id'])
        self.assertEqual([a['state'] for a in actions], ['sent'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM message_log').fetchone()[0], 1)
        self.assertEqual(self.store.context('account', CHAT, 5),
                         [{'role': 'assistant', 'content': '昨天那关后来过了没'}])
        self.assertFalse(self.service.eligible(CHAT, time.time()))
        self.assertEqual(self.calls, 1)

    async def test_new_incoming_message_preempts_planned_opener(self):
        self.hook = lambda: self.store.observe([{**self.event, 'key':'new', 'local_id':2, 'timestamp':time.time()}])
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'skipped')

    async def test_waiting_for_slow_image_api_does_not_block_proactive_text(self):
        self.runtime.images.generating = 'slow-image-job'
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.gateway.sent, [(CHAT, '昨天那关后来过了没')])
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'sent')
        self.gateway.ui_lock = asyncio.Lock()
        await self.gateway.ui_lock.acquire()
        self.assertTrue(self.service.blocked())
        self.gateway.ui_lock.release()

    async def test_new_message_after_confirmed_first_part_keeps_short_reply_together(self):
        self.reply['actions'].append({'type':'text','text':'我还挺好奇的'})
        send = self.gateway.send
        async def incoming(chat, text, **kwargs):
            receipt = await send(chat, text, **kwargs)
            self.store.observe([{**self.event, 'key':'incoming', 'local_id':2, 'timestamp':time.time()}])
            return receipt
        self.gateway.send = incoming
        await self.service.run_once()
        self.assertEqual(len(self.gateway.sent), 2)
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'sent')

    async def test_revoked_permission_still_stops_after_first_confirmed_part(self):
        self.reply['actions'].append({'type':'text','text':'这句不能发'})
        send = self.gateway.send
        async def revoke(chat, text, **kwargs):
            receipt = await send(chat, text, **kwargs)
            self.configure(proactive_enabled=False)
            return receipt
        self.gateway.send = revoke
        await self.service.run_once()
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'partial')

    async def test_empty_model_answer_reports_provider_error_and_silence_clears_it(self):
        engine = Engine(None)
        async def empty(*args, **kwargs):
            engine.last_error = 'HTTP 404（模型或接口不可用）'
            return ''
        engine.chat = empty
        factory = self.runtime.engine_factory
        self.runtime.engine_factory = lambda **kwargs: engine
        await self.service.run_once()
        self.assertIn('HTTP 404', self.service.snapshot()['runs'][0]['error'])
        self.runtime.engine_factory = factory
        self.reply = {'should_reply':False, 'actions':[]}
        self.service.touch(CHAT, last_decision=0)
        await self.service.run_once()
        self.assertEqual(self.service.last_error, '')

    async def test_revoking_permission_during_generation_cancels_send(self):
        self.hook = lambda: self.configure(proactive_enabled=False)
        await self.service.run_once()
        self.assertEqual(self.gateway.sent, [])

    async def test_silence_does_not_consume_daily_send_quota(self):
        self.reply = {'should_reply': False, 'reason':'暂无新话题', 'actions':[]}
        await self.service.run_once()
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'ignored')
        self.assertEqual(self.gateway.sent, [])
        self.assertTrue(self.service.quota(CHAT, time.time()))

    async def test_uncertain_send_continues_later_independent_actions(self):
        self.gateway.confirmed = False
        self.reply['actions'].append({'type':'text', 'text':'接着说一句'})
        await self.service.run_once()
        self.assertEqual(len(self.gateway.sent), 2)
        self.assertEqual(self.service.snapshot()['runs'][0]['state'], 'uncertain')
        self.assertFalse(self.runtime.paused_reason)

    async def test_strict_group_continuation_is_scoped_and_expires(self):
        self.configure(trigger_only_chat_ids=[CHAT], proactive_strict_chat_ids=[CHAT])
        self.service.touch(CHAT, waiting_after=time.time()-1, session_until=time.time()+600,
                           partners=json.dumps(['person']))
        event = {**self.event, 'timestamp':time.time()}
        self.assertTrue(self.service.continuation(event))
        self.assertFalse(self.service.continuation({**event, 'sender_id':'other'}))
        self.assertFalse(self.service.continuation(event, now=time.time()+601))
        self.configure(proactive_enabled=False)
        self.assertFalse(self.service.continuation(event))

    async def test_direct_stop_pauses_without_archiving_quoted_instructions(self):
        self.service.observe(self.event, {'isSelf':False, 'type':1, 'content':'先别聊了', 'isMentioned':True})
        self.assertFalse(self.service.permitted(CHAT))
        self.service.pause(CHAT, resume=True)
        self.assertTrue(self.service.permitted(CHAT))
        self.service.observe(self.event, {'isSelf':False, 'type':1, 'content':'先别聊了', 'forwarded':{'items':[]}})
        # A nonempty forwarded payload is always ignored.
        self.service.pause(CHAT, resume=True)
        self.service.observe(self.event, {'isSelf':False, 'type':1, 'content':'普通聊天',
            'reply':{'content':'先别聊了'}, 'isMentioned':True})
        self.assertTrue(self.service.permitted(CHAT))

    async def test_proactive_image_permission_is_rechecked(self):
        await self.service.run_once()
        run = self.service.snapshot()['runs'][0]
        job = dict(source_key='proactive:'+run['id'], account='account', chat=CHAT)
        self.runtime.images.check_proactive_permission(job)
        self.configure(proactive_enabled=False)
        with self.assertRaisesRegex(ValueError, '权限'):
            self.runtime.images.check_proactive_permission(job)

    async def test_restarted_scheduler_event_is_skipped(self):
        event = {**self.event, 'key':'proactive:old', 'trigger':'proactive', 'timestamp':time.time()}
        self.store.insert(event)
        await self.runtime.process_next()
        self.assertEqual(self.store.get(event['key'])['state'], 'skipped')
        self.assertEqual(self.calls, 0)


class ProactiveApiTests(unittest.TestCase):
    def test_config_requires_auth_and_cannot_authorize_unselected_contact(self):
        runs = ROOT/'test-runs'
        runs.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='proactive-api-', dir=runs))
        app = create_app(folder, gateway=Gateway(), run_background=False)
        client = TestClient(app)
        response = client.get('/api/proactive')
        self.assertEqual(response.status_code, 401)
        config = app.state.config
        headers = {'Authorization':'Bearer '+config.admin_token}
        response = client.post('/api/proactive/config', headers=headers,
                               json={'values':{'proactive_enabled':True,'proactive_chat_ids':['stranger']}})
        self.assertEqual(response.status_code, 400)
        response = client.post('/api/proactive/config', headers=headers, json={'values':{'proactive_per_day':7}})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['proactive_enabled'])
        app.state.runtime.store.close()


if __name__ == '__main__':
    unittest.main()

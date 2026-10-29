"""Real failure transitions and no-send UI recovery; fake gateway only."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import tempfile
import time
from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings

import test_chat_habits as habits
from whalegirl_linux.gateway import Receipt


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    def ready(self):
        self.gateway.reset_ui = AsyncMock(return_value={'main_ready':True})
        self.gateway.safety_notice = AsyncMock(return_value='')
        self.gateway.native_request = AsyncMock(return_value={'stickers':[]})

    def fail(self, attempts=2):
        self.runtime.safety.outcome('account', Receipt(False, error='favorite outside exposed page',
            definite_failure=True, attempts=attempts), self.config.settings, chat='group@chatroom')

    async def test_two_failures_recover_once_and_new_success_clears_counter(self):
        self.ready(); self.fail()
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'pending')
        self.assertFalse(self.runtime.safety.state('account').get('hold_reason'))
        await self.runtime.recovery.run()
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'awaiting_send')
        self.assertFalse(self.runtime.safety.held('account'))
        self.assertEqual(self.gateway.sent,[])
        self.runtime.safety.outcome('account',Receipt(True),self.config.settings)
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'recovered')
        self.assertEqual(self.runtime.recovery.snapshot()['consecutive_failures'],0)
        self.assertEqual(self.gateway.reset_ui.await_count,1)

    async def test_failure_after_recovery_pauses_without_another_reset(self):
        self.ready(); self.fail(); await self.runtime.recovery.run()
        self.fail(attempts=1)
        self.assertTrue(self.runtime.safety.held('account'))
        await self.runtime.recovery.run()
        self.assertEqual(self.gateway.reset_ui.await_count,1)

    async def test_recovery_failure_pauses_and_is_not_automatically_repeated(self):
        self.ready(); self.fail(); self.gateway.reset_ui.side_effect=ValueError('主界面不可用')
        await self.runtime.recovery.run()
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'failed')
        self.assertTrue(self.runtime.safety.held('account'))
        self.runtime.recovery.adopt_legacy(); await self.runtime.recovery.run()
        self.assertEqual(self.gateway.reset_ui.await_count,1)

    async def test_uncertain_receipt_never_requests_recovery_or_pauses(self):
        self.ready()
        self.runtime.safety.outcome('account',Receipt(False,error='回执待核实'),self.config.settings)
        await self.runtime.recovery.run()
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'idle')
        self.assertFalse(self.runtime.safety.held('account'))
        self.gateway.reset_ui.assert_not_awaited()

    async def test_switch_off_keeps_the_existing_two_failure_pause(self):
        self.config.save(self.config.settings.model_copy(update={'send_auto_recovery_enabled':False}))
        self.ready(); self.fail(); await self.runtime.recovery.run()
        self.assertTrue(self.runtime.safety.held('account'))
        self.gateway.reset_ui.assert_not_awaited()

    async def test_existing_send_hold_is_adopted_but_client_warning_is_not(self):
        self.ready(); self.runtime.safety.hold('account','failure_circuit','旧发送暂停')
        self.runtime.recovery.adopt_legacy(); await self.runtime.recovery.run()
        self.assertFalse(self.runtime.safety.held('account'))
        self.runtime.safety.resume('account')
        self.runtime.safety.hold('account','client_warning','微信异常提示')
        self.runtime.recovery.adopt_legacy(); await self.runtime.recovery.run()
        self.assertEqual(self.runtime.safety.state('account')['hold_code'],'client_warning')
        self.assertEqual(self.gateway.reset_ui.await_count,1)

    async def test_wrong_account_does_not_reset_the_ui(self):
        self.ready(); self.fail()
        self.gateway.status=AsyncMock(return_value={'status':'logged_in','loggedInUser':'other'})
        await self.runtime.recovery.run()
        self.gateway.reset_ui.assert_not_awaited()
        self.assertTrue(self.runtime.safety.held('account'))

    async def test_manual_refresh_preserves_a_pause_and_the_reply_switch(self):
        self.ready(); self.runtime.safety.hold('account','failure_circuit','发送暂停')
        self.config.save(self.config.settings.model_copy(update={'enabled':False}))
        await self.runtime.recovery.run(manual=True,resume=False)
        self.assertTrue(self.runtime.safety.held('account'))
        self.runtime.recovery.adopt_legacy()
        self.assertEqual(self.runtime.recovery.snapshot()['status'],'refreshed')
        self.assertFalse(self.config.settings.enabled)
        await self.runtime.recovery.run(manual=True,resume=True)
        self.assertFalse(self.runtime.safety.held('account'))
        self.assertFalse(self.config.settings.enabled)

    async def test_ui_recovery_waits_for_the_active_chat_turn(self):
        self.ready(); self.fail()
        async with self.runtime.chat_turn_lock:
            task=asyncio.create_task(self.runtime.recovery.run())
            await asyncio.sleep(.01)
            self.gateway.reset_ui.assert_not_awaited()
        await task
        self.gateway.reset_ui.assert_awaited_once()

    async def test_new_messages_queue_during_recovery_instead_of_being_ignored(self):
        self.ready(); self.fail()
        self.store.baseline('account','group@chatroom')
        self.gateway.messages=AsyncMock(return_value=[{'serverId':1,'localId':1,'type':1,
            'content':'鲸鱼娘 你好','sender':'person','senderName':'测试用户',
            'isSelf':False,'isMentioned':True,'timestamp':time.time()}])
        await self.runtime.poll_once()
        self.assertEqual(self.store.get(self.event()['key'])['state'],'queued')
        self.assertFalse(await self.runtime.process_next())
        await self.runtime.recovery.run()
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'收到啦'}]}]
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent,[('text','收到啦')])


class RecoveryApiTests(unittest.TestCase):
    def test_controls_require_auth_and_do_not_enable_reply_or_resend_old_messages(self):
        root=Path(__file__).resolve().parents[1]
        (root/'test-runs').mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='recovery-api-',dir=root/'test-runs'))
        gateway=habits.Gateway()
        gateway.reset_ui=AsyncMock(return_value={'main_ready':True})
        gateway.safety_notice=AsyncMock(return_value='')
        gateway.native_request=AsyncMock(return_value={'stickers':[]})
        app=create_app(folder,gateway=gateway,run_background=False)
        config=app.state.config;runtime=app.state.runtime
        config.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom']))
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/recovery/run',json={'resume':True}).status_code,401)
            headers={'Authorization':'Bearer '+config.admin_token}
            runtime.safety.hold('account','failure_circuit','旧发送暂停')
            self.assertEqual(client.post('/api/recovery/run',headers=headers,json={'resume':True}).status_code,200)
            self.assertFalse(runtime.safety.held('account'))
            self.assertFalse(config.settings.enabled)
            self.assertEqual(gateway.sent,[])
            self.assertEqual(client.post('/api/recovery/config',headers=headers,json={'enabled':False}).status_code,200)
            self.assertFalse(config.settings.send_auto_recovery_enabled)
            self.assertEqual(client.get('/api/recovery/issues',headers=headers).json(),{'pending':[],'failed':[]})


class NativeRecoveryTests(unittest.TestCase):
    def test_chat_navigation_uses_the_current_native_layout(self):
        path=Path(__file__).resolve().parents[1]/'deploy/native_recovery.py'
        spec=importlib.util.spec_from_file_location('native_recovery_check',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        contacts={'role':'push-button','name':'Contacts','bounds':{'x':151,'y':170,'width':60,'height':40}}
        chats={'role':'push-button','name':'','bounds':{'x':151,'y':122,'width':60,'height':40}}
        ui=SimpleNamespace(walk=lambda root:root)
        self.assertIs(module.chat_navigation(ui,[contacts,chats]),chats)
        wrong={**chats,'bounds':{**chats['bounds'],'x':300}}
        self.assertIsNone(module.chat_navigation(ui,[contacts,wrong]))


if __name__=='__main__':unittest.main()

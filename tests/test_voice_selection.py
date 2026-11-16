"""Curated voice switching, owner override, rollback and cloud adoption regressions."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import test_audio as fixtures
import test_pc_voice as pc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from pc_voice_common import request_control, read_control, write_json
from pc_voice_engine import load_engine
import pc_voice_selection as selection


CYRENE = {'backend': 'gpt-sovits', 'voice_id': 'cyrene'}
YACHIYO = {'backend': 'gpt-sovits', 'voice_id': 'yachiyo'}


def installed(root):
    write_json(root / 'voices.json', [
        {'id': 'cyrene', 'name': '昔涟', 'engine': CYRENE},
        {'id': 'yachiyo', 'name': '八千代', 'engine': YACHIYO}])
    write_json(root / 'engine.json', CYRENE)
    request_control(root, True)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.root = pc.folder()
        installed(self.root)

    def request(self):
        with patch.object(selection.subprocess, 'Popen') as spawn:
            result = selection.request_switch(self.root, 'yachiyo', 'b' * 32)
            self.assertEqual(spawn.call_count, 1)
        return result

    def controller(self, fail=False):
        operations = []
        def action(root, operation):
            voice = json.loads((root / 'engine.json').read_text())['voice_id']
            if fail and voice == 'yachiyo' and operation == 'start':
                raise RuntimeError('synthetic model load failure')
            operations.append((operation, voice))
            intent = request_control(root, operation == 'start')
            return {'request_id': intent['request_id']}
        def snapshot(root):
            desired = read_control(root)['desired']
            return {'phase': 'running' if desired else 'stopped', 'worker_running': desired,
                    'model_ready': desired, 'active_voice': json.loads((root / 'engine.json').read_text())['voice_id']}
        return SimpleNamespace(action=action, snapshot=snapshot,
                               psutil=SimpleNamespace(NoSuchProcess=ProcessLookupError,
                                                      Process=lambda pid: None)), operations

    def test_ids_and_idempotency_reject_uninstalled_and_concurrent_requests(self):
        with self.assertRaises(ValueError):
            selection.request_switch(self.root, '../other', 'a' * 32)
        self.request()
        with patch.object(selection.subprocess, 'Popen') as spawn:
            self.assertEqual(selection.request_switch(self.root, 'yachiyo', 'b' * 32)['phase'], 'queued')
            spawn.assert_not_called()
            with self.assertRaises(ValueError):
                selection.request_switch(self.root, 'cyrene', 'c' * 32)
            with self.assertRaises(ValueError):
                selection.request_switch(self.root, 'cyrene', 'b' * 32)

    def test_failed_process_launch_does_not_leave_pending_or_change_engine(self):
        with patch.object(selection.subprocess, 'Popen', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                selection.request_switch(self.root, 'yachiyo')
        self.assertEqual(selection.selection_status(self.root)['phase'], 'failed')
        self.assertEqual(json.loads((self.root / 'engine.json').read_text()), CYRENE)

    def test_switch_drains_before_loading_and_preserves_stopped_state(self):
        for desired in (True, False):
            with self.subTest(desired=desired):
                installed(self.root)
                request_control(self.root, desired)
                with patch.object(selection.subprocess, 'Popen'):
                    request = selection.request_switch(self.root, 'yachiyo')
                control, operations = self.controller()
                with patch.dict(sys.modules, {'pc_voice_control': control}):
                    selection.run_switch(self.root, request['id'])
                self.assertEqual(operations, [('stop', 'cyrene')] + ([('start', 'yachiyo')] if desired else []))
                self.assertEqual(selection.selection_status(self.root)['phase'], 'completed')
                self.assertEqual(read_control(self.root)['desired'], desired)
                self.assertIn('yachiyo.yaml', load_engine(self.root)['command'][-1])

    def test_new_model_failure_restores_old_config_and_readiness(self):
        request = self.request()
        control, operations = self.controller(fail=True)
        with patch.dict(sys.modules, {'pc_voice_control': control}):
            selection.run_switch(self.root, request['id'])
        self.assertEqual(selection.selection_status(self.root)['phase'], 'failed')
        self.assertEqual(json.loads((self.root / 'engine.json').read_text()), CYRENE)
        self.assertEqual(operations[-1], ('start', 'cyrene'))

    def test_owner_stop_after_queue_cancels_without_automatic_restart(self):
        request = self.request()
        request_control(self.root, False)
        control, operations = self.controller()
        with patch.dict(sys.modules, {'pc_voice_control': control}):
            selection.run_switch(self.root, request['id'])
        self.assertEqual(selection.selection_status(self.root)['phase'], 'cancelled')
        self.assertEqual(operations, [])
        self.assertFalse(read_control(self.root)['desired'])


class BridgeSelectionTests(unittest.TestCase):
    def test_switch_requires_auth_and_only_two_ids_no_paths_or_parameters(self):
        fixture = pc.AdapterTests(); fixture.setUp(); installed(fixture.directory)
        with TestClient(pc.bridge.make_app(fixture.path, transport=fixture.transport)) as client:
            body = {'voice_id': 'yachiyo', 'request_id': 'b' * 32}
            self.assertEqual(client.post('/v1/voices/switch', json=body).status_code, 401)
            with patch.object(pc.bridge, 'request_switch', return_value={**body, 'phase': 'queued'}) as switch:
                self.assertEqual(client.post('/v1/voices/switch', headers=fixture.headers, json=body).status_code, 200)
                for extra in ({'voice_id': '../model'}, {'engine': 'other'}, {'path': 'arbitrary'}):
                    self.assertEqual(client.post('/v1/voices/switch', headers=fixture.headers,
                        json={**body, **extra}).status_code, 422)
                self.assertEqual(switch.call_count, 1)

    def test_inactive_preset_is_never_synthesized_and_switch_stops_new_requests(self):
        fixture = pc.AdapterTests(); fixture.setUp()
        installed(fixture.directory)
        settings = json.loads(fixture.path.read_text())
        settings['presets']['yachiyo'] = dict(settings['presets']['cyrene'])
        fixture.path.write_text(json.dumps(settings))
        with TestClient(pc.bridge.make_app(fixture.path, transport=fixture.transport)) as client:
            health = client.get('/health', headers=fixture.headers).json()
            self.assertEqual(health['active_voice'], 'cyrene')
            self.assertEqual(health['presets'], ['cyrene'])
            self.assertEqual(len(health['voices']), 2)
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers,
                json={**fixture.body, 'preset': 'yachiyo'}).status_code, 422)
            write_json(fixture.directory / 'voice-selection.json', {'phase': 'stopping'})
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers, json=fixture.body).status_code, 503)
            self.assertFalse(any(call.method == 'POST' for call in fixture.calls))


class CloudAdoptionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AudioTests.asyncSetUp
    asyncTearDown = fixtures.AudioTests.asyncTearDown
    configure = fixtures.AudioTests.configure
    local_config = pc.LocalPipelineTests.local_config

    def prepare(self):
        self.local_config()
        revision, settings = self.service.settings.current()
        values = settings.model_dump()
        values['presets']['yachiyo'] = {**values['presets']['pc'], 'name': '八千代', 'voice': 'yachiyo'}
        values['ai_preset_ids'] = ['pc', 'yachiyo']
        self.service.settings.save(revision, values)
        self.provider.pc_status = AsyncMock(return_value={'connected': True, 'active_voice': 'yachiyo', 'backend': 'gpt-sovits'})

    async def test_local_selection_adopts_preset_and_model_preserving_permissions_and_secret(self):
        self.prepare()
        before = self.service.settings.snapshot()
        key_before = self.service.settings.capture('speech')['key']
        status = await self.service.pc_status()
        after = self.service.settings.snapshot()
        self.assertTrue(status['preset_synchronized'])
        self.assertEqual(after['settings']['default_preset'], 'yachiyo')
        self.assertEqual(after['settings']['providers']['pc']['model'], 'yachiyo-v2proplus')
        for name in ('allowed_chat_ids', 'voice_mode', 'enabled', 'ai_voice_dynamics_enabled'):
            self.assertEqual(after['settings'][name], before['settings'][name])
        self.assertEqual(self.service.settings.capture('speech')['key'], key_before)
        await self.service.pc_status()
        self.assertEqual(self.service.settings.snapshot()['revision'], after['revision'])

    async def test_gsv_lite_voice_is_adopted_as_a_new_preset_without_replacing_settings(self):
        self.local_config()
        self.provider.pc_status = AsyncMock(return_value={
            'connected': True, 'active_voice': 'xiaofei', 'backend': 'gsv-lite'})
        before = self.service.settings.snapshot()
        key_before = self.service.settings.capture('speech')['key']
        status = await self.service.pc_status()
        after = self.service.settings.snapshot()
        self.assertTrue(status['preset_synchronized'])
        self.assertEqual(after['settings']['default_preset'], 'xiaofei')
        self.assertEqual(after['settings']['presets']['xiaofei']['voice'], 'xiaofei')
        self.assertEqual(after['settings']['presets']['xiaofei']['name'], '小菲')
        self.assertEqual(after['settings']['providers']['pc']['model'], 'xiaofei-gsv-lite-v2')
        for name in ('allowed_chat_ids', 'voice_mode', 'enabled', 'ai_voice_dynamics_enabled'):
            self.assertEqual(after['settings'][name], before['settings'][name])
        self.assertEqual(self.service.settings.capture('speech')['key'], key_before)

    async def test_offline_or_conflicting_update_does_not_overwrite_owner_settings(self):
        self.prepare()
        before = self.service.settings.snapshot()
        self.provider.pc_status.return_value['connected'] = False
        await self.service.pc_status()
        self.assertEqual(self.service.settings.snapshot(), before)
        self.provider.pc_status.return_value['connected'] = True
        with patch.object(self.service.settings, 'save', side_effect=ValueError('revision conflict')):
            self.assertTrue((await self.service.pc_status())['preset_sync_pending'])
        self.assertEqual(self.service.settings.snapshot(), before)

    async def test_tool_advertises_only_ready_voice_presets(self):
        self.prepare()
        await self.service.pc_status()
        self.provider.pc_ready = lambda: True
        self.provider.pc_presets = lambda: ['yachiyo']
        tools = self.service.tools.schemas(SimpleNamespace(api_style='openai',stream_enabled=False,chat_with_tools=True),self.event)
        speech = next(row['function'] for row in tools if row['function']['name'] == 'send_voice')
        self.assertEqual(speech['parameters']['properties']['voice_preset_id']['enum'], ['yachiyo'])

    async def test_tool_language_choices_follow_active_local_engine(self):
        self.prepare()
        await self.service.pc_status()
        self.provider.pc_ready = lambda: True
        self.provider.pc_presets = lambda: ['yachiyo']
        self.provider.pc_languages = lambda: ['auto', 'zh', 'ja']
        tools = self.service.tools.schemas(SimpleNamespace(api_style='openai', stream_enabled=False,
            chat_with_tools=True), self.event)
        speech = next(row['function'] for row in tools if row['function']['name'] == 'send_voice')
        self.assertEqual(speech['parameters']['properties']['language']['enum'], ['auto', 'zh', 'ja'])


if __name__ == '__main__':
    unittest.main()

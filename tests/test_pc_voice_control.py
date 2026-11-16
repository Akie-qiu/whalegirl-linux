"""Local lifecycle tests: no model, SSH process or production state is touched."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from pc_voice_common import ModelStartupGuard, read_control, request_control, write_json


def folder():
    root = Path(__file__).resolve().parents[1] / 'test-runs'
    root.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='voice-control-', dir=root))


class StateTests(unittest.TestCase):
    def test_repeated_model_exit_is_bounded_until_explicit_start(self):
        guard = ModelStartupGuard('first-start')
        for _ in range(2):
            guard.exited(1)
            self.assertFalse(guard.blocked)
        guard.exited(1)
        self.assertTrue(guard.blocked)
        self.assertIn('3 次', guard.error)
        guard.control('first-start')
        self.assertTrue(guard.blocked)
        guard.control('explicit-retry')
        self.assertFalse(guard.blocked)
        self.assertEqual(guard.failures, 0)

    def test_verified_readiness_resets_consecutive_failures(self):
        guard = ModelStartupGuard('owner-start')
        guard.exited(1)
        guard.exited(1)
        guard.ready()
        guard.exited(2)
        self.assertFalse(guard.blocked)
        self.assertEqual(guard.failures, 1)

    def test_windows_sharing_collision_retries_atomic_state_write(self):
        root = folder()
        target = root / 'status.json'
        write_json(target, {'old': True})
        import os
        original = os.replace
        calls = []
        def replace(source, destination):
            calls.append(True)
            if len(calls) < 3:
                self.assertEqual(json.loads(target.read_text()), {'old': True})
                raise PermissionError('synthetic sharing violation')
            return original(source, destination)
        with patch('pc_voice_common.os.replace', side_effect=replace), patch('pc_voice_common.time.sleep'):
            write_json(target, {'new': True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(target.read_text()), {'new': True})

    def test_persistent_state_lock_is_bounded_and_keeps_last_complete_state(self):
        root = folder()
        target = root / 'status.json'
        write_json(target, {'old': True})
        with patch('pc_voice_common.os.replace', side_effect=PermissionError('locked')) as replace, \
                patch('pc_voice_common.time.sleep'), self.assertRaises(PermissionError):
            write_json(target, {'new': True})
        self.assertEqual(replace.call_count, 21)
        self.assertEqual(json.loads(target.read_text()), {'old': True})

    def test_manual_stop_survives_restart_and_invalid_state_fails_closed(self):
        root = folder()
        request_control(root, False)
        self.assertFalse(read_control(root)['desired'])
        request_control(root, True)
        self.assertTrue(read_control(root)['desired'])
        (root / 'service-control.json').write_text('{broken')
        self.assertFalse(read_control(root)['desired'])


@unittest.skipUnless(importlib.util.find_spec('psutil'), 'Windows worker environment provides psutil')
class ControlTests(unittest.TestCase):
    def setUp(self):
        import pc_voice_control
        self.control = pc_voice_control
        self.root = folder()

    def test_opening_or_reading_status_never_starts_worker(self):
        with patch.object(self.control, 'worker_live', return_value=False), \
                patch.object(self.control.subprocess, 'Popen') as spawn:
            result = self.control.snapshot(self.root)
        spawn.assert_not_called()
        self.assertFalse(result['worker_running'])
        self.assertFalse(result['cloud_confirmed'])

    def test_explicit_start_and_stop_persist_intent_without_duplicate_worker(self):
        with patch.object(self.control, 'worker_live', return_value=True), \
                patch.object(self.control.subprocess, 'Popen') as spawn:
            self.control.action(self.root, 'start')
            self.assertTrue(read_control(self.root)['desired'])
            self.control.action(self.root, 'stop')
        self.assertFalse(read_control(self.root)['desired'])
        spawn.assert_not_called()

    def test_stale_ack_is_not_presented_as_cloud_connected(self):
        import io
        import time
        (self.root / 'bridge.json').write_text(json.dumps({'token': 'test-only'}))
        response = io.BytesIO(json.dumps({'ready': True, 'cloud_accepted': True,
            'cloud_ack_at': time.time() - 60}).encode())
        with patch.object(self.control, 'worker_live', return_value=True), \
                patch.object(self.control.urllib.request, 'urlopen', return_value=response):
            result = self.control.snapshot(self.root)
        self.assertTrue(result['model_ready'])
        self.assertFalse(result['cloud_confirmed'])

    def test_stop_hides_previous_connection_immediately(self):
        import io
        import time
        request_control(self.root, False)
        (self.root / 'bridge.json').write_text(json.dumps({'token': 'test-only'}))
        response = io.BytesIO(json.dumps({'ready': False, 'stopping': True,
            'cloud_accepted': True, 'cloud_ack_at': time.time()}).encode())
        with patch.object(self.control, 'worker_live', return_value=True), \
                patch.object(self.control.urllib.request, 'urlopen', return_value=response):
            result = self.control.snapshot(self.root)
        self.assertFalse(result['cloud_confirmed'])

    def test_startup_failure_is_visible_without_claiming_model_readiness(self):
        write_json(self.root / 'worker-status.json', {'phase': 'failed', 'engine': 'gsv-lite',
            'error': '模型连续启动失败 3 次，已暂停重试'})
        with patch.object(self.control, 'worker_live', return_value=True):
            result = self.control.snapshot(self.root)
        self.assertEqual(result['phase'], 'failed')
        self.assertEqual(result['engine'], 'gsv-lite')
        self.assertIn('暂停重试', result['error'])
        self.assertFalse(result['model_ready'])


if __name__ == '__main__':
    unittest.main()

"""Regressions for archive/live-reader races and delayed native commits."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.runtime import Runtime, timestamp
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class Gateway:
    login = {'state': 'idle'}

    def __init__(self):
        self.rows = []
        self.calls = []

    async def status(self):
        return {'status': 'logged_in', 'loggedInUser': 'account'}

    async def messages(self, chat, limit=100, **cursor):
        self.calls.append(cursor)
        rows = sorted(self.rows, key=lambda m: (timestamp(m['timestamp']), m['localId']))
        if 'after_time' in cursor:
            boundary = (cursor['after_time'], cursor.get('after_id', 0))
            return [r for r in rows if (timestamp(r['timestamp']), r['localId']) > boundary][:limit]
        return rows[-limit:]

    async def close(self):
        pass


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='poll-recovery-', dir=runs))
        self.config = Config(self.folder)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=['test@chatroom']), 'test-only-placeholder')
        self.store = Store(self.folder / 'state.sqlite3')
        self.gateway = Gateway()
        self.runtime = Runtime(self.config, self.store, self.gateway, ROOT / 'styles')
        self.now = int(time.time())

    async def asyncTearDown(self):
        await self.runtime.close()

    def raw(self, identity, age=0):
        return {'serverId': identity, 'localId': identity, 'type': 1, 'sender': 'tester',
                'isSelf': False, 'content': '鲸鱼娘你好', 'isMentioned': True,
                'timestamp': self.now - age}

    def event(self, raw):
        return Runtime.event('account', 'test@chatroom', raw)

    async def seed(self):
        self.gateway.rows = [self.raw(1, 100)]
        await self.runtime.poll_once()

    async def test_archive_cannot_advance_live_cursor(self):
        await self.seed()
        newest = self.raw(3)
        self.store.observe([self.event(newest)])
        self.gateway.rows.extend([self.raw(2, 1), newest])
        await self.runtime.poll_once()
        self.assertEqual(self.store.stats().get('queued'), 2)
        self.assertEqual(self.store.poll_cursor('account', 'test@chatroom'), (self.now, 3))

    async def test_delayed_older_row_is_reconciled_once(self):
        await self.seed()
        self.gateway.rows.append(self.raw(3))
        await self.runtime.poll_once()
        self.gateway.rows.append(self.raw(2, 1))
        self.runtime.last_reconcile.clear()
        await self.runtime.poll_once()
        await self.runtime.poll_once()
        self.assertEqual(self.store.stats().get('queued'), 2)
        self.assertEqual(self.store.poll_cursor('account', 'test@chatroom'), (self.now, 3))

    async def test_delayed_history_is_logged_without_reply(self):
        await self.seed()
        self.gateway.rows.append(self.raw(3))
        await self.runtime.poll_once()
        self.gateway.rows.append(self.raw(2, 300))
        self.runtime.last_reconcile.clear()
        await self.runtime.poll_once()
        event = self.store.get(self.event(self.raw(2, 300))['key'])
        self.assertEqual(event['state'], 'ignored')
        self.assertIn('不补发', event['error'])
        self.assertEqual(self.store.stats().get('queued'), 1)

    async def test_forward_activity_does_not_postpone_tail_reconciliation(self):
        await self.seed()
        self.runtime.last_reconcile[('account', 'test@chatroom')] = time.monotonic() - 14
        previous = self.runtime.last_reconcile.copy()
        self.gateway.rows.append(self.raw(2))
        await self.runtime.poll_once()
        self.assertEqual(previous, self.runtime.last_reconcile)

    async def test_receipt_log_can_include_untriggered_messages(self):
        await self.seed()
        self.gateway.rows.append({**self.raw(2), 'isSelf': True})
        await self.runtime.poll_once()
        self.assertFalse(self.store.recent())
        rows = self.store.recent(include_ignored=True)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(r['state'] == 'ignored' and r['error'] for r in rows))

    async def test_late_sender_metadata_can_wake_previously_incomplete_message(self):
        await self.seed()
        self.gateway.rows.append({**self.raw(2), 'sender': '', 'isSelf': None})
        await self.runtime.poll_once()
        self.assertEqual(self.store.get(self.event(self.raw(2))['key'])['state'], 'ignored')
        self.gateway.rows[-1] = self.raw(2)
        self.runtime.last_reconcile.clear()
        await self.runtime.poll_once()
        self.assertEqual(self.store.stats().get('queued'), 1)
        self.store.update(self.event(self.raw(2))['key'], 'sent', reply='already delivered')
        self.gateway.rows[-1]['content'] += '（同步完成）'
        self.runtime.last_reconcile.clear()
        await self.runtime.poll_once()
        self.assertEqual(self.store.stats().get('sent'), 1)
        self.assertIsNone(self.store.queue())

    async def test_native_failure_does_not_fall_back_to_partial_ui(self):
        gateway = AgentGateway('http://127.0.0.1', 'test')
        gateway.ui_jobs_directory = self.folder
        gateway.native_request = AsyncMock(side_effect=OSError('reader unavailable'))
        gateway.client.get = AsyncMock()
        try:
            with self.assertRaises(OSError):
                await gateway.messages('test@chatroom', after_time=1, after_id=1)
            gateway.client.get.assert_not_awaited()
        finally:
            await gateway.close()


class GuardTests(unittest.TestCase):
    def test_only_exact_gateway_sigkill_of_pinned_client_is_guarded(self):
        spec = importlib.util.spec_from_file_location('health_guard', ROOT/'deploy/health_restart_guard.py')
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        def resolve(path, **kwargs):
            return Path('/opt/agent-server/agent-server') if path.parts[-2:] == ('10', 'exe') else Path('/opt/wechat/wechat')
        def read(path, **kwargs):
            return 'wechat' if path.name == 'comm' else '20 (wechat) ' + ' '.join(['1']*20)
        with patch.object(Path, 'resolve', resolve), patch.object(Path, 'read_text', read):
            self.assertEqual(guard.guarded_target(['-9', '20'], parent=10)['pid'], 20)
            self.assertIsNone(guard.guarded_target(['-TERM', '20'], parent=10))
            self.assertIsNone(guard.guarded_target(['-9', '20'], parent=11))
            self.assertIsNone(guard.guarded_target(['-9', '1'], parent=10))

    def test_upstream_su_launch_ancestor_requires_live_wechat_descendant(self):
        spec = importlib.util.spec_from_file_location('health_guard_ancestor', ROOT/'deploy/health_restart_guard.py')
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        def resolve(path, **kwargs):
            return Path({'10':'/opt/agent-server/agent-server', '20':'/usr/bin/su',
                         '21':'/opt/wechat/wechat'}.get(path.parts[-2], '/usr/bin/other'))
        def read(path, **kwargs):
            if path.name == 'comm': return 'su'
            if path.name == 'children': return '21'
            return '20 (su) ' + ' '.join(['1']*20)
        with patch.object(Path,'resolve',resolve), patch.object(Path,'read_text',read), \
             patch.object(Path,'read_bytes',return_value=b'su\0wechat\0-c\0/usr/bin/wechat\0'):
            self.assertEqual(guard.guarded_target(['-9','20'],parent=10)['wechat_pid'],21)
            with patch.object(Path,'read_bytes',return_value=b'su\0wechat\0-c\0exec /usr/bin/wechat\0'):
                self.assertEqual(guard.guarded_target(['-9','20'],parent=10)['wechat_pid'],21)
            with patch.object(Path,'read_bytes',return_value=b'su\0unrelated\0'):
                self.assertIsNone(guard.guarded_target(['-9','20'],parent=10))


if __name__ == '__main__':
    unittest.main()

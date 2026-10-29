"""Message send times must reach the model in the current turn as well as history."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whalegirl_linux.context import build_context
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine

ROOT = Path(__file__).resolve().parents[1]


class MessageTimeContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix='message-time-', dir=runs))
        self.store = Store(self.path / 'state.sqlite3')
        self.now = datetime(2026, 10, 28, 14, 30, 11, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.settings = Settings(account_id='account', allowed_chat_ids=['private'], vision_enabled=False)

    def tearDown(self):
        self.store.close()

    def event(self, identity, offset, text):
        row = dict(key=f'account/private/{identity}', account='account', chat='private',
                   sender='测试用户', sender_id='person', kind=1, is_self=0,
                   local_id=identity, timestamp=self.now-offset, content=text,
                   trigger='direct', payload_json='{}')
        self.store.observe([row])
        return row

    async def test_merged_current_messages_keep_separate_send_times(self):
        first = self.event(1, 10, '我先说一下')
        second = self.event(2, 4, '还有一个条件')
        with patch('whalegirl_linux.context.time.time', return_value=self.now):
            messages, images, _ = await build_context(
                self.store, None, self.settings, first, [first, second], {})
        prompt = messages[0]['content']
        self.assertIn('[2026-10-28 14:30:01｜测试用户｜账号ID=person] 我先说一下', prompt)
        self.assertIn('[2026-10-28 14:30:07｜测试用户｜账号ID=person] 还有一个条件', prompt)
        with patch('whalegirl_linux.time_context.time.time', return_value=self.now):
            payload = AIEngine('https://example.test/v1', 'test')._build_payload(messages, '', None)
        self.assertIn('【服务器当前时间】2026-10-28 14:30:11 周三 北京时间',
                      payload['messages'][-1]['content'])
        self.assertFalse(images)

    async def test_history_keeps_its_original_time_alongside_current_message(self):
        self.event(1, 20, '这个条件是茶里面不加糖')
        current = self.event(2, 3, '这个条件里的茶可以换成奶茶吗')
        with patch('whalegirl_linux.context.time.time', return_value=self.now):
            messages, _, _ = await build_context(
                self.store, None, self.settings, current, [current], {'private': '测试会话'})
        prompt = messages[0]['content']
        self.assertIn('[10-28 14:29:51｜测试会话｜测试用户｜账号ID=person｜消息1]', prompt)
        self.assertIn('[2026-10-28 14:30:08｜测试用户｜账号ID=person]', prompt)


if __name__ == '__main__':
    unittest.main()

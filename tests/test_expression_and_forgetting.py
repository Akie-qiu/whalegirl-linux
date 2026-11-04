"""Previous-release expression controls and administrator source forgetting."""
import json
from pathlib import Path
import tempfile
import time
import unittest
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app
from whalegirl_linux.conversation import expression_preferences_prompt, habit_static_prompt, plan_actions
from whalegirl_linux.settings import Settings, Config
from whalegirl_linux.store import Store
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.runtime import Runtime
from test_chat_habits import Engine, Gateway

ROOT = Path(__file__).resolve().parents[1]


def directory():
    runs = ROOT / 'test-runs'; runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='expression-forgetting-', dir=runs))


class ExpressionTests(unittest.TestCase):
    def test_controls_roundtrip_through_actual_console_api(self):
        path = directory()
        app = create_app(path, gateway=Gateway(), engine_factory=Engine, run_background=False)
        with TestClient(app) as client:
            config = app.state.config
            headers = {'Authorization': 'Bearer ' + config.admin_token}
            current = config.settings.model_dump()
            current.update(girlish_tone='bright', chat_short_replies_enabled=True,
                           chat_short_reply_sentences=1, chat_short_reply_chars=24)
            response = client.post('/api/config', headers=headers, json={'settings': current})
            self.assertEqual(response.status_code, 200, response.text)
            saved = Config(path).settings
            self.assertEqual((saved.girlish_tone, saved.chat_short_reply_sentences, saved.chat_short_reply_chars), ('bright', 1, 24))
            self.assertEqual(saved.system_prompt_override, current['system_prompt_override'])
            self.assertEqual(saved.max_tokens, current['max_tokens'])
            current['chat_short_reply_sentences'] = 0
            self.assertEqual(client.post('/api/config', headers=headers, json={'settings': current}).status_code, 422)

    def test_same_expression_preferences_reach_both_old_participation_paths(self):
        for autonomy in (False, True):
            settings = Settings(autonomy_enabled=autonomy, girlish_tone='soft', chat_short_reply_sentences=1,
                                chat_short_reply_chars=24, reply_length='detailed')
            prompt = habit_static_prompt(settings)
            self.assertIn('柔软细腻', prompt)
            self.assertIn('不超过1句、合计约24字', prompt)
            self.assertIn('不是截断限制', prompt)
            self.assertNotIn('默认句长', prompt)
        settings.chat_short_replies_enabled = False
        self.assertNotIn('合计约24字', habit_static_prompt(settings))
        self.assertIn('默认句长', habit_static_prompt(settings))

    def test_explicit_detailed_answer_is_not_cut_to_chat_target(self):
        settings = Settings(chat_short_reply_chars=10)
        text = '第一步先保存文件，第二步检查网络连接，第三步重新运行并记录具体错误信息；这些步骤需要完整说明。'
        actions = plan_actions({'actions': [{'type': 'text', 'text': text}]}, {}, settings)
        self.assertIn('第三步重新运行', actions[0]['text'])
        self.assertGreater(len(actions[0]['text']), 10)
        self.assertEqual(expression_preferences_prompt(Settings(girlish_tone='off', chat_short_replies_enabled=False)), '')


class ForgottenSourcesTests(unittest.TestCase):
    def setUp(self):
        self.path = directory(); self.database = self.path / 'state.sqlite3'
        self.store = Store(self.database); self.book = MemoryBook(self.store); self.hub = self.book.hub
        self.cutoff = time.time()
        self.old = self.event('old', 'test@chatroom', self.cutoff - 100)
        self.other = self.event('other', 'other@chatroom', self.cutoff - 100)
        self.new = self.event('new', 'test@chatroom', self.cutoff + 10)
        self.store.observe([self.old, self.other, self.new])
        with self.store.db:
            self.store.db.execute('INSERT INTO memory_hub_meta VALUES(?,?)', ('forget_before:account:test@chatroom', str(self.cutoff)))

    def event(self, key, chat, stamp):
        return dict(key='account/' + chat + '/' + key, account='account', chat=chat, sender='测试者',
                    sender_id='person', kind=1, is_self=0, local_id=0, content='我喜欢猫', timestamp=stamp, payload_json='{}')

    def tearDown(self):
        self.store.close()

    def test_raw_messages_survive_but_old_context_and_raw_recall_do_not(self):
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM message_log').fetchone()[0], 3)
        self.assertEqual([r['key'] for r in self.store.conversation('account', 'test@chatroom')], [self.new['key']])
        self.assertEqual([r['key'] for r in self.store.conversation('account', 'other@chatroom')], [self.other['key']])
        self.assertIn(self.old['key'], self.book.suppressed_sources('account', ['test@chatroom']))
        recall = self.store.memory('account', ['test@chatroom'], 'test@chatroom', 'person', '喜欢猫')
        self.assertNotIn(self.old['key'], [r['key'] for r in recall])

    def test_old_source_cannot_be_relearned_as_another_memory_kind(self):
        for kind in ('fact', 'chat_style', 'shared_moment'):
            self.assertTrue(self.hub.blocked('account', 'test@chatroom', 'person', '新的概括', self.old['key'], kind))
        with self.assertRaisesRegex(ValueError, '清除|停止使用'):
            self.book.change('account', ['test@chatroom'], dict(chat='test@chatroom', subject='person', kind='fact',
                content='喜欢猫', source_key=self.old['key']), actor='model', allowed_sources=[self.old['key']])
        self.assertFalse(self.hub.forgotten_source('account', self.other['key']))
        self.assertFalse(self.hub.forgotten_source('account', self.new['key']))
        added = self.book.change('account', ['test@chatroom'], dict(chat='test@chatroom', subject='person', kind='fact',
            content='喜欢猫', source_key=self.new['key']), actor='model', allowed_sources=[self.new['key']])
        self.assertEqual(added['status'], 'active')

    def test_cutoff_survives_restart_and_late_archive_imports(self):
        self.store.close(); self.store = Store(self.database); self.book = MemoryBook(self.store); self.hub = self.book.hub
        late = self.event('late-import', 'test@chatroom', self.cutoff - 50)
        self.store.observe([late])
        self.assertTrue(self.hub.forgotten_source('account', late['key']))
        self.assertEqual([r['key'] for r in self.store.conversation('account', 'test@chatroom')], [self.new['key']])


class ScopedCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_cleanup_is_scoped_and_preserves_raw_messages(self):
        path = directory(); config = Config(path)
        config.save(Settings(account_id='account', allowed_chat_ids=['test@chatroom', 'other@chatroom']))
        store = Store(path / 'state.sqlite3')
        runtime = Runtime(config, store, Gateway(), ROOT / 'styles', engine_factory=Engine)
        group = 'test@chatroom'; stamp = time.time() - 10
        for number, chat in enumerate((group, 'other@chatroom'), 1):
            source = dict(key='account/' + chat + '/' + str(number), account='account', chat=chat, sender_id='person',
                          sender='测试者', is_self=0, kind=1, local_id=number, content='我喜欢猫', timestamp=stamp, payload_json='{}')
            store.observe([source])
            runtime.memory_book.change('account', config.settings.allowed_chat_ids,
                dict(chat=chat, subject='person', kind='fact', content='喜欢猫', source_key=source['key']))
        await runtime.close()
        spec = importlib.util.spec_from_file_location('cleanup_under_test', ROOT / 'deploy/forget_test_chat.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        backups = path / 'private-backups'; backups.mkdir()
        with patch.object(module, 'DATA', path), patch.object(module, 'GROUP', group), patch.object(module, 'BACKUPS', backups), \
             patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=3)), \
             patch.object(module.os, 'chown', create=True):
            preview = module.run()
            self.assertEqual(preview['counts']['memories'], 1)
            with self.assertRaisesRegex(ValueError, 'scope changed'):
                module.run(apply=True, expected='wrong', output=backups / 'forget-test-chat-wrong')
            report = module.run(apply=True, expected=preview['plan_sha256'], output=backups / 'forget-test-chat-actual')
            self.assertTrue(report['preserve_raw_chats'])
        store = Store(path / 'state.sqlite3'); book = MemoryBook(store)
        try:
            self.assertEqual(store.db.execute('SELECT count(*) FROM message_log').fetchone()[0], 2)
            self.assertEqual(store.db.execute('SELECT count(*) FROM memories WHERE chat=?', (group,)).fetchone()[0], 0)
            self.assertEqual(store.db.execute('SELECT count(*) FROM memories WHERE chat=?', ('other@chatroom',)).fetchone()[0], 1)
            self.assertEqual(store.conversation('account', group), [])
            self.assertTrue(store.conversation('account', 'other@chatroom'))
            self.assertTrue(book.hub.forgotten_source('account', 'account/' + group + '/1'))
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()

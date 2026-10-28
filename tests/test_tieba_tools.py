"""Source paging, live tool delivery, actual-use tracking and retention boundaries."""
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.resources import native_pressure
from test_chat_habits import Gateway, Engine
import test_tieba as fixtures
from test_tieba_archive import source


class TiebaToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = fixtures.directory()
        self.config = Config(self.root)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=['a@chatroom'],
            tieba_enabled=True, tieba_share_chat_ids=['a@chatroom'], tieba_start_hour=0, tieba_end_hour=0,
            debounce_single_seconds=0, debounce_burst_seconds=0, debounce_max_seconds=0,
            min_send_interval=3, chat_cooldown=5, stickers_enabled=False, images_chat_enabled=False), 'test-key')
        self.store = Store(self.root/'state.sqlite3')
        self.gateway = Gateway()
        self.gateway.status_for_send = self.gateway.status
        self.r = Runtime(self.config, self.store, self.gateway, fixtures.ROOT/'styles', engine_factory=Engine)
        self.b, self.t = self.r.tieba, self.r.tieba.tools
        self.thread = source()
        self.thread['text'] = self.thread['posts'][0]['text'] = '开场\n'+('保存全部正文。'*800)+'\n摸鱼是物种优势，这里是尾部重要说明'
        self.identity = self.b.archives.add(self.thread, '有趣的原文')
        await self.b.archives.process(self.identity)
        self.b.learn('account', self.thread, {'memes': [{'phrase': '摸鱼是物种优势', 'meaning': '用物种特点给摸鱼找借口',
            'scene': '工作偷闲时互相调侃', 'background': '讨论工作偷闲的反转', 'examples': '这叫发挥物种优势',
            'avoid': '对方认真难过时', 'aliases': '偷懒 工作划水'}]})

    async def asyncTearDown(self):
        await self.r.close()

    def event(self, mid=1, content='今天又在摸鱼'):
        return {'key': f'account/a@chatroom/{mid}', 'account': 'account', 'chat': 'a@chatroom',
                'sender': '测试群友', 'sender_id': 'person', 'kind': 1, 'is_self': 0, 'local_id': mid,
                'content': content, 'timestamp': time.time(), 'trigger': 'direct', 'payload_json': '{}'}

    async def test_full_source_and_alias_search_do_not_touch_usage_until_read(self):
        result = self.t.search('工作划水')
        self.assertEqual(result['posts'][0]['tid'], '123')
        self.assertIn('摸鱼是物种优势', self.t.search('尾部重要说明')['posts'][0]['excerpt'])
        self.assertEqual(self.store.db.execute('SELECT uses FROM tieba_lifecycle').fetchone()[0], 0)
        result = self.t.read('123', offset=4500)
        self.assertIn('尾部重要说明', result['passages'][0]['text'])
        self.assertEqual(result['next_offset'], None)
        self.assertEqual(self.store.db.execute('SELECT uses FROM tieba_lifecycle').fetchone()[0], 1)

    async def test_requested_tools_read_then_share_through_existing_send_ledger(self):
        first = self.event()
        engine = Engine()
        learning = {'type': 'tool_learning', 'content': '先读原文再选择摘录'}
        engine.answers = [{'should_reply': True, 'actions': [], 'tieba_request': {'tool': 'read_tieba', 'tid': '123', 'offset': 4500}},
            {'should_reply': True, 'actions': [], 'tieba_request': {'tool': 'share_tieba', 'tid': '123',
             'excerpts': [{'pid': '11', 'text': '摸鱼是物种优势，这里是尾部重要说明'}], 'comment': '这下有正当理由了'}}]
        diagnostics = {}
        decision = await self.t.resolve(engine, [], self.t.static_prompt(),
            {'should_reply': True, 'tieba_request': {'tool': 'search_tieba', 'query': '工作划水'},
             'learning_proposals': [learning]},
            first, self.config.generation, diagnostics, 1000)
        self.assertEqual(engine.calls, 2)
        self.assertEqual(diagnostics['tool_rounds'], 2)
        self.assertEqual(decision['learning_proposals'], [learning])
        actions = self.t.attach_share(decision, first, [])
        self.store.insert(first, 'generating')
        await self.r.send_plan(first, [first], first, actions, {}, self.config.generation, {})
        self.assertEqual(self.b.snapshot()['shares'][0]['state'], 'sent')
        self.assertEqual(self.b.share_count('account'), 1)
        self.assertEqual(len(self.store.actions(first['key'])), 1)
        self.assertIn('尾部重要说明', self.gateway.sent[0][1])
        self.assertIn('原帖摘录', self.gateway.sent[0][1])
        self.assertIn('https://tieba.baidu.com/p/123', self.gateway.sent[0][1])

    async def test_normal_chat_has_no_extra_model_call_and_recent_use_is_penalized(self):
        first, engine = self.event(), Engine()
        decision = {'should_reply': True, 'actions': [{'type': 'text', 'text': '确实 在发挥物种优势'}]}
        unchanged = await self.t.resolve(engine, [], '', decision, first, self.config.generation, {}, 800)
        self.assertIs(unchanged, decision)
        self.assertEqual(engine.calls, 0)
        meme = self.t.memes()[0]
        diagnostics = {'meme_ids': [meme['id']]}
        before = self.t.rank_memes('工作划水', first['chat'])[0]['score']
        self.t.record_usage(first, {'tieba_used_meme_ids': [meme['id']]}, diagnostics, '')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tieba_meme_usage').fetchone()[0], 0)
        self.t.record_usage(first, {'tieba_used_meme_ids': [meme['id']]}, diagnostics, '确实 在发挥物种优势')
        after = self.t.rank_memes('工作划水', first['chat'])
        self.assertTrue(not after or after[0]['score'] < before)
        self.assertTrue(self.t.rank_memes('摸鱼是物种优势是什么意思', first['chat']))

    async def test_ordinary_chat_exposes_tieba_tool_but_not_saved_content(self):
        first = self.event(content='今天有点累')
        self.store.observe([first])
        self.store.insert(first)
        engine = await self.r.model()
        engine.answers = [{'should_reply': True, 'actions': [{'type': 'text', 'text': '辛苦啦'}]}]
        captured = []
        original_chat = engine.chat

        async def capture_chat(messages, **kwargs):
            captured.append((messages, kwargs))
            return await original_chat(messages, **kwargs)

        engine.chat = capture_chat
        await self.r.process_next()

        self.assertEqual(engine.calls, 1)
        self.assertEqual(len(captured), 1)
        user_content = json.dumps(captured[0][0], ensure_ascii=False)
        system_prompt = captured[0][1]['system_prompt']
        self.assertIn('search_tieba', system_prompt)
        self.assertNotIn('保存全部正文。', user_content)
        self.assertNotIn('摸鱼是物种优势', user_content)
        self.assertNotIn('摸鱼是物种优势', system_prompt)

    async def test_share_checks_actual_passages_and_authorized_switches(self):
        first = self.event()
        request = {'tid': '123', 'excerpts': [{'pid': '11', 'text': '不存在的编造正文'}]}
        self.assertEqual(self.t.validate_share(first, request)['code'], 'excerpt_not_in_source')
        request['excerpts'] = [{'pid': '不存在的楼层', 'text': '开场'}]
        self.assertFalse(self.t.validate_share(first, request)['ok'])
        self.assertEqual(self.t.validate_share({**first, 'chat': 'other'}, {'tid': '123'})['code'], 'chat_not_authorized')
        self.config.settings.tieba_share_enabled = False
        self.assertEqual(self.t.validate_share(first, {'tid': '123'})['code'], 'share_disabled')
        self.assertTrue(self.t.read('123')['ok'])
        self.config.settings.tieba_enabled = False
        self.assertFalse(self.t.read('123')['ok'])

    async def test_idle_retention_restore_pin_and_reviewed_file_boundary(self):
        old = time.time()-60*86400
        self.b.execute('UPDATE tieba_lifecycle SET created=?,last_used=0', (old,))
        now = time.time()
        self.b.retention.cleanup_once(now)
        self.assertFalse(self.b.retention.active('123'))
        self.assertFalse(self.t.read('123')['ok'])
        self.assertEqual(self.t.search('摸鱼')['posts'], [])
        self.b.retention.edit('123', 'restore')
        self.assertTrue(self.b.retention.active('123'))
        self.b.retention.edit('123', 'pin')
        self.assertEqual(self.b.retention.candidates(now+100*86400), [])
        self.b.retention.edit('123', 'unpin')
        self.b.execute('UPDATE tieba_lifecycle SET trashed_at=?,last_used=?', (now-8*86400, old))
        directory = self.b.archives.directory(self.b.archives.row(self.identity))
        unknown = directory/'never-delete-this.txt'
        unknown.write_text('unknown')
        self.b.retention.last_check = 0
        with patch.object(Path, 'unlink') as unlink, patch.object(Path, 'rmdir') as rmdir:
            self.b.retention.cleanup_once(now)
        unlink.assert_not_called()
        rmdir.assert_not_called()
        # Separate reviewed plan without actually removing any filesystem objects.
        with patch.object(Path, 'iterdir', return_value=iter([directory/'source.json', directory/'original.txt'])), \
                patch.object(Path, 'unlink') as unlink, patch.object(Path, 'rmdir') as rmdir:
            self.b.retention.last_check = 0
            self.b.retention.cleanup_once(now)
        self.assertEqual(unlink.call_count, 2)
        self.assertFalse(self.b.retention.active('123'))
        log = self.store.db.execute("SELECT plan_json FROM tieba_cleanup_log WHERE operation='purge_planned'").fetchone()
        self.assertEqual(len(json.loads(log[0])), 2)
        with self.assertRaises(ValueError):
            self.b.retention.edit('123', 'restore')

    async def test_live_topic_can_share_without_an_ordinary_reply_and_ignores_strict_group(self):
        first = self.event(content='我觉得摸鱼是物种优势')
        self.store.observe([first])
        self.r.account_id, self.r.gateway_state = 'account', {'status': 'logged_in'}
        self.config.settings.trigger_only_chat_ids = [first['chat']]
        self.b.observe({**first, 'trigger': 'natural'}, {'isSelf': False})
        self.assertFalse(self.b.context_pending)
        self.config.settings.trigger_only_chat_ids = []
        self.b.observe(first, {'isSelf': False})
        self.b.context_pending[first['chat']]['due'] = 0
        engine = Engine()
        engine.answers = [{'should_reply': True, 'tieba_request': {'tool': 'share_tieba', 'tid': '123',
            'excerpts': [{'pid': '11', 'text': '摸鱼是物种优势'}], 'comment': '说到这个我刚好刷到个帖'}}]
        with patch.object(self.b, 'new_engine', return_value=engine):
            self.assertTrue(await self.b.context_once())
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertEqual(self.b.snapshot()['shares'][0]['state'], 'sent')
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM history WHERE role='user'").fetchone()[0], 0)

    async def test_pressure_defers_background_work_but_does_not_disable_local_tools(self):
        folder = self.config.directory/'stability'
        folder.mkdir()
        (folder/'native-resources.json').write_text(json.dumps({'at': time.time(), 'pressure': True}))
        self.assertTrue(native_pressure(self.config))
        self.assertFalse(await self.b.scan())
        self.assertFalse(await self.b.archives.resume_once())
        self.assertTrue(self.t.search('摸鱼')['posts'])
        (folder/'native-resources.json').write_text(json.dumps({'at': time.time()-200, 'pressure': True}))
        self.assertFalse(native_pressure(self.config))

    async def test_regular_reply_worker_can_read_original_and_record_a_confirmed_meme_use(self):
        first = self.event(content='鲸鱼娘 解释一下摸鱼是物种优势')
        self.store.insert(first)
        self.store.observe([first])
        engine = await self.r.model()
        meme = self.t.memes()[0]
        engine.answers = [
            {'should_reply': True, 'actions': [], 'tieba_request': {'tool': 'search_tieba', 'query': '工作划水'}},
            {'should_reply': True, 'actions': [], 'tieba_request': {'tool': 'read_tieba', 'tid': '123', 'offset': 4500}},
            {'should_reply': True, 'actions': [{'type': 'text', 'text': '就是给自己偷懒找个正经理由呗'}],
             'tieba_used_meme_ids': [meme['id']]}]
        await self.r.process_next()
        self.assertEqual(self.store.get(first['key'])['state'], 'sent')
        self.assertEqual(engine.calls, 3)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tieba_meme_usage').fetchone()[0], 1)

    async def test_background_detail_enrichment_preserves_admin_text(self):
        self.b.execute('UPDATE tieba_meme_details SET background=?,examples=?,aliases=?', ('', '', ''))
        meme = self.t.memes()[0]
        engine = Engine()
        engine.answers = [{'memes': [{'id': meme['id'], 'background': '从工作偷闲聊到物种特点的反转',
                                      'examples': '发挥一下物种优势', 'avoid': '不要伤害别人', 'aliases': '摸鱼 划水'}]}]
        with patch.object(self.b, 'new_engine', return_value=engine):
            self.assertTrue(await self.t.enrich_once())
        row = self.t.memes()[0]
        self.assertIn('反转', row['background'])
        self.assertEqual(row['avoid'], '对方认真难过时')
        self.assertEqual(self.store.db.execute('SELECT uses FROM tieba_lifecycle').fetchone()[0], 0)

"""Focused regression tests for human-feel v10 upgrades (Modules 1-5)."""
import asyncio
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from whalegirl_linux.autonomy import AutonomyService
from whalegirl_linux.context import build_context
from whalegirl_linux.conversation import anti_formula_check
from whalegirl_linux.moments import MomentsService, _naturalize_moment_caption
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.stickers import StickerCatalog
from whalegirl_linux.store import Store
import test_chat_habits as habits
import test_moments as moments_fixture
import test_moments_world as world_fixture
import test_proactive as proactive_fixture

ROOT = Path(__file__).resolve().parents[1]


def _png_bytes(color=(90, 160, 240)):
    buf = BytesIO()
    Image.new('RGB', (40, 40), color).save(buf, format='PNG')
    return buf.getvalue()


class StickerFreshnessAndCooldownTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='sticker-v10-', dir=runs))
        self.addCleanup(lambda: None)
        self.store = Store(self.folder / 'state.sqlite3')
        self.addCleanup(self.store.close)
        self.catalog = StickerCatalog(self.folder / 'stickers')
        self.catalog.entries = {
            'a' * 32: '开心 哈哈 笑死',
            'b' * 32: '一般 路过',
            'c' * 32: '偷来的 可爱',
        }
        self.catalog.available = list(self.catalog.entries)

    def test_recent_cooldown_sticker_never_beats_non_recent_even_on_higher_score(self):
        history = {'a' * 32: {'count': 1, 'last': time.time() - 10, 'recent': True}}
        chosen = list(self.catalog.candidates('开心 哈哈 笑死', limit=2, history=history))
        self.assertEqual(set(chosen), {'b' * 32, 'c' * 32})
        self.assertNotIn('a' * 32, chosen)

        # Fallback only happens when fewer than limit non-recent candidates exist.
        fallback = list(self.catalog.candidates('开心 哈哈 笑死', limit=5, history=history))
        self.assertEqual(fallback[-1], 'a' * 32)

    def test_fresh_collected_sticker_gets_bonus_until_first_use(self):
        from whalegirl_linux.sticker_library import StickerLibrary
        svc = StickerLibrary(self.store, self.catalog)
        now = time.time()
        with self.store.lock, self.store.db:
            self.store.db.execute(
                '''INSERT INTO sticker_candidates
                   (account, md5, source_key, chat, state, profile_json, reason, error, created, updated, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                ('acc1', 'c' * 32, 'acc1/g@chatroom/1', 'g@chatroom', 'collected', '{}', '', '', now, now, 'chat'),
            )
        bonus = svc.fresh_collected_bonus('acc1', 'g@chatroom')
        self.assertEqual(bonus.get('c' * 32), 2)

        chosen = list(self.catalog.candidates('开心', limit=2, history={}, fresh_bonus=bonus))
        self.assertIn('c' * 32, chosen)

        # Once used (count >= 1), bonus stops boosting it ahead of exact match 'a'.
        history = {'c' * 32: {'count': 1, 'last': now - 999, 'recent': False}}
        after_use = list(self.catalog.candidates('开心 路过', limit=2, history=history, fresh_bonus=bonus))
        self.assertEqual(set(after_use), {'a' * 32, 'b' * 32})

    def test_inflight_and_uncertain_outbound_stickers_enter_cooldown_history(self):
        from whalegirl_linux.sticker_library import StickerLibrary
        svc = StickerLibrary(self.store, self.catalog)
        ev = {
            'key': 'acc1/g@chatroom/1', 'account': 'acc1', 'chat': 'g@chatroom',
            'sender': 'u', 'sender_id': 'u', 'content': 'hi', 'timestamp': time.time(),
        }
        self.store.insert(ev)
        aid = self.store.action_start(ev['key'], ev, 0, {'type': 'sticker', 'display': '[原生表情]'})
        self.store.action_finish(aid, 'sending', details={'md5': 'a' * 32})
        hist = self.store.sticker_selection_history('acc1', 'g@chatroom', 300)
        svc.apply_inflight_cooldown('acc1', 'g@chatroom', 300, hist)
        self.assertTrue(hist.get('a' * 32, {}).get('recent'))


class GentleSoftEndingAndOpenerTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='soft-v10-', dir=runs))
        self.store = Store(self.folder / 'state.sqlite3')
        self.addCleanup(self.store.close)

    def _add_self(self, idx, content):
        now = time.time() - (20 - idx)
        self.store.observe([{
            'key': f'acc1/p1/{idx}', 'account': 'acc1', 'chat': 'p1',
            'sender': '机器人', 'sender_id': 'acc1', 'kind': 1, 'is_self': 1,
            'local_id': idx, 'content': content, 'timestamp': now, 'payload_json': '{}',
        }])

    def test_gentle_mode_allows_two_wu_openers_and_nudges_at_three_or_soft_tail_streak(self):
        self._add_self(1, '呜 我知道了呀')
        self._add_self(2, '呜 刚才吓一跳呢')
        self._add_self(3, '好呀')
        res2 = anti_formula_check(self.store, 'acc1', 'p1', gentle=True)
        self.assertNotIn('呜', res2['banned_openers'])
        self.assertNotIn('连续几句都用「呀/嘛/啦/呢」收尾了', res2['prompt'])

        self._add_self(4, '呜 这也太难了嘛')
        self._add_self(5, '就是说哇')
        res3 = anti_formula_check(self.store, 'acc1', 'p1', gentle=True)
        self.assertIn('呜', res3['banned_openers'])
        self.assertIn('连续几句都用「呀/嘛/啦/呢」收尾了', res3['prompt'])


class ProactiveFreshnessAndBilibiliShareTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = proactive_fixture.ProactiveTests.asyncSetUp
    asyncTearDown = proactive_fixture.ProactiveTests.asyncTearDown
    message = proactive_fixture.ProactiveTests.message
    configure = proactive_fixture.ProactiveTests.configure

    async def test_initiative_context_sees_5min_image_and_warns_stale_chatter(self):
        now = time.time()
        photo = {
            **self.message(2),
            'kind': 3,
            'content': '[图片]',
            'timestamp': now - 300,
            'sender_id': 'u2',
        }
        self.store.observe([photo])
        msgs, keys, _ = await build_context(
            self.store,
            _ImgGateway(),
            Settings(vision_enabled=True),
            photo,
            [],
            {},
            initiative=True,
        )
        self.assertEqual(keys, [photo['key']])
        self.assertNotIn('话题新鲜度提醒', msgs[0]['content'][0]['text'])

        stale = {
            **self.message(3),
            'key': 'account/stale@chatroom/3',
            'chat': 'stale@chatroom',
            'kind': 1,
            'content': '散了散了',
            'timestamp': now - 900,
            'sender_id': 'u2',
        }
        self.store.observe([stale])
        msgs_stale, _, _ = await build_context(
            self.store, None, Settings(vision_enabled=False), stale, [], {}, initiative=True,
        )
        self.assertIn('话题新鲜度提醒', msgs_stale[0]['content'])

    async def test_proactive_offers_unshared_bilibili_and_records_share_once_per_day(self):
        self.configure(bilibili_enabled=True)
        now = time.time()
        with self.store.lock, self.store.db:
            self.store.db.execute(
                '''INSERT INTO growth_journals(id, run_id, account, role_id, topic, sources, body, created)
                   VALUES (?,?,?,?,?,?,?,?)''',
                ('j1', 'run1', 'account', self.runtime.memory_hub.role(), '克苏鲁实况', '[]', '主播被吓到尖叫特别好笑', now - 600),
            )
            self.store.db.execute(
                '''INSERT INTO bilibili_readings(id, account, role, bvid, cid, created, state, metadata, coverage, source_id, journal_id, enabled)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('r1', 'account', self.runtime.memory_hub.role(), 'BV1xx', '1', now - 600, 'done',
                 json.dumps({'title': '恐怖游戏一口气看完', 'author': '某UP'}, ensure_ascii=False),
                 '{}', '', 'j1', 1),
            )

        self.reply = {
            'should_reply': True,
            'reason': '分享刚在B站刷到的搞笑视频',
            'shared_bilibili_id': 'r1',
            'actions': [{'type': 'text', 'text': '刚看个克苏鲁实况差点把我笑岔气'}],
        }
        captured = []
        class InspectEngine(proactive_fixture.Engine):
            async def chat(inner, messages, **kwargs):
                captured.append(messages)
                return json.dumps(self.reply, ensure_ascii=False)
        self.runtime.engine_factory = lambda **kwargs: InspectEngine(None)

        await self.service.run_once()
        self.assertEqual(len(captured), 1)
        prompt_blob = json.dumps(captured[0], ensure_ascii=False)
        self.assertIn('surf_discoveries', prompt_blob)
        self.assertIn('恐怖游戏一口气看完', prompt_blob)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_shares').fetchone()[0], 1)
        self.assertEqual(self.runtime.bilibili.unshared_readings(proactive_fixture.CHAT, limit=2), [])


class _ImgGateway:
    async def media(self, *a, **kw):
        import base64
        return {'data': base64.b64encode(_png_bytes()).decode(), 'quality': 'original'}

    async def visible_media(self, *a, **kw):
        return {}


class MomentsRealismAndRotationTests(unittest.IsolatedAsyncioTestCase):
    setUp = moments_fixture.MomentsTests.setUp
    tearDown = moments_fixture.MomentsTests.tearDown
    generation_fixture = moments_fixture.MomentsTests.generation_fixture

    def test_naturalize_caption_strips_theater_bracket_prefix(self):
        raw1 = '【新模型拟人小剧场】\n暴雨熄了公寓的灯，门外却亮起两枚四角星'
        self.assertEqual(_naturalize_moment_caption(raw1), '暴雨熄了公寓的灯，门外却亮起两枚四角星')
        raw2 = '【小剧场】  半夜偷偷下厨煮饭'
        self.assertEqual(_naturalize_moment_caption(raw2), '半夜偷偷下厨煮饭')

    def test_recent_surf_sparks_reads_actual_bilibili_and_growth_tables(self):
        now = time.time()
        role_id = self.runtime.memory_hub.role() if hasattr(self.runtime, 'memory_hub') else 'default'
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS growth_journals(id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    account TEXT NOT NULL, role_id TEXT NOT NULL, topic TEXT NOT NULL,
                    sources TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS growth_insights(memory_id INTEGER PRIMARY KEY,
                    journal_id TEXT NOT NULL, category TEXT NOT NULL, topic TEXT NOT NULL,
                    conditions TEXT NOT NULL, counterexamples TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS bilibili_readings(id TEXT PRIMARY KEY, account TEXT NOT NULL,
                    role TEXT NOT NULL, bvid TEXT NOT NULL, cid INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL, state TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
                    coverage TEXT NOT NULL DEFAULT '{}', source_id TEXT NOT NULL DEFAULT '',
                    journal_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1);
            ''')
            self.store.db.execute(
                '''INSERT INTO growth_journals(id, run_id, account, role_id, topic, sources, body, created)
                   VALUES (?,?,?,?,?,?,?,?)''',
                ('j_m1', 'run_m1', 'account', role_id, '王老菊试玩', '[]', '这boss设计也太阴间了', now - 300),
            )
            self.store.db.execute(
                '''INSERT INTO bilibili_readings(id, account, role, bvid, cid, created, state, metadata, coverage, source_id, journal_id, enabled)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('r_m1', 'account', role_id, 'BV1aa', '1', now - 300, 'done',
                 json.dumps({'title': '王老菊教你影之刃'}, ensure_ascii=False), '{}', '', 'j_m1', 1),
            )
        sparks = self.service._recent_surf_sparks('account')
        self.assertTrue(any('王老菊教你影之刃' in s for s in sparks), sparks)

    def test_world_rotates_two_solo_posts_after_world_episode(self):
        db_path = self.store.db.execute('PRAGMA database_list').fetchone()[2]
        self.runtime.config.directory = Path(db_path).parent
        world_fixture.configure(self.runtime.config)
        self.service.world = moments_fixture.MomentsService(self.runtime).world

        # Unestablished -> prepares world
        self.assertIsNotNone(self.service._prepare_publish_world('account'))

        # Establish 1 world event + record 1 recent world publish -> next 2 yields solo (None)
        ep = json.dumps({'summary': '修望远镜', 'location': '客厅', 'cast_ids': ['whalegirl']}, ensure_ascii=False)
        role_id = self.service.world.scope()[1]
        with self.store.lock, self.store.db:
            self.store.db.execute(
                'INSERT OR REPLACE INTO moments_world_state(account, role_id, design, version, updated) VALUES (?,?,?,?,?)',
                ('account', role_id, '{}', 1, time.time() - 500),
            )
            self.store.db.execute(
                'INSERT INTO moments_world_events(moment_id, account, role_id, episode, created) VALUES (?,?,?,?,?)',
                ('m1', 'account', role_id, ep, time.time() - 500),
            )
            self.store.db.execute(
                '''INSERT INTO moments_actions(id, account, kind, source, payload, text, state, created, updated)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                ('m1', 'account', 'publish', '2026-11-14/0', json.dumps({'world_episode': {'summary': '修望远镜'}}),
                 '一起修望远镜', 'sent', time.time() - 500, time.time() - 500),
            )
        self.assertIsNone(self.service._prepare_publish_world('account'))

        # Add 1 solo post -> still only 1 solo post since world, yields None
        with self.store.lock, self.store.db:
            self.store.db.execute(
                '''INSERT INTO moments_actions(id, account, kind, source, payload, text, state, created, updated)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                ('m2', 'account', 'publish', '2026-11-14/1', json.dumps({'story_seed': 'original'}),
                 '一个人喝奶茶', 'sent', time.time() - 300, time.time() - 300),
            )
        self.assertIsNone(self.service._prepare_publish_world('account'))

        # Add 2nd solo post -> last 2 are solo, yields world again (~33% cadence)
        with self.store.lock, self.store.db:
            self.store.db.execute(
                '''INSERT INTO moments_actions(id, account, kind, source, payload, text, state, created, updated)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                ('m3', 'account', 'publish', '2026-11-14/2', json.dumps({'story_seed': 'original'}),
                 '半夜看B站搞笑视频', 'sent', time.time() - 100, time.time() - 100),
            )
        self.assertIsNotNone(self.service._prepare_publish_world('account'))


class MicroEventsTypingAndIntentsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    async def test_word_length_typing_pause_activates_at_configured_interval(self):
        self.config.save(self.config.settings.model_copy(update={
            'chat_habits_enabled': True, 'reply_part_interval': 0.85,
            'min_send_interval': 0, 'chat_cooldown': 0,
        }))
        ev = self.event()
        self.queue(ev)
        sleeps = []
        async def fake_sleep(secs):
            sleeps.append(secs)
        with patch('whalegirl_linux.runtime.asyncio.sleep', side_effect=fake_sleep):
            engine = await self.runtime.model()
            engine.answers = [{'should_reply': True, 'actions': [
                {'type': 'text', 'text': '第一句'},
                {'type': 'text', 'text': '这是一段比较长的十几个字的第二句话呀'},
            ]}]
            await self.runtime.process_next()
        # First bubble sleep ~0.85+, second bubble len=19 -> min(1.35, max(0.85, 0.35+0.04*19))=1.11
        self.assertTrue(any(0.95 <= s <= 1.25 for s in sleeps), sleeps)

    async def test_cross_chat_self_forward_and_fresh_revoke_awareness(self):
        now = time.time()
        fwd = {
            **self.event(mid=10, content='[合并转发聊天记录｜沃不明白的聊天记录｜1条]\n  1. [2026-11-14 18:03｜沃不明白] 羞耻旧话\n[合并转发结束]'),
            'kind': 49, 'payload_json': '{}', 'timestamp': now - 20,
        }
        rev = {
            'key': 'account/group@chatroom/11', 'account': 'account', 'chat': 'group@chatroom',
            'sender': '微信用户', 'sender_id': '', 'kind': 10000, 'is_self': -1, 'local_id': 11,
            'content': '<?xml version="1.0"?><sysmsg type="revokemsg"><revokemsg><content>"某群友" 撤回了一条消息</content><revoketime>0</revoketime></revokemsg></sysmsg>',
            'timestamp': now - 15, 'payload_json': '{}',
        }
        cur = {**self.event(mid=12, content='哈哈哈看这个'), 'payload_json': '{}', 'timestamp': now}
        self.store.observe([fwd, rev, cur])
        msgs, _, _ = await build_context(
            self.store, None, Settings(vision_enabled=False), cur, [cur], {}, initiative=False,
        )
        txt = msgs[0]['content']
        self.assertIn('合并转发归属提醒', txt)
        self.assertIn('社死现场', txt)
        self.assertIn('刚发生的群内小动态', txt)
        self.assertIn('某群友', txt)

    def test_autonomy_prompt_mentions_followup_for_concrete_upcoming_events(self):
        svc = AutonomyService(self.runtime)
        self.config.save(self.config.settings.model_copy(update={'autonomy_enabled': True}))
        p = svc.static_prompt()
        self.assertIn('1～6小时', p)
        self.assertIn('followup', p)


if __name__ == '__main__':
    unittest.main()

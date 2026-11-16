"""Focused regression tests for human-feel v9 improvements (#2, #3, #4)."""
import tempfile
import time
import unittest
from pathlib import Path

from whalegirl_linux.affection import AffectionBook
from whalegirl_linux.context import build_context
from whalegirl_linux.conversation import (
    LIFE_SCENES,
    anti_formula_check,
    sticker_static_prompt,
    sticker_usage_prompt,
)
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store
from whalegirl_linux.usage_tools import UsageStatsTools


ROOT = Path(__file__).resolve().parents[1]


class UsageProvenanceLabelTests(unittest.TestCase):
    def test_legacy_result_without_split_labels_prepends_reply_heading_idempotently(self):
        decision = {
            'should_reply': True,
            'actions': [{'type': 'text', 'text': '今天用了1000 token'}],
        }
        result = {'ok': True, 'reply_heading': '【本群 · 今天 · 北京时间】'}
        UsageStatsTools.label_reply(decision, result)
        UsageStatsTools.label_reply(decision, result)
        self.assertEqual(
            decision['actions'][0]['text'],
            '【本群 · 今天 · 北京时间】 今天用了1000 token',
        )

    def test_natural_prose_with_scope_and_period_skips_robotic_heading(self):
        decision = {
            'should_reply': True,
            'actions': [{'type': 'text', 'text': '飘渺，全账号这7天一共用了172万多token，参考费用大概3块6人民币。'}],
        }
        result = {
            'ok': True,
            'scope_label': '全账号',
            'period_label': '最近7天',
            'reply_heading': '全账号·最近7天：',
        }
        UsageStatsTools.label_reply(decision, result)
        self.assertEqual(
            decision['actions'][0]['text'],
            '飘渺，全账号这7天一共用了172万多token，参考费用大概3块6人民币。',
        )

    def test_multi_bubble_split_prose_provenance_recognized(self):
        decision = {
            'should_reply': True,
            'actions': [
                {'type': 'text', 'text': '我帮你扫了一眼全账号用量'},
                {'type': 'text', 'text': '近7天差不多用了3块6左右'},
            ],
        }
        result = {
            'ok': True,
            'scope_label': '全账号',
            'period_label': '最近7天',
            'reply_heading': '全账号·最近7天：',
        }
        UsageStatsTools.label_reply(decision, result)
        self.assertEqual(decision['actions'][0]['text'], '我帮你扫了一眼全账号用量')
        self.assertEqual(decision['actions'][1]['text'], '近7天差不多用了3块6左右')

    def test_missing_scope_appends_compact_provenance_tail_idempotently(self):
        decision = {
            'should_reply': True,
            'actions': [
                {'type': 'text', 'text': '这7天大概花了3块6人民币'},
            ],
        }
        result = {
            'ok': True,
            'scope_label': '全账号',
            'period_label': '最近7天',
            'reply_heading': '全账号·最近7天：',
        }
        UsageStatsTools.label_reply(decision, result)
        UsageStatsTools.label_reply(decision, result)
        self.assertEqual(
            decision['actions'][0]['text'],
            '这7天大概花了3块6人民币（全账号·最近7天）',
        )


class AffectionGroupOccasionTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / 'test-runs'
        folder.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='affection-v9-', dir=folder))
        self.store = Store(self.folder / 'state.sqlite3')
        self.book = AffectionBook(self.store)
        self.settings = Settings(
            account_id='acc1',
            allowed_chat_ids=['ext@chatroom', 'owner_wxid'],
            owner_ids=['owner_wxid'],
        )

    def tearDown(self):
        self.store.close()

    def test_owner_calling_preserves_mom_master_without_forcing_full_name(self):
        now = time.time()
        group_msg = {
            'key': 'acc1/ext@chatroom/1',
            'account': 'acc1',
            'chat': 'ext@chatroom',
            'sender': '飘渺',
            'sender_id': 'owner_wxid',
            'kind': 1,
            'is_self': 0,
            'local_id': 1,
            'content': '看看最近消耗',
            'timestamp': now,
            'payload_json': '{}',
        }
        private_msg = {
            **group_msg,
            'key': 'acc1/owner_wxid/2',
            'chat': 'owner_wxid',
            'local_id': 2,
        }
        self.store.observe([group_msg, private_msg])
        venue = self.book.venue_prompt(group_msg, self.settings)
        static = self.book.static_prompt(group_msg, self.settings, include_venue=False)
        dyn_group = self.book.dynamic_prompt(group_msg, [group_msg], self.settings)
        dyn_private = self.book.dynamic_prompt(private_msg, [private_msg], self.settings)

        self.assertNotIn('直呼“飘渺”', venue + static + dyn_group)
        self.assertIn('飘渺是你的妈妈和唯一主人', static)
        self.assertIn('群聊中只认唯一主人', dyn_group)
        self.assertIn('单独私聊', dyn_private)


class RiceCrutchAndStickerPromptTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / 'test-runs'
        folder.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='conv-v9-', dir=folder))
        self.store = Store(self.folder / 'state.sqlite3')

    def tearDown(self):
        self.store.close()

    def test_life_scenes_morning_and_lunch_do_not_spam_white_rice(self):
        rice_morning = sum(1 for item in LIFE_SCENES['morning'] if '白米饭' in item or '小鲸鱼碗' in item)
        rice_lunch = sum(1 for item in LIFE_SCENES['lunch'] if '白米饭' in item or '小鲸鱼碗' in item)
        self.assertEqual(rice_morning, 0)
        self.assertEqual(rice_lunch, 0)

    def test_anti_formula_check_triggers_ban_rice_crutch_when_off_topic_or_repeated(self):
        now = time.time()
        rows = [
            {
                'key': 'acc1/g@chatroom/1', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '友A', 'sender_id': 'u1', 'kind': 1, 'is_self': 0,
                'local_id': 1, 'content': '昨晚那局排位太逆天了', 'timestamp': now - 30, 'payload_json': '{}',
            },
            {
                'key': 'acc1/g@chatroom/2', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '机器人', 'sender_id': 'acc1', 'kind': 1, 'is_self': 1,
                'local_id': 2, 'content': '本鲸鱼还捧着饭碗没饭吃呢', 'timestamp': now - 20, 'payload_json': '{}',
            },
            {
                'key': 'acc1/g@chatroom/3', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '机器人', 'sender_id': 'acc1', 'kind': 1, 'is_self': 1,
                'local_id': 3, 'content': '本鲸鱼差点笑死', 'timestamp': now - 10, 'payload_json': '{}',
            },
        ]
        self.store.observe(rows)
        off_topic_batch = [{
            'key': 'acc1/g@chatroom/4', 'account': 'acc1', 'chat': 'g@chatroom',
            'sender': '友A', 'sender_id': 'u1', 'kind': 1, 'is_self': 0,
            'local_id': 4, 'content': '今天还要写代码好累', 'timestamp': now, 'payload_json': '{}',
        }]
        res = anti_formula_check(self.store, 'acc1', 'g@chatroom', gentle=True, batch=off_topic_batch)
        self.assertTrue(res['ban_rice_crutch'])
        self.assertIn('本鲸鱼', res['banned_self_refs'])
        self.assertIn('饭', res['prompt'])

        food_topic_batch = [{
            **off_topic_batch[0],
            'content': '今天中午吃什么呀',
        }]
        food_res = anti_formula_check(self.store, 'acc1', 'g@chatroom', gentle=True, batch=food_topic_batch)
        self.assertFalse(food_res['ban_rice_crutch'])

        # Repeating twice triggers even on a food topic
        self.store.observe([{
            'key': 'acc1/g@chatroom/5', 'account': 'acc1', 'chat': 'g@chatroom',
            'sender': '机器人', 'sender_id': 'acc1', 'kind': 1, 'is_self': 1,
            'local_id': 5, 'content': '先炫两碗白米饭再说', 'timestamp': now - 5, 'payload_json': '{}',
        }])
        repeat_res = anti_formula_check(self.store, 'acc1', 'g@chatroom', gentle=True, batch=food_topic_batch)
        self.assertTrue(repeat_res['ban_rice_crutch'])

    def test_sticker_prompts_encourage_stickers_while_preserving_guardrails(self):
        settings = Settings(
            account_id='acc1',
            allowed_chat_ids=['g@chatroom'],
            stickers_enabled=True,
            sticker_aggressiveness='high',
            sticker_usage_target=35,
            sticker_text_pair_eagerness=70,
            sticker_only_eagerness=45,
            sticker_reaction_eagerness=70,
            reply_max_parts=3,
        )
        static_p = sticker_static_prompt(settings)
        self.assertIn('只有sticker是完整有效的回复', static_p)
        self.assertIn('不为凑频率制造回复', static_p)
        self.assertIn('不描述画面', static_p)
        self.assertIn('在没有着重提到的情况下不作为聊天主体', static_p)
        self.assertIn('大方放到actions里', static_p)

        usage_p = sticker_usage_prompt(
            self.store,
            settings,
            {'account': 'acc1', 'chat': 'g@chatroom'},
        )
        self.assertIn('期望约35%', usage_p)
        self.assertIn('近期偏少', usage_p)
        self.assertIn('优先甩个贴脸表情包', usage_p)
        self.assertNotIn('绝不把表情作为聊天主体或强求配额', usage_p)


class ContextForwardedAndContinuityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = ROOT / 'test-runs'
        folder.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='ctx-v9-', dir=folder))
        self.store = Store(self.folder / 'state.sqlite3')
        self.settings = Settings(
            account_id='acc1',
            allowed_chat_ids=['g@chatroom'],
            context_messages=20,
            memory_enabled=False,
            vision_enabled=False,
            autonomy_enabled=True,
        )

    def tearDown(self):
        self.store.close()

    async def test_forwarded_chat_record_triggers_attribution_warning_in_normal_and_initiative(self):
        now = time.time()
        fwd_msg = {
            'key': 'acc1/g@chatroom/10',
            'account': 'acc1',
            'chat': 'g@chatroom',
            'sender': '最老实老实人',
            'sender_id': 'u_honest',
            'kind': 49,
            'is_self': 0,
            'local_id': 10,
            'content': '[合并转发聊天记录｜群聊的聊天记录；共4条：[2026-09-22｜飘·渺] 我们是....挚友啊....]',
            'timestamp': now - 200,
            'payload_json': '{}',
        }
        self.store.observe([fwd_msg])

        messages_init, _, _ = await build_context(
            self.store, None, self.settings, fwd_msg, [], {}, initiative=True,
        )
        text_init = messages_init[0]['content']
        self.assertIn('合并转发归属提醒', text_init)
        self.assertIn('最老实老实人', text_init)
        self.assertIn('绝不把转发框里过时的旧发言当成眼下刚发生的新对话', text_init)

        trigger_msg = {
            'key': 'acc1/g@chatroom/11',
            'account': 'acc1',
            'chat': 'g@chatroom',
            'sender': '最老实老实人',
            'sender_id': 'u_honest',
            'kind': 1,
            'is_self': 0,
            'local_id': 11,
            'content': '哈哈你看以前这记录',
            'timestamp': now,
            'payload_json': '{}',
        }
        self.store.observe([trigger_msg])
        messages_react, _, _ = await build_context(
            self.store, None, self.settings, trigger_msg, [trigger_msg], {}, initiative=False,
        )
        text_react = messages_react[0]['content']
        self.assertIn('合并转发归属提醒', text_react)

    async def test_recent_same_speaker_emotional_stream_preserved_across_keyword_trigger(self):
        now = time.time()
        rows = [
            {
                'key': 'acc1/g@chatroom/20', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '飘渺', 'sender_id': 'owner_wxid', 'kind': 1, 'is_self': 0,
                'local_id': 20, 'content': '我今天参加比赛失利了', 'timestamp': now - 420, 'payload_json': '{}',
            },
            {
                'key': 'acc1/g@chatroom/21', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '飘渺', 'sender_id': 'owner_wxid', 'kind': 1, 'is_self': 0,
                'local_id': 21, 'content': '今天要弄的东西太多了', 'timestamp': now - 150, 'payload_json': '{}',
            },
            {
                'key': 'acc1/g@chatroom/22', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '飘渺', 'sender_id': 'owner_wxid', 'kind': 1, 'is_self': 0,
                'local_id': 22, 'content': '大肥鱼怎么这么久不说话', 'timestamp': now, 'payload_json': '{}',
            },
        ]
        self.store.observe(rows)
        messages, _, _ = await build_context(
            self.store, None, self.settings, rows[-1], [rows[-1]], {}, initiative=False,
        )
        text = messages[0]['content']
        self.assertIn('近15分钟同人情绪与语境连续性', text)
        self.assertIn('我今天参加比赛失利了', text)
        self.assertIn('今天要弄的东西太多了', text)

    async def test_old_same_speaker_message_outside_window_does_not_trigger_short_continuity(self):
        now = time.time()
        rows = [
            {
                'key': 'acc1/g@chatroom/30', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '飘渺', 'sender_id': 'owner_wxid', 'kind': 1, 'is_self': 0,
                'local_id': 30, 'content': '中午好累啊', 'timestamp': now - 3600, 'payload_json': '{}',
            },
            {
                'key': 'acc1/g@chatroom/31', 'account': 'acc1', 'chat': 'g@chatroom',
                'sender': '飘渺', 'sender_id': 'owner_wxid', 'kind': 1, 'is_self': 0,
                'local_id': 31, 'content': '晚上玩啥游戏', 'timestamp': now, 'payload_json': '{}',
            },
        ]
        self.store.observe(rows)
        messages, _, _ = await build_context(
            self.store, None, self.settings, rows[-1], [rows[-1]], {}, initiative=False,
        )
        self.assertNotIn('近15分钟同人情绪与语境连续性', messages[0]['content'])


if __name__ == '__main__':
    unittest.main()

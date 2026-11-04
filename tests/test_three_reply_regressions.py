"""Regression coverage for dedupe, mention-preserving caps and zero debounce."""
import asyncio
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from whalegirl_linux.conversation import cap_text_actions, dedupe_recent_self_actions, plan_actions
from whalegirl_linux.mentions import apply_mentions
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Settings


def texts(*values):
    return [{'type': 'text', 'text': value, 'display': value} for value in values]


class RecentReplyDedupeTests(unittest.TestCase):
    def test_negative_correction_survives_actual_plan(self):
        actions = plan_actions({'actions': texts('不可以退款', '需要先联系商家')}, {},
            Settings(), recent_self_texts=['可以退款'])
        self.assertEqual([a['text'] for a in actions], ['不可以退款', '需要先联系商家'])

    def test_added_condition_is_not_a_repeat(self):
        actions = texts('仅周五可以退款', '其他时间请联系客服')
        self.assertEqual(dedupe_recent_self_actions(actions, ['可以退款']), actions)

    def test_changed_acknowledgement_is_preserved(self):
        actions = texts('刚才不在呢', '现在看到消息了')
        self.assertEqual(dedupe_recent_self_actions(actions, ['我在呢']), actions)

    def test_correction_about_a_roleplay_prop_is_preserved(self):
        actions = texts('我没有晃尾巴', '你看错啦')
        self.assertEqual(dedupe_recent_self_actions(actions, ['正在晃尾巴']), actions)

    def test_exact_recent_repeat_is_still_removed(self):
        self.assertEqual(dedupe_recent_self_actions(texts('收到啦', '现在开始检查'), ['收到啦']),
                         texts('现在开始检查'))

    def test_bare_acknowledgement_variants_are_still_removed(self):
        self.assertEqual(dedupe_recent_self_actions(texts('在呢', '刚看到你的消息'), ['我在呀']),
                         texts('刚看到你的消息'))

    def test_bare_prop_repeat_is_still_removed(self):
        self.assertEqual(dedupe_recent_self_actions(texts('晃尾巴', '刚看到你的消息'), ['尾巴都晃']),
                         texts('刚看到你的消息'))

    def test_duplicate_current_bubble_is_still_removed(self):
        self.assertEqual(dedupe_recent_self_actions(texts('收到啦', '收到啦', '现在开始检查')),
                         texts('收到啦', '现在开始检查'))


class SearchMentionCapTests(unittest.TestCase):
    def test_all_four_recipients_reach_native_mention_resolution(self):
        actions = texts('甲', '乙', '第三位的说明', '第四位的说明')
        for index, action in enumerate(actions, 1):
            action['mentions'] = ['m' + str(index)]
        before = copy.deepcopy(actions)
        merged = cap_text_actions(actions, 3)
        options = {'m' + str(i): {'id': 'person-' + str(i), 'name': '群友' + str(i)} for i in range(1, 5)}
        resolved = apply_mentions(merged, options, True)
        self.assertEqual({p['id'] for a in resolved for p in a.get('mentions', [])},
                         {'person-' + str(i) for i in range(1, 5)})
        self.assertEqual(len(resolved), 3)
        self.assertEqual(actions, before)

    def test_targets_are_deduplicated_in_original_order(self):
        actions = texts('甲', '乙')
        actions[0]['mentions'] = ['m1', 'm2']
        actions[1]['mentions'] = ['m2', 'm3']
        self.assertEqual(cap_text_actions(actions, 1)[0]['mentions'], ['m1', 'm2', 'm3'])

    def test_overflow_does_not_silently_lose_native_targets(self):
        actions = texts('甲', '乙')
        actions[0]['mentions'] = ['m1', 'm2']
        actions[1]['mentions'] = ['m3', 'm4']
        self.assertEqual(cap_text_actions(actions, 1), actions)

    def test_distinct_quotes_are_not_merged(self):
        actions = texts('甲', '乙')
        actions[0]['quote_local_id'] = 1
        actions[1]['quote_local_id'] = 2
        self.assertEqual(cap_text_actions(actions, 1), actions)

    def test_sticker_order_and_plain_text_merge_are_preserved(self):
        actions = texts('甲', '乙') + [{'type': 'sticker', 'id': 'fixture'}] + texts('丙')
        merged = cap_text_actions(actions, 2)
        self.assertEqual([a['type'] for a in merged], ['text', 'sticker', 'text'])
        self.assertEqual(merged[0]['text'], '甲，乙')


class DebounceSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def elapsed(self, *, content='你好', kind=1, followup=False,
                      single=0, burst=0, maximum=0, habits=True):
        settings = Settings(enabled=True, chat_habits_enabled=habits,
            followup_wait_enabled=followup, debounce_single_seconds=single,
            debounce_burst_seconds=burst, debounce_max_seconds=maximum)
        first = {'account': 'fixture', 'chat': 'fixture@chatroom', 'updated': 1000.,
                 'kind': kind, 'content': content}
        runtime = SimpleNamespace(store=SimpleNamespace(queued_chat=lambda *args: [first]),
            config=SimpleNamespace(settings=settings, generation=1), processing='')
        clock = [1000.]
        async def sleep(seconds):
            clock[0] += seconds
        with patch('whalegirl_linux.runtime.time.time', side_effect=lambda: clock[0]), \
             patch('whalegirl_linux.runtime.asyncio.sleep', side_effect=sleep):
            result = await Runtime.debounce(runtime, first, settings, 1)
        self.assertEqual(result, [first])
        return clock[0] - 1000.

    async def test_zero_buffers_do_not_wait_for_greeting_or_sticker(self):
        for followup in [False, True]:
            for kind, content in [(1, '你好'), (47, '[原生表情包]')]:
                with self.subTest(followup=followup, kind=kind):
                    self.assertEqual(await self.elapsed(content=content, kind=kind, followup=followup), 0)

    async def test_zero_single_buffer_does_not_enable_opener_wait(self):
        self.assertEqual(await self.elapsed(single=0, burst=.2, maximum=1), 0)

    async def test_maximum_buffer_remains_a_hard_deadline(self):
        self.assertAlmostEqual(await self.elapsed(single=.05, burst=.1, maximum=.2), .2)

    async def test_enabled_opener_wait_still_works_with_positive_limits(self):
        self.assertAlmostEqual(await self.elapsed(single=.1, burst=.2, maximum=3), 2.6)

    async def test_regular_message_buffer_is_unchanged(self):
        self.assertAlmostEqual(await self.elapsed(content='明天开会带资料', single=.3,
            burst=.2, maximum=1), .3)

    async def test_disabled_chat_habits_bypass_buffer(self):
        self.assertEqual(await self.elapsed(single=.3, burst=.2, maximum=1, habits=False), 0)


if __name__ == '__main__':
    unittest.main()

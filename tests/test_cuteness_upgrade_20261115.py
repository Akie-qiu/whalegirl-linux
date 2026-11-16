"""Focused regressions for 2026-11-15 Tendency-4 cuteness upgrade (Plan B)."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import test_chat_habits as habits
from whalegirl_linux import fishtalk_rules as ft_rules
from whalegirl_linux.affection import AffectionBook
from whalegirl_linux.conversation import (
    anti_formula_check,
    circuit_break_actions,
    expression_preferences_prompt,
    habit_static_prompt,
    sticker_usage_prompt,
)
from whalegirl_linux.fishtalk import FishtalkService
from whalegirl_linux.learning import LearningBook
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class CutenessPromptAndCalibrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    async def test_tiered_tendency4_prompts_unlock_ellipsis_doubling_and_sticker_clarity(self):
        self.config.save(self.config.settings.model_copy(update={
            'girlish_tone': 'soft',
            'persona_density': 'high',
            'sticker_after_text': 'off',
        }))
        ev = self.event(mid=1, content='这看着太肥了吧')
        self.queue(ev)
        engine = await self.runtime.model()
        seen = []

        async def capture(messages, **kwargs):
            seen.append(kwargs.get('system_prompt', ''))
            return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '哼……哪有嘛~'}]}, ensure_ascii=False)

        engine.chat = capture
        await self.runtime.process_next()
        prompt = seen[0]
        self.assertNotIn('省掉前半句复述铺垫与句首“唔/呜”口癖', prompt)
        self.assertNotIn('“草”', expression_preferences_prompt(self.config.settings))
        self.assertIn('不算拆条', prompt)
        self.assertIn('「……」', prompt)
        self.assertIn('娇憨小傲娇', prompt)
        self.assertIn('叠字', prompt)

        book = AffectionBook(self.store)
        cfg = self.config.settings.model_copy(update={'owner_ids': ['wxid_owner_real']})
        owner_vp = book.venue_prompt({'chat': 'wxid_owner_real', 'sender_id': 'wxid_owner_real'}, cfg)
        group_vp = book.venue_prompt({'chat': 'a@chatroom', 'sender_id': 'u1'}, cfg)
        self.assertIn('软糯娇怯', owner_vp)
        self.assertIn('娇憨小傲娇', group_vp)
        self.assertIn('「……」', habit_static_prompt(cfg))

    def test_anti_formula_restores_strict_wu_opener_limit_and_catches_tilde_soft_tail_streak(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        store = Store(Path(tempfile.mkdtemp(prefix='cute-af-', dir=runs)) / 'state.sqlite3')
        self.addCleanup(store.close)
        now = time.time()
        msgs = [
            (1, '唔……今天风好大呀'),
            (2, '确认本人了~'),
            (3, '唔……那待会儿再说啦'),
            (4, '看完成甲壳虫别找我~'),
        ]
        store.observe([{
            'key': f'acc/g@chatroom/{idx}', 'account': 'acc', 'chat': 'g@chatroom',
            'sender': 'bot', 'sender_id': 'acc', 'kind': 1, 'is_self': 1,
            'local_id': idx, 'content': text, 'timestamp': now - (10 - idx), 'payload_json': '{}',
        } for idx, text in msgs])
        res4 = anti_formula_check(store, 'acc', 'g@chatroom', gentle=True)
        # Strict opener guard for '唔' triggers at 2 hits even when gentle=True.
        self.assertIn('唔', res4['banned_openers'])
        # Four consecutive soft/tilde endings ('呀', '~', '啦', '~') also trigger anti-streak nudge.
        self.assertIn('连续几句都用「呀/嘛/啦/呢」收尾了', res4['prompt'])

        # Without explicit user filler critique, single upcoming line is not physically amputated.
        kept, meta = circuit_break_actions(
            [{'type': 'text', 'text': '有点想吃白米饭', 'display': '有点想吃白米饭'}],
            incoming_text='饿了吗',
            rut_info=res4,
        )
        self.assertEqual(kept[0]['text'], '有点想吃白米饭')
        self.assertEqual(meta['changed'], 0)

    def test_sticker_usage_prompt_detects_cold_streak_without_breaking_diagnostics_or_model_plan(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        store = Store(Path(tempfile.mkdtemp(prefix='cute-stk-', dir=runs)) / 'state.sqlite3')
        self.addCleanup(store.close)
        base = {'account': 'acc', 'chat': 'g@chatroom', 'sender': 'u1', 'sender_id': 'u1',
                'kind': 1, 'is_self': 0, 'local_id': 1, 'content': '在吗', 'timestamp': time.time(),
                'trigger': 'direct', 'payload_json': '{}'}
        # 6 older sticker turns + 5 most recent text-only turns => 6/11 = 54.5% >= target 40%,
        # yet recent cold streak is 5 text-only replies in a row.
        for i in range(1, 12):
            k = f'turn-{i}'
            ev = {**base, 'key': k}
            kind = 'sticker' if i <= 6 else 'text'
            ident = store.action_start(k, ev, 0, {'type': kind, 'display': 'test'})
            store.action_finish(ident, 'sent')
            store.db.execute('UPDATE outbound_actions SET sent_at=? WHERE inbox_key=?', (time.time() - (20 - i), k))
        stats = {}
        settings = Settings(stickers_enabled=True, sticker_usage_target=40, sticker_aggressiveness='high')
        prompt = sticker_usage_prompt(store, settings, base, stats)
        self.assertEqual(stats, {'target': 40, 'recent_turns': 11, 'sticker_turns': 6})
        self.assertIn('已连续5轮未发表情', prompt)


class LearningIsolationAndFishtalkTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='cute-learn-', dir=runs))
        self.store = Store(self.folder / 'state.sqlite3')
        self.addCleanup(self.store.close)
        self.settings = Settings(
            account_id='account',
            owner_ids=['wxid_owner'],
            allowed_chat_ids=['wxid_owner', 'grp@chatroom'],
            global_memory=True,
            memory_scope_mode='shared_profiles',
            learning_auto_accept=True,
        )
        self.cfg = Config(self.folder)
        self.cfg.save(self.settings)

    def test_owner_dm_situational_rules_do_not_pollute_group_recall_in_hub_or_fallback(self):
        dm_msgs = [
            (1, 'wxid_owner', 'wxid_owner', '中午十二点喊我午睡叫醒'),
            (2, 'wxid_owner', 'wxid_owner', '刚拍的照片发你啦别复述直接顺着刚说的哄，别用自己…'),
            (3, 'wxid_owner', 'wxid_owner', '以后别用‘呜’开头说话'),
            (4, 'wxid_owner', 'wxid_owner', '平时说话也要保持18岁少女软萌自然口吻'),
        ]
        for lid, chat, sid, txt in dm_msgs:
            self.store.observe([{
                'key': f'account/{chat}/{lid}', 'account': 'account', 'chat': chat,
                'sender': '主人', 'sender_id': sid, 'kind': 1, 'is_self': 0,
                'local_id': lid, 'content': txt, 'timestamp': time.time() - 50 + lid, 'payload_json': '{}',
            }])
        grp_ev = {
            'key': 'account/grp@chatroom/10', 'account': 'account', 'chat': 'grp@chatroom',
            'sender': '主人', 'sender_id': 'wxid_owner', 'kind': 1, 'is_self': 0,
            'local_id': 10, 'content': '这看着太肥了吧', 'timestamp': time.time(), 'payload_json': '{}',
        }
        self.store.observe([grp_ev])

        mem_book = MemoryBook(self.store)
        mem_book.hub.config = self.cfg
        self.store.memory_hub = mem_book.hub
        book = LearningBook(self.store)

        proposals = [
            ('午睡叫醒规则要记住', '中午十二点喊我午睡叫醒', f'account/wxid_owner/1'),
            ('被主人催着看羞耻样子时，别复述她刚说的词、别用“自己”反复堆句子，直接一句软糯短句给反应', '别复述直接顺着刚说的哄', f'account/wxid_owner/2'),
            ('别用‘呜’开头', '以后别用‘呜’开头说话', f'account/wxid_owner/3'),
            ('保持18岁少女软萌自然口吻', '保持18岁少女软萌自然口吻', f'account/wxid_owner/4'),
        ]
        for idx, (content, evidence, skey) in enumerate(proposals, start=1):
            src = {'key': skey, 'account': 'account', 'chat': 'wxid_owner', 'sender_id': 'wxid_owner'}
            book.propose(src, [src], {
                'type': 'chat_style', 'scope': 'chat', 'subject': 'self',
                'source_key': skey, 'evidence': evidence, 'content': content, 'confidence': 0.95,
            }, self.settings)

        # 1) Production MemoryHub path
        hub_recalled = book.recall(grp_ev, [grp_ev], '这看着太肥了吧', self.settings)
        hub_texts = [r['content'] for r in hub_recalled]
        self.assertIn('保持18岁少女软萌自然口吻', hub_texts)
        self.assertNotIn('午睡叫醒规则要记住', hub_texts)
        self.assertNotIn('被主人催着看羞耻样子时，别复述她刚说的词、别用“自己”反复堆句子，直接一句软糯短句给反应', hub_texts)
        self.assertNotIn('别用‘呜’开头', hub_texts)

        # 2) Fallback SQLite path (memory_hub = None)
        self.store.memory_hub = None
        fb_recalled = book.recall(grp_ev, [grp_ev], '这看着太肥了吧', self.settings)
        fb_texts = [r['content'] for r in fb_recalled]
        self.assertNotIn('被主人催着看羞耻样子时，别复述她刚说的词、别用“自己”反复堆句子，直接一句软糯短句给反应', fb_texts)
        self.assertNotIn('别用‘呜’开头', fb_texts)

    def test_fishtalk_rules_exempt_cute_ellipsis_and_tilde_and_filter_dry_bro_examples(self):
        cute_prev = ['唔……其实我也挺想去的', '哼，才不是特意等你的~']
        res_cute = ft_rules.check('好嘛好嘛，那我就勉为其难夸你一句好啦~', previous=cute_prev)
        self.assertNotIn('two_part_rut', res_cute['notes'])

        # Genuine space-separated two-part boilerplate still triggers
        flat_prev = ['哇好厉害 我也想去看一看', '哈哈真的吗 我刚才差点就信了']
        res_flat = ft_rules.check('天呐不会吧 我怎么感觉哪里怪怪的', previous=flat_prev)
        self.assertIn('two_part_rut', res_flat['notes'])

        # Dry bro-slang samples are skipped in dynamic_prompt when soft alternatives exist
        self.store.observe([{
            'key': f'account/grp@chatroom/{i}', 'account': 'account', 'chat': 'grp@chatroom',
            'sender_id': f'u{i % 3}', 'sender': f'群友{i % 3}',
            'content': text, 'timestamp': time.time() - 60 + i, 'is_self': 0, 'kind': 1, 'local_id': i,
            'payload_json': '{}',
        } for i, text in enumerate([
            '不是大哥，我雄竞在哪？',
            '本人目前暂无出任英伟达CEO的计划',
            '总共有5000页慢慢翻呗',
            '今天天气真不错呀一起去散步嘛',
            '晚饭想吃点甜甜的小蛋糕呢',
            '这个小猫表情也太可爱了吧',
            '下班啦今天好累想躺平一会儿',
            '周末一起去看新上映的动画电影呀',
            '刚刚路边看见一只胖橘晒太阳',
        ], start=1)])
        rt = SimpleNamespace(
            store=self.store,
            config=self.cfg,
            processing='',
            memory_hub=SimpleNamespace(forget_cutoff=lambda *a: 0),
            memory_book=SimpleNamespace(suppressed_sources=lambda *a: set()),
        )
        svc = FishtalkService(rt)
        svc.rebuild('grp@chatroom')
        dyn = svc.dynamic_prompt({'account': 'account', 'chat': 'grp@chatroom'}, [{'content': '有点累呀', 'is_self': 0}])
        self.assertNotIn('不是大哥', dyn)
        self.assertNotIn('CEO的计划', dyn)
        self.assertIn('严禁模仿男群友/大叔的冷直男反问', dyn)


if __name__ == '__main__':
    unittest.main()

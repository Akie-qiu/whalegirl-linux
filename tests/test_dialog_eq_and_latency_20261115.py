"""Focused regressions for 2026-11-15 dialog EQ, latency, Moments, and Bilibili reply upgrades."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import test_bilibili_reply as bili_fixture
import test_chat_habits as habits
import test_moments as moments_fixture
import test_proactive as proactive_fixture

from whalegirl_linux import fishtalk_rules as ft_rules
from whalegirl_linux.conversation import (
    anti_formula_check,
    circuit_break_actions,
    expression_preferences_prompt,
    habit_static_prompt,
    is_followup_wait_candidate,
)
from whalegirl_linux.fishtalk import FishtalkService
from whalegirl_linux.life_autonomy import LifeAutonomyService
from whalegirl_linux.moments_creativity import story_brief
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store


class LatencyAndStaleCheckTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    def test_followup_wait_candidate_filters_pat_stickers_and_complete_messages(self):
        settings = Settings(followup_wait_enabled=True, followup_wait_seconds=10)
        pat_ev = {**self.event(mid=1, content='朋友 拍了拍你', trigger='pat')}
        sticker_ev = {**self.event(mid=2, content='[表情包]', trigger='direct'), 'kind': 47}
        complete_ev = self.event(mid=3, content='今天晚上大家吃什么呀', trigger='direct')
        callout_ev = self.event(mid=4, content='鲸鱼娘', trigger='direct')
        leadin_ev = self.event(mid=5, content='等一下我给你看个图', trigger='direct')

        self.assertFalse(is_followup_wait_candidate(pat_ev, [pat_ev], settings))
        self.assertFalse(is_followup_wait_candidate(sticker_ev, [sticker_ev], settings))
        self.assertFalse(is_followup_wait_candidate(complete_ev, [complete_ev], settings))
        self.assertTrue(is_followup_wait_candidate(callout_ev, [callout_ev], settings))
        self.assertTrue(is_followup_wait_candidate(leadin_ev, [leadin_ev], settings))

    async def test_wait_tool_is_hidden_for_pat_or_complete_chat_and_capped_at_4500ms_for_leadin(self):
        self.config.save(self.config.settings.model_copy(update={
            'followup_wait_enabled': True, 'followup_wait_seconds': 10,
            'web_search_enabled': False, 'sticker_after_text': 'off',
        }))

        for idx, ev in enumerate((
            self.event(mid=10, content='群友 拍了拍你', trigger='pat'),
            self.event(mid=11, content='刚下班回来好累啊你吃了吗', trigger='direct'),
        ), start=1):
            self.queue(ev)
            engine = await self.runtime.model()
            engine.api_style = 'openai'
            engine.api_url = 'https://api.deepseek.com/chat/completions'
            engine.stream_enabled = False
            observed_tools = []
            captured_prompts = []

            async def chat_no_tools(messages, **kwargs):
                captured_prompts.append(kwargs.get('system_prompt', ''))
                return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': f'收到{idx}'}]}, ensure_ascii=False)

            async def native_tools(messages, tools, **kwargs):
                observed_tools.extend(t['function']['name'] for t in tools)
                return {'content': json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': f'收到{idx}'}]}, ensure_ascii=False), 'tool_calls': []}

            engine.chat = chat_no_tools
            engine.chat_with_tools = native_tools
            await self.runtime.process_next()
            self.assertNotIn('wait_for_next_message', observed_tools)
            for p in captured_prompts:
                self.assertNotIn('wait_for_next_message', p)

        # Callout should expose wait_for_next_message with parameter ceiling capped at <= 4.5s.
        callout = self.event(mid=12, content='鲸鱼娘')
        self.queue(callout)
        engine = await self.runtime.model()
        engine.api_style = 'openai'
        engine.api_url = 'https://api.deepseek.com/chat/completions'
        engine.stream_enabled = False
        tool_schemas = []

        async def native_wait(messages, tools, **kwargs):
            tool_schemas.extend(tools)
            return {
                'content': '',
                'tool_calls': [{
                    'id': 'wait1',
                    'function': {'name': 'wait_for_next_message', 'arguments': json.dumps({'reason': '先叫名字多半后面还有话'})},
                }],
            }

        engine.chat_with_tools = native_wait
        await self.runtime.process_next()
        wait_tool = [t for t in tool_schemas if t['function']['name'] == 'wait_for_next_message']
        self.assertEqual(len(wait_tool), 1)
        row = self.store.get(callout['key'])
        self.assertEqual(row['state'], 'waiting')
        metrics_row = self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?', (callout['key'],)).fetchone()
        self.assertIsNotNone(metrics_row)
        timing = json.loads(metrics_row[0])
        self.assertLessEqual(float(timing['followup_wait']['seconds']), 4.5)
        self.assertIn(callout['chat'], self.runtime.followup_waits)

    async def test_stale_check_supersedes_before_send_when_new_message_arrives_during_model(self):
        first = self.event(mid=20, content='帮我找下那个文件')
        self.queue(first)
        engine = await self.runtime.model()
        followup = self.event(mid=21, content='算了不用找了我自己翻到了')

        async def slow_model(messages, **kwargs):
            self.queue(followup)
            return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '我这就去帮你找'}]}, ensure_ascii=False)

        engine.chat = slow_model
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.store.get(first['key'])['state'], 'merged')
        metrics_row = self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?', (first['key'],)).fetchone()
        self.assertIsNotNone(metrics_row)
        timing = json.loads(metrics_row[0])
        self.assertTrue(timing.get('stale_check', {}).get('superseded'))

        # Subsequent round processes the newer message cleanly without overlap.
        async def final_model(messages, **kwargs):
            return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '找到就好啦'}]}, ensure_ascii=False)

        engine.chat = final_model
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [('text', '找到就好啦')])

    async def test_stale_check_preempts_at_ui_session_and_brakes_second_bubble_to_partial(self):
        # 1) Pre-UI session stale check cancels zero-sent turn before gateway.send.
        first = self.event(mid=30, content='你在吗')
        self.queue(first)
        engine = await self.runtime.model()
        engine.answers = [{'should_reply': True, 'actions': [{'type': 'text', 'text': '在呀'}]}]
        orig_guard = self.runtime.fishtalk.guard_actions
        injected = False

        def guard_injects_new(f, actions, ev, diag):
            nonlocal injected
            res = orig_guard(f, actions, ev, diag)
            if not injected:
                injected = True
                self.queue(self.event(mid=31, content='刚打错字了我其实想问别的事'))
            return res

        self.runtime.fishtalk.guard_actions = guard_injects_new
        await self.runtime.process_next()
        self.runtime.fishtalk.guard_actions = orig_guard
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.store.get(first['key'])['state'], 'merged')
        metrics_row = self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?', (first['key'],)).fetchone()
        timing = json.loads(metrics_row[0])
        self.assertEqual(timing.get('stale_check', {}).get('stage'), 'before_ui_session')

        # Consume mid=31 so queue stays clean for part 2.
        engine.answers = [{'should_reply': True, 'actions': [{'type': 'text', 'text': '你说吧'}]}]
        await self.runtime.process_next()
        self.gateway.sent.clear()

        # 2) Multi-bubble brake after bubble 1 is confirmed and bubble 2 sees newer incoming message.
        second = self.event(mid=32, content='今天好无聊')
        self.queue(second)
        engine.answers = [{'should_reply': True, 'actions': [
            {'type': 'text', 'text': '刚想跟你说点趣事'},
            {'type': 'text', 'text': '这句在新人插话后应该刹车'},
        ]}]
        orig_send = self.gateway.send

        async def send_and_interrupt(chat, text, **options):
            receipt = await orig_send(chat, text, **options)
            if len(self.gateway.sent) == 1:
                self.queue(self.event(mid=33, content='等下我突然想起件事'))
            return receipt

        self.gateway.send = send_and_interrupt
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent, [('text', '刚想跟你说点趣事')])
        self.assertEqual(self.store.get(second['key'])['state'], 'partial')


class PromptDirectionAndAntiFormulaTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    async def test_prompts_enforce_single_soft_direct_bubble_without_old_two_part_boilerplate_or_temp_change(self):
        self.assertIsNone(Settings().model_temperature)
        self.config.save(self.config.settings.model_copy(update={'sticker_after_text': 'off'}))
        ev = self.event(mid=1, content='你喜欢晴天还是雨天呀')
        self.queue(ev)
        engine = await self.runtime.model()
        seen_prompts = []

        async def capture(messages, **kwargs):
            seen_prompts.append(kwargs.get('system_prompt', ''))
            return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '喜欢下雨天躲被窝听雨声'}]}, ensure_ascii=False)

        engine.chat = capture
        await self.runtime.process_next()
        combined = '\n'.join(seen_prompts) + '\n' + expression_preferences_prompt(Settings()) + '\n' + habit_static_prompt(Settings())
        self.assertNotIn('优先“第1条2～6字短反应 + 第2条具体吐槽/追问”', combined)
        self.assertNotIn('第1条12字以内短反应 + 第2条再补内容', combined)
        self.assertIn('默认只发1条2～20字软糯直球短气泡', combined)
        self.assertIn('禁止', combined)

    def test_space_and_newline_two_part_symmetry_input_echo_and_nagging_gentle_trim(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='anti-formula-', dir=runs))
        store = Store(folder / 'state.sqlite3')
        self.addCleanup(store.close)
        now = time.time()
        history = [
            (1, '哇这个真的吗 我觉得你刚刚讲的太有道理了吧'),
            (2, '好好好 等下我去试试看呀，早点睡哦别熬夜了'),
            (3, '太夸张了吧 注意休息别熬夜啦'),
        ]
        store.observe([{
            'key': f'acc/g@chatroom/{idx}', 'account': 'acc', 'chat': 'g@chatroom',
            'sender': 'bot', 'sender_id': 'acc', 'kind': 1, 'is_self': 1,
            'local_id': idx, 'content': text, 'timestamp': now - (10 - idx), 'payload_json': '{}',
        } for idx, text in history])
        incoming = {
            'key': 'acc/g@chatroom/9', 'account': 'acc', 'chat': 'g@chatroom',
            'sender': 'u1', 'sender_id': 'u1', 'kind': 1, 'is_self': 0,
            'local_id': 9, 'content': '看我刚买的新皮肤', 'timestamp': now, 'payload_json': '{}',
        }
        rut = anti_formula_check(store, 'acc', 'g@chatroom', gentle=True, batch=[incoming])
        self.assertTrue(rut.get('ban_balanced_couplet'))
        self.assertTrue(rut.get('ban_nagging_streak'))

        # Two-bubble + single-bubble interrogation / echo trimming without emptying the reply.
        trimmed_two, meta_two = circuit_break_actions(
            [
                {'type': 'text', 'text': '这套新皮肤超帅呀', 'display': '这套新皮肤超帅呀'},
                {'type': 'text', 'text': '还是限时买的吗？', 'display': '还是限时买的吗？'},
            ],
            incoming_text='看我刚买的新皮肤',
            rut_info={'ban_end_question': True, 'ban_balanced_couplet': True, 'ban_nagging_streak': True, 'input_echo_rut': True},
        )
        self.assertTrue(any(a['type'] == 'text' and a['text'].strip() for a in trimmed_two))
        joined = ' '.join(a.get('text', '') for a in trimmed_two if a['type'] == 'text')
        self.assertIn('超帅呀', joined)
        self.assertNotIn('还是限时买的', joined)
        self.assertGreaterEqual(meta_two['changed'], 1)

        trimmed_inline, meta_inline = circuit_break_actions(
            [{'type': 'text', 'text': '这套新皮肤超帅啊 还是限时买的吗？', 'display': '这套新皮肤超帅啊 还是限时买的吗？'}],
            incoming_text='看我刚买的新皮肤',
            rut_info={'ban_end_question': True},
        )
        self.assertEqual(trimmed_inline[0]['text'], '这套新皮肤超帅啊')
        self.assertGreaterEqual(meta_inline['changed'], 1)

        # Single bubble trailing sleep nagging is gently stripped when ban_nagging_streak triggers.
        trimmed_nag, meta_nag = circuit_break_actions(
            [{'type': 'text', 'text': '那套配色确实好看，早点睡哦别熬夜了', 'display': '那套配色确实好看，早点睡哦别熬夜了'}],
            incoming_text='新皮肤真好看',
            rut_info={'ban_nagging_streak': True},
        )
        self.assertEqual(len(trimmed_nag), 1)
        self.assertNotIn('别熬夜', trimmed_nag[0]['text'])
        self.assertIn('配色确实好看', trimmed_nag[0]['text'])
        self.assertGreaterEqual(meta_nag['changed'], 1)

    def test_fishtalk_rules_cross_turn_notes_and_hints_mapping(self):
        q_prev = ['这个真的假的呀？', '你晚上吃啥呀？', '明天还要早起吗？']
        res_q = ft_rules.check('那不是很累呀？', previous=q_prev, examples=[])
        self.assertIn('question_streak', res_q['notes'])

        sym_prev = ['哇好厉害 我也想去看一看', '哈哈真的吗 我刚才差点就信了']
        res_sym = ft_rules.check('天呐不会吧 我怎么感觉哪里怪怪的', previous=sym_prev, examples=[])
        self.assertIn('two_part_rut', res_sym['notes'])

        fast_prev = ['哇塞这也太绝了吧真的绝了', '天呐怎么会这样太离谱啦']
        res_fast = ft_rules.check('哇塞这也太绝了怎么能这么好', previous=fast_prev, examples=[])
        self.assertIn('fast_opener_repeat', res_fast['notes'])

        nag_prev = ['今天早点睡别太晚啦', '记得多喝水注意休息呀']
        res_nag = ft_rules.check('别熬夜啦赶紧去睡吧', previous=nag_prev, examples=[])
        self.assertIn('nagging_streak', res_nag['notes'])

        # Map notes through FishtalkService into dynamic_prompt feedback (up to 4 codes).
        runs = ROOT / 'test-runs'
        folder = Path(tempfile.mkdtemp(prefix='ft-notes-', dir=runs))
        store = Store(folder / 'state.sqlite3')
        self.addCleanup(store.close)
        store.observe([{
            'key': f'account/a@chatroom/{i}', 'account': 'account', 'chat': 'a@chatroom',
            'sender_id': 'alice' if i % 2 else 'bob', 'sender': 'name',
            'content': f'大家平时晚上聊第{i}局呀真有意思', 'timestamp': time.time() - 50 + i,
            'is_self': 0, 'kind': 1, 'local_id': i, 'payload_json': '{}',
        } for i in range(1, 12)])
        rt = SimpleNamespace(
            store=store,
            config=SimpleNamespace(settings=Settings(
                account_id='account', allowed_chat_ids=['a@chatroom'], active_persona_id='whale',
            )),
            processing='',
            memory_hub=SimpleNamespace(forget_cutoff=lambda *args: 0),
            memory_book=SimpleNamespace(suppressed_sources=lambda *args: set()),
        )
        svc = FishtalkService(rt)
        svc.rebuild('a@chatroom')
        with store.lock, store.db:
            store.db.execute(
                'INSERT OR REPLACE INTO fishtalk_feedback VALUES(?,?,?,?,?)',
                ('account', 'whale', 'a@chatroom',
                 json.dumps(['question_streak', 'two_part_rut', 'fast_opener_repeat', 'nagging_streak']),
                 time.time()),
            )
        prompt = svc.dynamic_prompt({'account': 'account', 'chat': 'a@chatroom'})
        self.assertIn('反问结尾', prompt)
        self.assertIn('先短反应再解释', prompt)
        self.assertIn('一样的开头起手', prompt)
        self.assertIn('催早点睡', prompt)


class ProactiveAndLifeAutonomyGatingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = proactive_fixture.ProactiveTests.asyncSetUp
    asyncTearDown = proactive_fixture.ProactiveTests.asyncTearDown
    message = proactive_fixture.ProactiveTests.message
    configure = proactive_fixture.ProactiveTests.configure

    async def test_proactive_still_runs_anti_formula_check_even_when_fishtalk_enabled(self):
        self.configure(fishtalk_enabled=True, chat_habits_enabled=True)
        with patch('whalegirl_linux.proactive.anti_formula_check', wraps=anti_formula_check) as spy:
            self.assertTrue(await self.service.run_once())
        self.assertEqual(spy.call_count, 1)

    async def test_life_autonomy_hidden_for_pat_and_casual_chat_but_present_for_tuning_intent(self):
        s = self.config.settings.model_copy(update={
            'enabled': True, 'life_autonomy_enabled': True,
            'growth_enabled': True, 'bilibili_enabled': True, 'tieba_enabled': True,
            'followup_wait_enabled': False, 'web_search_enabled': False, 'sticker_after_text': 'off',
        })
        self.configure(**s.model_dump())

        pat_ev = {**self.message(10), 'content': '群友 拍了拍你', 'trigger': 'pat', 'timestamp': time.time()}
        casual_ev1 = {**self.message(11), 'content': '还在玩游戏', 'trigger': 'direct', 'timestamp': time.time()}
        casual_ev2 = {**self.message(12), 'content': '你很可爱哦', 'trigger': 'direct', 'timestamp': time.time()}
        tune_ev = {**self.message(13), 'content': '把探索开放度调高一点吧', 'trigger': 'direct', 'timestamp': time.time()}

        self.assertFalse(LifeAutonomyService.should_offer_tools(pat_ev, [pat_ev], s))
        self.assertFalse(LifeAutonomyService.should_offer_tools(casual_ev1, [casual_ev1], s))
        self.assertFalse(LifeAutonomyService.should_offer_tools(casual_ev2, [casual_ev2], s))
        self.assertTrue(LifeAutonomyService.should_offer_tools(tune_ev, [tune_ev], s))

        # End-to-end tool exposure inside Runtime.process_next(): casual chat omits life_*; tuning exposes life_*.
        self.runtime.config.api_key = lambda: 'test-key'

        async def inspect_tools_for(ev):
            self.store.insert(ev)
            self.store.observe([ev])
            engine = await self.runtime.model()
            engine.api_style = 'openai'
            engine.api_url = 'https://api.deepseek.com/chat/completions'
            engine.stream_enabled = False
            seen_tool_names = []
            seen_system = []

            async def native(messages, tools, **kwargs):
                seen_tool_names.extend(t['function']['name'] for t in tools)
                seen_system.append(kwargs.get('system_prompt', ''))
                return {
                    'content': json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '收到啦'}]}, ensure_ascii=False),
                    'tool_calls': [],
                }

            engine.chat_with_tools = native
            await self.runtime.process_next()
            return seen_tool_names, '\n'.join(seen_system)

        casual_tools, casual_sys = await inspect_tools_for(casual_ev2)
        self.assertFalse(any(name.startswith('life_') for name in casual_tools), casual_tools)
        self.assertNotIn('life_inspect', casual_sys)

        tune_tools, tune_sys = await inspect_tools_for(tune_ev)
        self.assertTrue(any(name.startswith('life_') for name in tune_tools), tune_tools)


class MomentsAndBilibiliStabilityTests(unittest.IsolatedAsyncioTestCase):
    setUp = moments_fixture.MomentsTests.setUp
    tearDown = moments_fixture.MomentsTests.tearDown
    generation_fixture = moments_fixture.MomentsTests.generation_fixture
    cached_comments = moments_fixture.MomentsTests.cached_comments

    async def test_moments_tokens_timeout_stagger_and_overused_entity_suppression(self):
        # 1) publish without life_world passes max_tokens >= 2200 to engine_factory
        self.generation_fixture({'participate': True, 'text': '换个角度写今天路边的小猫', 'media_mode': 'none'}, global_memory=False)
        factory_kwargs = []
        orig_factory = self.runtime.engine_factory

        def spy_factory(**kwargs):
            factory_kwargs.append(kwargs)
            return orig_factory(**kwargs)

        self.runtime.engine_factory = spy_factory
        await self.service.generate('publish')
        self.assertGreaterEqual(factory_kwargs[-1]['max_tokens'], 2200)

        # 2) publish with unestablished life_world passes max_tokens >= 3000
        with patch.object(self.service, '_prepare_publish_world', return_value={
            'established': False, 'cast': [], 'recent_events_newest_first': [],
        }):
            await self.service.generate('publish')
        self.assertGreaterEqual(factory_kwargs[-1]['max_tokens'], 3000)

        # 3) reply_own passes max_tokens >= 1400
        target = {'id': '2', 'author': 'friend', 'name': '朋友', 'text': '笑死我了', 'created': 2}
        post = {'id': 'chosen', 'author': 'account', 'own': True, 'text': '测试动态', 'comments': [target]}
        await self.service.generate('reply_own', {'post': post, 'comment': target})
        self.assertGreaterEqual(factory_kwargs[-1]['max_tokens'], 1400)

        # 4) timeout/timed out recovery uses base=180 + deterministic stagger <= 1800
        s = self.runtime.config.settings
        self.service.record_generation_health(s, 'Request timed out after 90s')
        health = self.service.generation_health(s)
        delay = health['retry_at'] - time.time()
        self.assertEqual(health['failures'], 1)
        self.assertGreater(delay, 140)
        self.assertLessEqual(delay, 1800)

        # 5) overused concrete entities like '泡沫' across >=2 of recent 6 posts trigger overused_entities and filter continuations
        history = [
            {'state': 'sent', 'text': '阳台晒泡沫堆起来像雪山', 'topic': '洗泡沫', 'story_progress': '泡沫还在冒泡'},
            {'state': 'sent', 'text': '泡沫把洗碗池都占满了', 'topic': '泡泡危机', 'story_progress': '还要继续冲泡沫'},
            {'state': 'sent', 'text': '路边看见一只三花猫晒太阳', 'topic': '街角偶遇猫', 'story_progress': '明天再去看看它'},
        ]
        brief = story_brief(history, 'moment-test-id')
        overused_names = [item['entity'] for item in brief.get('overused_entities', [])]
        self.assertTrue(any('泡沫' in name for name in overused_names), overused_names)
        for cand in brief['continuation_candidates']:
            combined = ' '.join((cand.get('topic', ''), cand.get('hook', ''), cand.get('progress', '')))
            self.assertNotIn('泡沫', combined)


class BilibiliReplyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bili_fixture.ReplyWorkflowTests.asyncSetUp
    asyncTearDown = bili_fixture.ReplyWorkflowTests.asyncTearDown

    async def test_bilibili_reply_anti_symmetry_prompt_and_allows_non_bilibili_growth_busy(self):
        svc = self.reply_service
        now = int(time.time())
        enq = svc.enqueue_mention(
            '7701',
            {
                'id': 7701,
                'at_time': now - 5,
                'business_id': 1,
                'user': {'mid': int(bili_fixture.OWNER_UID), 'nickname': '主人'},
                'item': {
                    'uri': f'https://www.bilibili.com/video/{bili_fixture.BV}/?p=1',
                    'business_id': 1,
                    'subject_id': 777001,
                    'source_id': 8899,
                    'root_id': 8899,
                    'source_content': '@小鲸鱼 这段你怎么看',
                },
            },
            bili_fixture.BOT_UID,
        )
        self.assertTrue(enq['accepted'])

        captured_systems = []

        class CaptureEngine:
            model = 'test-model'

            async def chat(self, messages, **kwargs):
                captured_systems.append(kwargs.get('system_prompt', ''))
                return json.dumps({
                    'should_reply': True,
                    'reply': '中间吉他突然切进去那一下太爽了！',
                    'reason': '紧扣视频中段编曲接话',
                }, ensure_ascii=False)

            async def close(self):
                pass

        fake_ctx = {
            'bvid': bili_fixture.BV, 'oid': '777001', 'page': 1, 'part': 'P1',
            'title': '测试视频', 'author': 'UP主', 'tname': '音乐', 'desc': '简介', 'dynamic': '',
            'duration': 60, 'url': f'https://www.bilibili.com/video/{bili_fixture.BV}/?p=1',
            'windows': [{'start': 0, 'end': 60, 'subtitle': '测试字幕', 'danmaku': '', 'visual': ''}],
            'thread_replies': [], 'up_top_comments': [], 'hot_comments': [], 'summary': '视频总结',
        }

        # When growth.busy=True for conflicting 'bilibili' exploration, reply process_next yields cleanly.
        self.runtime.growth.busy = True
        self.runtime.growth.current_kind = 'bilibili'
        with patch.object(self.runtime.growth, 'worker_engine', return_value=CaptureEngine()), \
             patch.object(svc, 'deep_read_for_reply', new=AsyncMock(return_value=fake_ctx)):
            self.assertFalse(await svc.process_next())

        # When growth.busy=True for non-conflicting 'memory' consolidation, reply process_next proceeds.
        self.runtime.growth.current_kind = 'memory'
        with patch.object(self.runtime.growth, 'worker_engine', return_value=CaptureEngine()), \
             patch.object(svc, 'deep_read_for_reply', new=AsyncMock(return_value=fake_ctx)), \
             patch.object(svc, 'send_reply', new=AsyncMock(return_value=None)):
            self.assertTrue(await svc.process_next())

        self.runtime.growth.busy = False
        self.runtime.growth.current_kind = ''
        self.assertEqual(len(captured_systems), 1)
        self.assertIn('短反应', captured_systems[0])
        self.assertIn('空格', captured_systems[0])


if __name__ == '__main__':
    unittest.main()

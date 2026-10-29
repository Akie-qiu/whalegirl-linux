"""Search dispatch and a small live clock; fake delivery only."""
import copy
import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_chat_habits as habits
import test_proactive as proactive
from whalegirl_linux.search import DeepSeekSearch
from whalegirl_linux.upstream.ai_engine import AIEngine


class ClockTests(unittest.TestCase):
    def test_all_protocols_get_fresh_clock_without_mutating_or_changing_static_prefix(self):
        stamp = datetime(2026, 10, 29, 9, 45, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()
        for style in ('openai', 'responses', 'anthropic'):
            for vision in (False, True):
                with self.subTest(style=style, vision=vision):
                    text = '历史消息\n【本次需要回复的消息】\n[2026-10-29 09:44:00] 现在几点'
                    content = ([{'type':'text', 'text':text},
                                {'type':'image_url', 'image_url':{'url':'data:image/png;base64,AA=='}}]
                               if vision else text)
                    messages = [{'role':'user', 'content':content}]
                    original = copy.deepcopy(messages)
                    engine = AIEngine('https://example.test/v1', 'test', api_style=style)
                    system = '固定人设\n【本轮动态上下文】动态资料'
                    with patch('whalegirl_linux.time_context.time.time', return_value=stamp):
                        first = engine._build_payload(messages, system, None)
                    with patch('whalegirl_linux.time_context.time.time', return_value=stamp+65):
                        second = engine._build_payload(messages, system, None)
                    serialized = json.dumps(first, ensure_ascii=False)
                    self.assertIn('2026-10-29 09:45:12 周四 北京时间', serialized)
                    self.assertEqual(serialized.count('【服务器当前时间】'), 1)
                    self.assertIn('[2026-10-29 09:44:00]', serialized)
                    key = {'openai':'messages', 'responses':'input', 'anthropic':'messages'}[style]
                    self.assertIn('09:46:17', json.dumps(second[key], ensure_ascii=False))
                    system_key = {'openai':'messages', 'responses':'instructions', 'anthropic':'system'}[style]
                    a, b = first[system_key], second[system_key]
                    self.assertEqual(a[0] if style == 'openai' else a, b[0] if style == 'openai' else b)
                    self.assertEqual(messages, original)


class SearchDispatchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = habits.ChatHabitTests.asyncSetUp
    asyncTearDown = habits.ChatHabitTests.asyncTearDown
    event = habits.ChatHabitTests.event
    queue = habits.ChatHabitTests.queue

    async def test_real_search_function_is_visible_even_when_wait_tool_is_off(self):
        self.config.save(self.config.settings.model_copy(update={
            'web_search_enabled':True, 'followup_wait_enabled':False}))
        self.queue(self.event(content='联网查一下最新官方公告'))
        engine = await self.runtime.model()
        engine.api_style = 'openai'; engine.api_url = 'https://api.deepseek.com/chat/completions'
        engine.stream_enabled = False
        observed = []
        async def native(messages, tools, **options):
            observed.extend(t['function']['name'] for t in tools)
            return {'content':'', 'tool_calls':[{'id':'search1', 'function':{
                'name':'web_search', 'arguments':json.dumps({'query':'公开官方公告'})}}]}
        engine.chat_with_tools = native
        class Search:
            last = {}
            async def run(self, engine, query, settings):
                self.query = query
                return {'ok':True, 'text':'公告发布了新版本', 'documents':[], 'requests':1}
        search = self.runtime.web_search = Search()
        engine.answers = [{'should_reply':True, 'actions':[{'type':'text', 'text':'新版本发布啦'}]}]
        await self.runtime.process_next()
        self.assertEqual(set(observed), {'web_search', 'fetch_webpage'})
        self.assertEqual(search.query, '公开官方公告')
        self.assertEqual(self.gateway.sent, [('text', '新版本发布啦')])

    async def test_search_request_is_not_mistaken_for_silence(self):
        self.config.save(self.config.settings.model_copy(update={'web_search_enabled':True}))
        self.queue(self.event(content='帮我联网查一下公告'))
        class Search:
            last = {}
            async def run(self, *args):
                return {'ok':True, 'text':'公开事实', 'documents':[], 'requests':1}
        self.runtime.web_search = Search()
        engine = await self.runtime.model()
        engine.answers = [{'should_reply':False, 'search_query':'公开公告', 'actions':[]},
                          {'should_reply':True, 'actions':[{'type':'text','text':'查到了'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 2)
        self.assertTrue(self.runtime.last_timing['web_search']['ok'])
        self.assertEqual(self.gateway.sent, [('text','查到了')])

    async def test_wait_resume_can_search_without_waiting_again(self):
        self.config.save(self.config.settings.model_copy(update={
            'web_search_enabled':True, 'followup_wait_enabled':True}))
        first = self.event(); self.queue(first)
        engine = await self.runtime.model()
        engine.api_style = 'openai'; engine.api_url = 'https://api.deepseek.com/chat/completions'
        engine.stream_enabled = False
        calls = []
        assistant = {'role':'assistant', 'content':None, 'tool_calls':[{
            'id':'wait1', 'type':'function', 'function':{'name':'wait_for_next_message','arguments':'{}'}}]}
        async def native(messages, tools, **options):
            calls.append((messages, options))
            if len(calls) == 1:
                return {'content':'','tool_calls':assistant['tool_calls'], 'assistant_message':assistant}
            return {'content':'', 'tool_calls':[{'id':'search1', 'function':{
                'name':'web_search', 'arguments':json.dumps({'query':'公开公告'})}}]}
        engine.chat_with_tools = native
        class Search:
            last = {}
            async def run(self, *args):
                return {'ok':True,'text':'新公告', 'documents':[], 'requests':1}
        self.runtime.web_search = Search()
        await self.runtime.process_next()
        self.assertEqual(self.store.get(first['key'])['state'], 'waiting')
        self.queue(self.event(2, content='帮我搜一下最新公告'))
        await asyncio.sleep(.25)
        engine.answers = [{'should_reply':True,'actions':[{'type':'text','text':'新公告来了'}]}]
        await self.runtime.process_next()
        self.assertEqual(calls[1][1]['tool_choice'], 'auto')
        self.assertEqual(calls[1][0][-1]['tool_call_id'], 'wait1')
        self.assertEqual(self.gateway.sent, [('text','新公告来了')])
        self.assertFalse(self.runtime.followup_waits)


class ProactiveSearchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = proactive.ProactiveTests.asyncSetUp
    asyncTearDown = proactive.ProactiveTests.asyncTearDown
    message = proactive.ProactiveTests.message
    configure = proactive.ProactiveTests.configure

    async def test_proactive_search_is_resolved_and_messages_have_readable_times(self):
        self.configure(web_search_enabled=True)
        calls = []
        class Search:
            last = {}
            async def run(self, *args):
                return {'ok':True, 'text':'公开新公告', 'documents':[], 'requests':1}
        self.runtime.web_search = Search()
        answers = iter([{'should_reply':False, 'search_query':'公开新公告', 'actions':[]},
                        {'should_reply':True, 'actions':[{'type':'text','text':'有个新公告'}]}])
        class Engine:
            last_error = ''
            async def chat(self, messages, **options):
                calls.append((messages, options))
                return json.dumps(next(answers), ensure_ascii=False)
            async def close(self):pass
        self.runtime.engine_factory = lambda **kwargs: Engine()
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls), 2)
        data = json.JSONDecoder().raw_decode(calls[0][0][0]['content'])[0]
        self.assertRegex(data['recent_messages'][0]['timestamp'], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
        self.assertIn('search_query', calls[0][1]['system_prompt'])
        self.assertEqual(self.gateway.sent, [(proactive.CHAT,'有个新公告')])


if __name__ == '__main__':
    unittest.main()

"""Keep one-call direct answers and truthful timing across the wait tool."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock

import test_chat_habits as habits


class ReplyPhase3Tests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_complete_direct_plain_answer_needs_only_one_model_call(self):
        first=self.event();self.queue(first)
        engine=await self.runtime.model()
        engine.chat=AsyncMock(return_value='收到啦，今天也辛苦了')
        await self.runtime.process_next()
        engine.chat.assert_awaited_once()
        self.assertEqual(self.gateway.sent,[('text','收到啦，今天也辛苦了')])
        self.assertEqual(self.store.get(first['key'])['state'],'sent')
        self.assertEqual(self.runtime.last_timing['format_recovery'],'plain_text_local')
        self.assertNotIn('format_repair_attempts',self.runtime.last_timing)
        self.assertEqual(self.runtime.last_timing['receive_time_source'],'first_received')

    async def test_natural_plain_answer_still_requires_participation_decision(self):
        first=self.event(content='大家最近都在玩什么呀',trigger='natural');self.queue(first)
        engine=await self.runtime.model()
        engine.chat=AsyncMock(side_effect=['我也来看看',json.dumps({'should_reply':False,'actions':[]})])
        await self.runtime.process_next()
        self.assertEqual(engine.chat.await_count,2)
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.store.get(first['key'])['state'],'ignored')

    async def test_wait_resume_keeps_first_receipt_and_both_model_durations(self):
        first=self.event(content='我先说第一个条件');self.queue(first)
        received_at=self.store.get(first['key'])['received_at']
        engine=await self.runtime.model();engine.api_style='openai';engine.stream_enabled=False
        calls=[]

        async def respond(messages,tools,**options):
            calls.append(options)
            await asyncio.sleep(.025)
            if len(calls)==1:
                tool_calls=[{'id':'wait','type':'function','function':{
                    'name':'wait_for_next_message','arguments':'{"reason":"条件还未说完"}'}}]
                return {'content':'','tool_calls':tool_calls,
                    'assistant_message':{'role':'assistant','content':None,'tool_calls':tool_calls}}
            return {'content':json.dumps({'should_reply':True,'actions':[{'type':'text','text':'听完啦'}]}),
                    'tool_calls':[]}

        engine.chat_with_tools=respond
        await self.runtime.process_next()
        first_metrics=json.loads(self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',
            (first['key'],)).fetchone()[0])
        self.assertGreater(first_metrics['model_seconds'],.02)
        self.queue(self.event(2,content='第二个条件是少冰'))
        await asyncio.sleep(.2)
        await self.runtime.process_next()
        timing=self.runtime.last_timing
        self.assertEqual(timing['followup_wait']['resumed'],'received')
        self.assertEqual(timing['received_at'],received_at)
        self.assertEqual(timing['inbox_delay_seconds'],first_metrics['inbox_delay_seconds'])
        self.assertEqual(timing['detected_after_seconds'],first_metrics['detected_after_seconds'])
        self.assertEqual(timing['prior_model_seconds'],first_metrics['model_seconds'])
        self.assertGreaterEqual(timing['model_seconds'],timing['prior_model_seconds']+.02)
        self.assertEqual(self.gateway.sent,[('text','听完啦')])

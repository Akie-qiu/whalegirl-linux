"""Regressions for a library miss followed by malformed explanation JSON."""
import json
import unittest
from unittest.mock import patch

from whalegirl_linux.model_reply import guarded_chat, ToolReplyFormatError
import test_chat_habits as habits


class RawEngine:
    last_error=''
    def __init__(self,answers):self.answers=list(answers);self.calls=[]
    async def chat(self,messages,**kwargs):
        self.calls.append((messages,kwargs));return self.answers.pop(0)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_followup_does_not_add_a_repair_call(self):
        value={'should_reply':True,'actions':[{'type':'text','text':'找到啦'}]}
        engine=RawEngine([json.dumps(value)]);diagnostics={}
        result=await guarded_chat(engine,[],diagnostics=diagnostics,stage='image_result',max_tokens=300)
        self.assertEqual(json.loads(result),value);self.assertEqual(len(engine.calls),1)
        self.assertNotIn('format_repair_attempts',diagnostics)

    async def test_shared_recovery_budget_is_one_per_turn(self):
        valid=json.dumps({'should_reply':True,'actions':[{'type':'text','text':'好呀'}]})
        engine=RawEngine(['not json',valid,'another malformed reply']);diagnostics={}
        await guarded_chat(engine,[],diagnostics=diagnostics,stage='tieba_result',max_tokens=300)
        with self.assertRaises(ToolReplyFormatError):
            await guarded_chat(engine,[],diagnostics=diagnostics,stage='memory_result',max_tokens=300)
        self.assertEqual(len(engine.calls),3);self.assertEqual(diagnostics['format_repair_attempts'],1)


class ToolReplyTurnTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_tieba_followup_recovers_without_repeating_read(self):
        first=self.event();diagnostics={}
        valid=json.dumps({'should_reply':True,'actions':[{'type':'text','text':'原来是这样'}]})
        engine=RawEngine(['not json',valid])
        with patch.object(self.runtime.tieba.tools,'search',return_value={'ok':True,'memes':[]}) as search:
            result=await self.runtime.tieba.tools.resolve(engine,[],'',
                {'should_reply':True,'tieba_request':{'tool':'search_tieba','query':'测试资料'}},
                first,self.config.generation,diagnostics,300,format_diagnostics=diagnostics)
        self.assertEqual(search.call_count,1);self.assertEqual(len(engine.calls),2)
        self.assertEqual(result['actions'][0]['text'],'原来是这样')
        self.assertEqual(diagnostics['format_repair_attempts'],1)

    async def image_turn(self,repair):
        first=self.event(content='发几张好笑的图');self.queue(first)
        raw=RawEngine([json.dumps({'should_reply':True,'actions':[],
            'image_request':{'tool':'send_library_image','asset_id':''}}),'图库里暂时没有合适的',repair])
        engine=await self.runtime.model();engine.chat=raw.chat
        with patch.object(self.runtime.images.tools,'direct_library_fallback',return_value=None),\
             patch.object(self.runtime.images.tools,'execute',return_value={'decision':'send_library_image','ok':False,'code':'no_match'}) as execute:
            await self.runtime.process_next()
            self.assertEqual(execute.call_count,1)
        return self.store.get(first['key']),raw

    async def test_library_miss_recovers_explanation_without_resubmitting_tool(self):
        valid=json.dumps({'should_reply':True,'actions':[{'type':'text','text':'图库里暂时没找到合适的'}],
                          'image_request':None})
        row,engine=await self.image_turn(valid)
        self.assertEqual(row['state'],'sent');self.assertEqual(len(engine.calls),3)
        self.assertEqual(self.gateway.sent,[('text','图库里暂时没找到合适的')])
        self.assertEqual(self.store.db.execute('select count(*) from picture_jobs').fetchone()[0],0)
        self.assertEqual(self.runtime.last_timing['format_error']['stage'],'image_result')

    async def test_failed_explanation_repair_is_logged_at_precise_stage(self):
        row,engine=await self.image_turn('{still invalid')
        self.assertEqual(row['state'],'failed');self.assertEqual(len(engine.calls),3)
        self.assertIn('image_result/',row['error']);self.assertEqual(self.gateway.sent,[])
        self.assertFalse(self.runtime.paused_reason)
        metrics=json.loads(self.store.db.execute('select metrics from reply_metrics where key=?',(row['key'],)).fetchone()[0])
        self.assertEqual(metrics['format_repair_attempts'],1)
        self.assertEqual(metrics['image_tool']['code'],'no_match')

    async def test_memory_operation_runs_once_when_its_explanation_is_repaired(self):
        first=self.event(content='记住我喜欢猫');self.queue(first)
        raw=RawEngine([json.dumps({'should_reply':True,'actions':[],
            'memory_ops':[{'op':'create','content':'喜欢猫'}]}),'记住啦',
            json.dumps({'should_reply':True,'actions':[{'type':'text','text':'记住啦'}]})])
        engine=await self.runtime.model();engine.chat=raw.chat
        with patch.object(self.runtime.memory_book,'model_ops',return_value=[{'op':'create','ok':True}]) as memory:
            await self.runtime.process_next()
        self.assertEqual(memory.call_count,1);self.assertEqual(len(raw.calls),3)
        self.assertEqual(self.store.get(first['key'])['state'],'sent')

"""The wait-tool path must keep JSON replies and recover pictured-chat format errors."""
import json
import unittest
from unittest.mock import patch

from whalegirl_linux.runtime import exception_location
from whalegirl_linux.stickers import parse_json
import test_chat_habits as habits


class ModelFormatGuardTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def run_image_turn(self,tool_content,repair=None):
        first=self.event();self.queue(first)
        engine=await self.runtime.model();engine.api_style='openai';engine.stream_enabled=False
        options=[]
        async def tool(messages,tools,**kwargs):
            options.append(kwargs)
            return {'content':tool_content,'tool_calls':[]}
        engine.chat_with_tools=tool
        if repair is not None:
            async def repair_call(*args,**kwargs):
                engine.calls+=1
                return repair
            engine.chat=repair_call
        built=([{'role':'user','content':'a conversation with an attached image'}],['photo-key'],
               {'current_messages':40,'selected_history_messages':39,'images':1})
        with patch('whalegirl_linux.runtime.build_context',return_value=built):
            await self.runtime.process_next()
        return self.store.get(first['key']),engine,options

    async def test_wait_tool_requests_json_without_extra_normal_call(self):
        valid=json.dumps({'should_reply':True,'actions':[{'type':'text','text':'好呀'}]})
        row,engine,options=await self.run_image_turn(valid)
        self.assertEqual(options[0]['response_format'],{'type':'json_object'})
        self.assertEqual(row['state'],'sent');self.assertEqual(engine.calls,0)

    async def test_pictured_plain_reply_is_repaired_instead_of_decoder_exception(self):
        valid=json.dumps({'should_reply':True,'actions':[{'type':'text','text':'好呀'}]})
        row,engine,_=await self.run_image_turn('好呀',valid)
        self.assertEqual(row['state'],'sent');self.assertEqual(engine.calls,1)
        self.assertEqual(self.runtime.last_timing['format_recovery'],'format_retry')
        self.assertEqual(self.runtime.last_timing['format_error']['images'],1)
        self.assertFalse(self.runtime.paused_reason)

    async def test_unrecoverable_image_format_is_logged_without_raw_exception(self):
        row,engine,_=await self.run_image_turn('{invalid','{still invalid')
        self.assertEqual(row['state'],'failed')
        self.assertIn('图片回复格式恢复失败',row['error'])
        self.assertNotIn('处理异常',row['error']);self.assertEqual(engine.calls,1)
        self.assertFalse(self.runtime.paused_reason)


class ErrorSourceTests(unittest.TestCase):
    def test_json_error_points_to_project_parser_without_private_content(self):
        try:parse_json('private response that is not json')
        except ValueError as exc:location=exception_location(exc)
        self.assertIn('stickers.py:parse_json:',location)
        self.assertNotIn('private response',location)
        self.assertNotIn('decoder.py',location)

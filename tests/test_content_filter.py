"""Provider rejection stays terminal and never changes role prompts or sends."""
import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx

import test_chat_habits as habits
from whalegirl_linux.model_reply import guarded_chat
from whalegirl_linux.time_context import CLOCK_RULE
from whalegirl_linux.upstream.ai_engine import (
    AIEngine, ContentFilteredResponse, content_filter_error,
)


PROMPT = '管理员保留的角色设定，测试中保持原样。'
TOOLS = [{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}]


def response(reason='content_filter', content='{"should_reply":true,"actions":[]}', *, calls=()):
    return {'choices':[{'finish_reason':reason,'message':{
        'role':'assistant','content':content,'tool_calls':list(calls)}}],
        'usage':{'prompt_tokens':17,'completion_tokens':3,'total_tokens':20}}


class EngineFilterTests(unittest.IsolatedAsyncioTestCase):
    def engine(self, body, *, stream=False):
        self.requests=[];self.events=[]
        engine=AIEngine('https://example.test/v1','synthetic',model='synthetic',
                        api_style='openai',max_retries=2,stream_enabled=stream)
        def handle(request):
            self.requests.append(json.loads(request.content))
            if isinstance(body,str):
                return httpx.Response(200,text=body,headers={'Content-Type':'text/event-stream'})
            return httpx.Response(200,json=body)
        engine._clients[id(asyncio.get_running_loop())]=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        engine.usage_callback=self.events.append
        return engine

    async def test_plain_and_tool_filter_discard_even_complete_looking_actions(self):
        call={'id':'blocked','function':{'name':'lookup','arguments':'{}'}}
        for tools in (False,True):
            with self.subTest(tools=tools):
                engine=self.engine(response(calls=[call]))
                try:
                    if tools:
                        answer=await engine.chat_with_tools([],TOOLS,system_prompt=PROMPT)
                        self.assertEqual(answer['tool_calls'],[])
                        self.assertFalse(answer['content'])
                    else:
                        self.assertEqual(await engine.chat([],system_prompt=PROMPT),'')
                    self.assertEqual(len(self.requests),1)
                    self.assertEqual(self.requests[0]['messages'][0]['content'],PROMPT+CLOCK_RULE)
                    self.assertEqual(engine.last_response_meta['error_code'],'content_filter')
                    self.assertIs(engine.last_response_meta['retryable'],False)
                    self.assertIn('模型服务内容审核拦截',engine.last_error)
                    self.assertNotIn('工具调用未完成',engine.last_error)
                    self.assertEqual(self.events[0]['status'],'error')
                    self.assertEqual(self.events[0]['total_tokens'],20)
                finally:
                    await engine.close()

    async def test_missing_filter_usage_stays_unknown(self):
        body=response();body['usage']=None
        engine=self.engine(body)
        try:
            self.assertEqual(await engine.chat([],system_prompt=PROMPT),'')
            self.assertIsNone(self.events[0]['total_tokens'])
            self.assertEqual(len(self.events),1)
        finally:
            await engine.close()

    async def test_stream_filter_has_same_terminal_diagnostic(self):
        body='data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'content_filter'}]})+'\n\ndata: [DONE]\n\n'
        engine=self.engine(body,stream=True)
        try:
            self.assertEqual(await engine.chat([],system_prompt=PROMPT),'')
            self.assertEqual(len(self.requests),1)
            self.assertEqual(engine.last_response_meta['error_code'],'content_filter')
            self.assertEqual(self.events[0]['status'],'error')
        finally:
            await engine.close()

    async def test_connection_test_preserves_actual_filter_cause(self):
        engine=self.engine(response())
        try:
            ok,reason=await engine.test_connection()
            self.assertFalse(ok)
            self.assertIn('content_filter',reason)
            self.assertEqual(len(self.requests),1)
        finally:
            await engine.close()


class FollowupFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_followup_filter_does_not_trigger_format_repair(self):
        requests=[]
        async def chat(*args,**kwargs):
            requests.append(kwargs)
            return ''
        engine=SimpleNamespace(chat=chat,last_error='模型回复未完成：content_filter')
        diagnostics={}
        with self.assertRaises(ContentFilteredResponse) as failure:
            await guarded_chat(engine,[],diagnostics=diagnostics,stage='search_result',system_prompt=PROMPT)
        self.assertEqual(failure.exception.stage,'search_result')
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['system_prompt'],PROMPT)
        self.assertNotIn('format_repair_attempts',diagnostics)
        self.assertEqual(diagnostics['model_blocked']['code'],'content_filter')

    async def test_filter_during_format_repair_keeps_provider_cause(self):
        requests=[]
        async def chat(*args,**kwargs):
            requests.append(kwargs)
            if len(requests)==1:
                return '{bad'
            engine.last_error='content_filter'
            return ''
        engine=SimpleNamespace(chat=chat,last_error='')
        diagnostics={}
        with self.assertRaises(ContentFilteredResponse):
            await guarded_chat(engine,[],diagnostics=diagnostics,stage='image_result',system_prompt=PROMPT)
        self.assertEqual(len(requests),2)
        self.assertTrue(all(item['system_prompt']==PROMPT for item in requests))
        self.assertIs(diagnostics['tool_reply_stages'][0]['repair_ok'],False)
        self.assertEqual(diagnostics['model_blocked']['stage'],'image_result')


class RuntimeFilterTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_blocked_initial_request_does_not_pause_next_message(self):
        first=self.event();self.queue(first)
        engine=await self.runtime.model();requests=[]
        async def chat(*args,**kwargs):
            requests.append(kwargs)
            if len(requests)==1:
                engine.last_error='模型工具调用未完成：content_filter'
                engine.last_response_meta={'finish_reason':'content_filter'}
                return ''
            engine.last_error=''
            engine.last_response_meta={'finish_reason':'stop'}
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'早呀'}]})
        engine.chat=chat
        await self.runtime.process_next()
        row=self.store.get(first['key'])
        self.assertEqual(row['state'],'failed')
        self.assertIn('模型服务内容审核拦截',row['error'])
        metrics=json.loads(self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(first['key'],)).fetchone()[0])
        self.assertEqual(metrics['model_blocked']['stage'],'initial')
        self.assertFalse(self.gateway.sent)
        self.assertFalse(self.runtime.paused_reason)
        self.runtime.processing='';self.runtime.processing_key=''
        second=self.event(2);self.queue(second)
        await self.runtime.process_next()
        self.assertEqual(self.store.get(second['key'])['state'],'sent')
        self.assertEqual(self.store.get(first['key'])['state'],'failed')
        self.assertEqual(len(requests),2)

    async def test_filter_in_search_followup_never_sends_stale_initial_action(self):
        self.config.save(self.config.settings.model_copy(update={'web_search_enabled':True}))
        first=self.event();self.queue(first)
        engine=await self.runtime.model();requests=[];searches=[]
        async def chat(*args,**kwargs):
            requests.append(kwargs)
            if len(requests)==1:
                return json.dumps({'should_reply':True,'search_query':'synthetic query',
                    'actions':[{'type':'text','text':'准备查一下'}]})
            engine.last_error='content_filter'
            engine.last_response_meta={'finish_reason':'content_filter'}
            return ''
        async def search(*args,**kwargs):
            searches.append(True)
            return {'ok':True,'documents':[],'text':'synthetic fact'}
        engine.chat=chat;self.runtime.web_search.run=search
        await self.runtime.process_next()
        row=self.store.get(first['key'])
        self.assertEqual(row['state'],'failed')
        self.assertIn('content_filter',row['error'])
        self.assertNotIn('格式无效',row['error'])
        self.assertEqual(len(searches),1)
        self.assertEqual(len(requests),2)
        self.assertFalse(self.gateway.sent)
        self.assertFalse(self.runtime.paused_reason)
        metrics=json.loads(self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(first['key'],)).fetchone()[0])
        self.assertEqual(metrics['model_blocked']['stage'],'search_result')
        self.assertNotIn('format_repair_attempts',metrics)

    async def test_filter_during_parse_repair_is_not_reported_as_json_error(self):
        first=self.event();self.queue(first)
        engine=await self.runtime.model();requests=[]
        async def chat(*args,**kwargs):
            requests.append(kwargs)
            if len(requests)==1:
                return '{bad'
            engine.last_error='content_filter'
            return ''
        engine.chat=chat
        await self.runtime.process_next()
        row=self.store.get(first['key'])
        self.assertEqual(row['state'],'failed')
        self.assertIn('content_filter',row['error'])
        self.assertNotIn('格式无效',row['error'])
        self.assertEqual(len(requests),2)
        self.assertFalse(self.gateway.sent)


if __name__=='__main__':
    unittest.main()

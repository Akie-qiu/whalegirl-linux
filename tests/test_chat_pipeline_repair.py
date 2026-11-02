"""Observed late-photo, native-tool and durable-share regressions. No real sends."""
import asyncio
import json
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import httpx

from whalegirl_linux.context import build_context
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.settings import Settings
from whalegirl_linux.tieba import share_window,TZ
from whalegirl_linux.upstream.ai_engine import AIEngine
import test_incoming_image_wait as photos
import test_chat_habits as habits


class PhotoContextTests(photos.IncomingImageContextTests):
    async def test_provisional_inbox_key_attaches_canonical_photo_once(self):
        photo=self.message(1,kind=3)
        provisional={**photo,'key':'account/a@chatroom/local:1'}
        self.store.observe([provisional])
        self.store.cache_image(photo['key'],photos.photo_media())
        _,keys,counts=await build_context(self.store,None,Settings(),provisional,[provisional],{})
        self.assertEqual(keys,[photo['key']]);self.assertEqual(counts['images'],1)

    async def test_initiative_reads_a_recent_photo_without_navigation(self):
        photo=self.message(1,kind=3);calls=[]
        class Gateway:
            async def media(self,*args,**kwargs):calls.append(kwargs);return photos.photo_media()
        _,keys,counts=await build_context(self.store,Gateway(),Settings(),photo,[],{},initiative=True)
        self.assertEqual(keys,[photo['key']]);self.assertFalse(calls[0]['navigate'])
        self.assertEqual(counts['pending_images'],0)

    async def test_initiative_does_not_silently_drop_a_loading_photo(self):
        photo=self.message(1,kind=3)
        class Gateway:
            async def media(self,*args,**kwargs):return {}
            async def visible_media(self,*args,**kwargs):raise AssertionError('No proactive scrolling')
        _,keys,counts=await build_context(self.store,Gateway(),Settings(),photo,[],{},initiative=True)
        self.assertFalse(keys);self.assertEqual(counts['pending_images'],1)


class EmptyResponseTests(unittest.IsolatedAsyncioTestCase):
    async def run_engine(self,responses,*,tools=True):
        requests=[];events=[];remaining=iter(responses)
        engine=AIEngine('https://example.test/v1','unused',api_style='openai',max_retries=2)
        def respond(request):requests.append(json.loads(request.content));return httpx.Response(200,json=next(remaining))
        engine._clients[id(asyncio.get_running_loop())]=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        engine.usage_callback=events.append
        try:
            args=([{'role':'user','content':'你好'}],)
            if tools:
                result=await engine.chat_with_tools(*args,[{'type':'function','function':{
                    'name':'web_search','parameters':{'type':'object','properties':{}}}}])
            else:result=await engine.chat(*args)
            return result,requests,events,engine.last_error,engine.last_response_meta
        finally:await engine.close()

    @staticmethod
    def response(content='',calls=None,finish='stop'):
        return {'choices':[{'finish_reason':finish,'message':{
            'role':'assistant','content':content,'tool_calls':calls or []}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2}}

    async def test_empty_native_result_retries_once_and_accounts_for_both(self):
        result,requests,events,error,meta=await self.run_engine([
            self.response(),self.response('{"should_reply":false,"actions":[]}')])
        self.assertTrue(result['content']);self.assertEqual(len(requests),2)
        self.assertEqual(requests[1]['tool_choice'],'none');self.assertEqual(error,'')
        self.assertEqual([e['status'] for e in events],['error','success'])
        self.assertEqual(meta['attempts'],2)

    async def test_unoffered_tool_is_never_run_or_promoted_to_success(self):
        call={'id':'bad','type':'function','function':{'name':'invented_tool','arguments':'{}'}}
        result,requests,events,error,meta=await self.run_engine([
            self.response(calls=[call],finish='tool_calls'),self.response('{"actions":[]}')])
        self.assertEqual(result['tool_calls'],[]);self.assertEqual(len(requests),2)
        self.assertIn('未提供',meta['recovered_from']);self.assertEqual(error,'')

    async def test_genuine_tool_only_result_is_success_without_retry(self):
        call={'id':'ok','type':'function','function':{'name':'web_search','arguments':'{"query":"天气"}'}}
        result,requests,events,error,meta=await self.run_engine([self.response(calls=[call],finish='tool_calls')])
        self.assertEqual(result['tool_calls'],[call]);self.assertEqual(len(requests),1)

    async def test_partial_tool_plan_is_discarded_and_recomputed_once(self):
        result,requests,events,error,meta=await self.run_engine([
            self.response('{"actions":[',finish='length'), self.response('{"actions":[]}')])
        self.assertEqual(result['content'],'{"actions":[]}');self.assertEqual(error,'');self.assertEqual(len(requests),2)
        self.assertGreater(requests[1]['max_tokens'],requests[0]['max_tokens'])
        self.assertEqual(requests[1]['messages'],requests[0]['messages'])

    async def test_two_empty_results_stop_with_an_actionable_error(self):
        result,requests,events,error,meta=await self.run_engine([self.response(),self.response()])
        self.assertFalse(result['content']);self.assertEqual(len(requests),2)
        self.assertIn('空内容',error);self.assertTrue(all(e['status']=='error' for e in events))

    async def test_plain_chat_empty_is_not_recorded_as_success(self):
        result,requests,events,error,meta=await self.run_engine([self.response(),self.response('你好')],tools=False)
        self.assertEqual(result,'你好');self.assertEqual([e['status'] for e in events],['error','success'])

    async def test_typed_text_content_is_preserved(self):
        result,requests,events,error,meta=await self.run_engine([self.response([{'type':'text','text':'你好'}])])
        self.assertEqual(result['content'],'你好');self.assertEqual(len(requests),1)


class WaitResumeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_repeated_wait_gets_a_final_reply_instead_of_empty_failure(self):
        first=self.event();self.queue(first);engine=await self.runtime.model()
        engine.api_style='openai';engine.stream_enabled=False
        call={'id':'wait','type':'function','function':{'name':'wait_for_next_message','arguments':'{}'}}
        self.runtime.followup_ready[first['key']]={'reason':'timeout','seconds':10,
            'generation':self.config.generation,'batch_keys':[first['key']],
            'assistant_message':{'role':'assistant','content':None,'tool_calls':[call]}}
        offered=[]
        async def tools(messages,definitions,**kwargs):
            offered.extend(t['function']['name'] for t in definitions)
            return {'content':'','tool_calls':[call]}
        engine.chat_with_tools=tools
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'那我先接着说啦'}]}]
        await self.runtime.process_next()
        self.assertNotIn('wait_for_next_message',offered)
        self.assertEqual(self.store.get(first['key'])['state'],'sent')


class ShareQueueTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown=habits.ChatHabitTests.asyncTearDown

    async def asyncSetUp(self):
        await habits.ChatHabitTests.asyncSetUp(self)
        self.config.save(self.config.settings.model_copy(update={
            'tieba_enabled':True,'tieba_start_hour':0,'tieba_end_hour':0,
            'allowed_chat_ids':['a@chatroom','b@chatroom'],'tieba_share_chat_ids':['a@chatroom'],
            'tieba_images_enabled':False}))
        self.b=self.runtime.tieba;self.sent=[]
        class Reader:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def threads(self,forum):
                return [{'tid':'123','forum':forum,'title':'摸鱼新发现','text':'摸鱼是物种优势',
                         'url':'https://tieba.baidu.com/p/123','images':[]}]
            async def detail(self,thread):return {**thread,'posts':[]}
        class Engine:
            async def chat(self,*args,**kwargs):
                return json.dumps({'posts':[{'tid':'123','score':90,'reason':'有趣',
                    'share_text':'这个挺有意思','image_ids':[],'memes':[]}]})
            async def close(self):pass
        self.b.reader_factory=lambda credentials:Reader()
        self.b.new_engine=lambda account:Engine()
        async def status():return {'status':'logged_in','loggedInUser':'account'}
        async def send(chat,text,**kwargs):
            self.sent.append((chat,text));return Receipt(True,'987')
        self.gateway.status_for_send=status;self.gateway.send=send

    def newer_post(self,tid='456'):
        post=dict(self.store.db.execute('SELECT * FROM tieba_posts LIMIT 1').fetchone())
        post.update(tid=tid,url='https://tieba.baidu.com/p/'+tid,seen=time.time()+1)
        self.b.retention.add(tid)
        return post

    async def test_night_queue_survives_until_real_send_window(self):
        await self.b.scan();self.config.settings.tieba_start_hour=9;self.config.settings.tieba_end_hour=23
        created=datetime(2026,10,30,0,30,tzinfo=TZ).timestamp()
        now=datetime(2026,10,30,9,5,tzinfo=TZ).timestamp()
        self.b.execute('UPDATE tieba_shares SET created=?',(created,))
        first,expires=share_window(self.config.settings,created)
        self.assertEqual(datetime.fromtimestamp(first,TZ).hour,9);self.assertGreater(expires,now)
        with patch('whalegirl_linux.tieba.time.time',return_value=now):
            self.assertTrue(await self.b.dispatch_once())
        self.assertEqual(len(self.sent),1)

    async def test_an_old_uncertain_post_does_not_block_a_different_post_forever(self):
        await self.b.scan()
        self.b.execute("UPDATE tieba_shares SET state='uncertain',sent_at=?,created=?",(time.time()-10800,time.time()-10800))
        self.assertEqual(self.b.queue_share(self.newer_post()),1)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tieba_shares WHERE state='uncertain'").fetchone()[0],1)
        self.assertFalse(self.sent)

    async def test_replace_only_unsent_old_candidate_without_deleting_it(self):
        await self.b.scan();self.assertEqual(self.b.queue_share(self.newer_post()),1)
        rows=list(self.store.db.execute('SELECT tid,state FROM tieba_shares ORDER BY created'))
        self.assertEqual([(r['tid'],r['state']) for r in rows],[('123','cancelled'),('456','queued')])

    async def test_console_reports_limits_instead_of_generic_wait(self):
        await self.b.scan();row=dict(self.store.db.execute('SELECT * FROM tieba_shares').fetchone())
        self.config.settings.tieba_share_per_day=0
        self.assertEqual(self.b.share_wait(row)['code'],'daily_limit')
        self.assertIn('waiting',self.b.snapshot()['shares'][0])

import asyncio
import json
import unittest

import httpx

from whalegirl_linux.search import DeepSeekSearch, strip_search_citations
from whalegirl_linux.settings import Settings
from whalegirl_linux.conversation import plan_actions
from test_chat_habits import ChatHabitTests


class SearchApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_search_endpoint_and_body(self):
        seen=[]
        def handler(request):
            seen.append((str(request.url),json.loads(request.content)))
            return httpx.Response(200,json={'results':[{'title':'官方公告','url':'https://example.com/news',
                'date':'2026-10-21','summary':'摘要','content':'公开事实'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            class Engine:
                api_style='openai';api_url='https://api.deepseek.com/chat/completions'
                async def _get_client(self):return client
            result=await DeepSeekSearch().run(Engine(),'查询公开公告',Settings(web_search_enabled=True))
        self.assertTrue(result['ok']);self.assertEqual(result['requests'],1)
        self.assertEqual(seen,[('https://api.deepseek.com/v1/tools/search',{'query':'查询公开公告','count':5,'need_content':True})])
        self.assertIn('公开事实',result['text'])

    async def test_failure_is_not_retried_or_misreported_as_success(self):
        calls=[]
        def handler(request):calls.append(1);return httpx.Response(403,json={'error':'not available'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            class Engine:
                api_style='openai';api_url='https://api.deepseek.com/chat/completions'
                async def _get_client(self):return client
            result=await DeepSeekSearch().run(Engine(),'查询公开公告',Settings(web_search_enabled=True))
        self.assertFalse(result['ok']);self.assertEqual(len(calls),1);self.assertIn('403',result['error'])

    async def test_citations_removed_and_web_reply_stays_plain(self):
        text='查到了，今天发布%cite%1%/cite%\n可以看看[官方介绍](https://example.com)[1]\n参考资料：\nhttps://example.com'
        cleaned=strip_search_citations(text)
        self.assertNotIn('%cite',cleaned);self.assertNotIn('[1]',cleaned);self.assertNotIn('https://',cleaned)
        self.assertIn('官方介绍',cleaned)
        actions=plan_actions({'actions':[{'type':'text','text':text}]},{},Settings())
        self.assertEqual(actions[0]['text'],cleaned)


class SearchRuntimeTests(ChatHabitTests):
    async def test_search_tool_runs_between_fast_model_turns_without_quotes(self):
        self.config.save(self.config.settings.model_copy(update={'web_search_enabled':True}))
        self.queue(self.event(content='鲸鱼娘 联网查一下今天的新公告'))
        class Search:
            last={}
            async def run(self,engine,query,settings):
                self.query=query
                return {'ok':True,'text':'今天发布了一个新公告','requests':1,'seconds':.1,'documents':[]}
        self.runtime.web_search=Search();engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'search_query':'最新官方公告','actions':[]},
            {'should_reply':True,'actions':[{'type':'text','text':'今天有新公告%cite%1%/cite%','quote_local_id':1}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,2);self.assertEqual(self.gateway.sent,[('text','今天有新公告')])
        self.assertTrue(self.runtime.last_timing['web_search']['ok'])

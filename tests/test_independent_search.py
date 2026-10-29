"""Search credentials stay official while the unchanged chat model uses a relay."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import httpx

import test_core as core
import test_chat_habits as habits
from whalegirl_linux.search import DeepSeekSearch
from whalegirl_linux.settings import Config, Settings


class IndependentTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_fetch_use_only_the_official_key_for_all_chat_protocols(self):
        settings=Settings(web_search_enabled=True)
        key=['fixture-search-key'];seen=[]
        def handler(request):
            seen.append((str(request.url),request.headers.get('authorization'),json.loads(request.content)))
            if request.url.path.endswith('/fetch'):
                return httpx.Response(200,json={'title':'官方说明','content':'网页事实'})
            return httpx.Response(200,json={'results':[{'title':'公告','url':'https://example.com/news','summary':'公开事实'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            search=DeepSeekSearch(SimpleNamespace(search_api_key=lambda:key[0]),client=client)
            for style in ('openai','responses','anthropic'):
                engine=SimpleNamespace(api_style=style,api_url='https://relay.example/v1/chat/completions',
                    api_key='fixture-relay-key',_get_client=AsyncMock(side_effect=AssertionError('Chat transport reused')))
                self.assertTrue((await search.run(engine,'公共公告',settings))['ok'])
                self.assertTrue((await search.fetch(engine,'https://example.com/news',settings))['ok'])
                engine._get_client.assert_not_called()
            key[0]='fixture-rotated-search-key'
            self.assertTrue((await search.run(engine,'下一则公开公告',settings))['ok'])
        self.assertEqual(len(seen),7)
        self.assertTrue(all(url.startswith('https://api.deepseek.com/v1/tools/') for url,_,_ in seen))
        self.assertTrue(all(auth=='Bearer fixture-search-key' for _,auth,_ in seen[:6]))
        self.assertEqual(seen[-1][1],'Bearer fixture-rotated-search-key')
        self.assertNotIn('fixture-relay-key',json.dumps(seen))

    async def test_legacy_official_key_falls_back_but_a_relay_key_never_does(self):
        seen=[]
        def handler(request):
            seen.append(request.headers.get('authorization'))
            return httpx.Response(200,json={'results':[{'title':'公开事实'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            search=DeepSeekSearch(client=client)
            engine=SimpleNamespace(api_style='anthropic',api_url='https://api.deepseek.com/anthropic/v1/messages',api_key='fixture-official')
            settings=Settings(web_search_enabled=True)
            self.assertTrue((await search.run(engine,'公共新闻',settings))['ok'])
            engine.api_url='https://relay.example/v1/messages';engine.api_key='fixture-relay'
            result=await search.run(engine,'公共新闻',settings)
            self.assertFalse(result['ok']);self.assertEqual(result['requests'],0)
            self.assertIn('官方搜索密钥',result['error'])
        self.assertEqual(seen,['Bearer fixture-official'])

    async def test_relay_receives_same_native_search_tools_without_a_provider_probe(self):
        engine=SimpleNamespace(api_style='openai',api_url='https://relay.example/v1',stream_enabled=False,chat_with_tools=AsyncMock())
        self.assertEqual({t['function']['name'] for t in DeepSeekSearch.tools(engine,Settings(web_search_enabled=True))},
                         {'web_search','fetch_webpage'})
        engine.chat_with_tools.assert_not_called()
        engine.stream_enabled=True
        self.assertEqual(DeepSeekSearch.tools(engine,Settings(web_search_enabled=True)),[])


class IndependentKeyApiTests(unittest.TestCase):
    setUp=core.ApiTests.setUp
    tearDown=core.ApiTests.tearDown

    def test_search_key_is_encrypted_private_and_survives_chat_model_switches(self):
        settings=self.app.state.config.settings.model_dump()
        settings.update(api_url='https://relay.example/v1/chat/completions',model='custom-model')
        response=self.client.post('/api/config',headers=self.headers,json={
            'settings':settings,'api_key':'fixture-relay-key','search_api_key':'fixture-search-key'})
        self.assertEqual(response.status_code,200)
        public=response.json();config=self.app.state.config
        self.assertTrue(public['search_api_key_configured'])
        self.assertNotIn('fixture-search-key',response.text)
        self.assertNotIn('search_api_key',public)
        self.assertEqual(config.api_key(),'fixture-relay-key')
        self.assertEqual(config.search_api_key(),'fixture-search-key')
        encrypted=config.directory/'search-api-key.enc'
        self.assertNotIn(b'fixture-search-key',encrypted.read_bytes())
        self.assertNotIn('fixture-search-key',config.path.read_text(encoding='utf-8'))
        settings.update(api_style='responses',api_url='https://other-relay.example/v1/responses',model='other-model')
        response=self.client.post('/api/config',headers=self.headers,json={'settings':settings,'api_key':'fixture-other-relay'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(config.api_key(),'fixture-other-relay')
        self.assertEqual(config.search_api_key(),'fixture-search-key')
        self.assertEqual(Config(config.directory).search_api_key(),'fixture-search-key')


class RelaySearchDispatchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_relay_model_gets_results_and_the_same_model_finishes_the_reply(self):
        self.config.save(self.config.settings.model_copy(update={'web_search_enabled':True,'followup_wait_enabled':False}))
        self.config.save_search_key('fixture-search-key')
        self.queue(self.event(content='联网查一下公开公告'))
        engine=await self.runtime.model()
        engine.api_style='openai';engine.api_url='https://relay.example/v1/chat/completions';engine.stream_enabled=False
        async def native(messages,tools,**options):
            self.assertIn('web_search',{t['function']['name'] for t in tools})
            return {'tool_calls':[{'id':'search1','function':{'name':'web_search','arguments':'{"query":"公开公告"}'}}]}
        engine.chat_with_tools=native
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'新公告发布啦'}]}]
        def handler(request):
            self.assertEqual(request.url.host,'api.deepseek.com')
            self.assertEqual(request.headers['authorization'],'Bearer fixture-search-key')
            return httpx.Response(200,json={'results':[{'title':'公开公告','summary':'新公告发布了'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.runtime.web_search=DeepSeekSearch(self.config,client=client)
            await self.runtime.process_next()
        self.assertTrue(self.runtime.last_timing['web_search']['ok'])
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.gateway.sent,[('text','新公告发布啦')])
        self.assertIs(self.runtime.engine,engine)


if __name__=='__main__':unittest.main()

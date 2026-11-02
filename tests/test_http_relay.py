"""The reported HTTP relay, zero pacing and HTML response regressions."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whalegirl_linux.app import create_app
from whalegirl_linux.settings import Settings
from whalegirl_linux.upstream.ai_engine import AIEngine
import test_chat_habits as habits


class RelaySettingsTests(unittest.TestCase):
    def test_http_port_base_and_zero_pacing_save_without_resetting_other_settings(self):
        root=Path(__file__).resolve().parents[1]/'test-runs';root.mkdir(exist_ok=True)
        app=create_app(Path(tempfile.mkdtemp(prefix='http-relay-',dir=root)),gateway=habits.Gateway(),run_background=False)
        c=app.state.config;c.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom'],sticker_usage_target=75))
        values={'api_url':'http://154.9.235.108:8080/v1','chat_cooldown':0,'min_send_interval':0}
        with TestClient(app) as client:
            response=client.post('/api/config',headers={'Authorization':'Bearer '+c.admin_token},json={'settings':values})
            self.assertEqual(response.status_code,200,response.text)
            saved=response.json();self.assertEqual({k:saved[k] for k in values},values)
            self.assertEqual(saved['allowed_chat_ids'],['group@chatroom']);self.assertEqual(saved['sticker_usage_target'],75)
        engine=AIEngine(values['api_url'],'fake',api_style='openai')
        self.assertEqual(engine.api_url,'http://154.9.235.108:8080/v1/chat/completions')

    def test_pacing_still_rejects_negative_values_and_image_settings_keep_their_protocol(self):
        for values in ({'chat_cooldown':-.1},{'min_send_interval':-.1},{'image_api_url':'http://example.test/v1'}):
            with self.assertRaises(ValidationError):Settings(**values)


class RelayResponseTests(unittest.IsolatedAsyncioTestCase):
    async def request(self,response,*,tools=False):
        calls=[];events=[]
        engine=AIEngine('http://relay.test:8080/v1','fake',api_style='openai',max_retries=2)
        def handler(request):calls.append(request);return response
        engine._clients[id(asyncio.get_running_loop())]=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        engine.usage_callback=events.append
        try:
            options={'tools':[{'type':'function','function':{'name':'wait_for_followup','parameters':{'type':'object','properties':{}}}}]} if tools else {}
            result=await (engine.chat_with_tools if tools else engine.chat)([{'role':'user','content':'不要输出请求资料'}],**options)
            return result,calls,events,engine.last_error
        finally:await engine.close()

    async def test_html_page_has_a_clear_error_without_echoing_body_or_retrying(self):
        for tools in (False,True):
            result,calls,events,error=await self.request(httpx.Response(200,text='<html>private upstream body</html>',headers={'content-type':'text/html'}),tools=tools)
            self.assertEqual(len(calls),1);self.assertIn('返回了网页',error);self.assertNotIn('private upstream body',error)
            self.assertFalse(result.get('content') if tools else result)
            self.assertEqual(events[0]['status'],'error')

    async def test_http_json_reply_preserves_usage_and_needs_one_request(self):
        response=httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'连接正常'}}],
            'usage':{'prompt_tokens':10,'completion_tokens':2,'prompt_tokens_details':{'cached_tokens':8}}})
        for tools in (False,True):
            result,calls,events,error=await self.request(response,tools=tools)
            self.assertEqual(result['content'] if tools else result,'连接正常');self.assertEqual(error,'')
            self.assertEqual(len(calls),1);self.assertEqual(calls[0].url.path,'/v1/chat/completions')
            self.assertEqual(events[0]['cached_tokens'],8)


if __name__=='__main__':unittest.main()

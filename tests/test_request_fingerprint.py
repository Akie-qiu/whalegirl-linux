"""Request diagnostics retain only hashes and stay paired with each response."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock

import httpx

from whalegirl_linux.upstream.ai_engine import AIEngine


def engine(style='openai'):
    return AIEngine('http://127.0.0.1/v1','test-key',model='test-model',api_style=style,max_retries=1)


class RequestFingerprintTests(unittest.TestCase):
    def test_dynamic_user_data_does_not_change_system_and_tools_keep_order(self):
        e=engine();e.last_response_meta={'existing':'unchanged'}
        one=e._build_payload([{'role':'user','content':'first input'}],
            'fixed system\n【本轮动态上下文】first dynamic',None)
        two=e._build_payload([{'role':'user','content':'second input'}],
            'fixed system\n【本轮动态上下文】second dynamic',None)
        self.assertEqual(e.last_response_meta,{'existing':'unchanged'})
        a={'type':'function','function':{'name':'first','parameters':{'type':'object'}}}
        b={'type':'function','function':{'name':'second','parameters':{'type':'object'}}}
        one['tools']=[a,b];two['tools']=[dict(reversed(list(a.items()))),b]
        fp=e._request_fingerprint(one)
        self.assertEqual(fp,e._request_fingerprint(two))
        self.assertEqual(set(fp),{'system_sha256','tools_sha256','system_chars','api_style'})
        two['tools']=[b,a]
        self.assertNotEqual(fp['tools_sha256'],e._request_fingerprint(two)['tools_sha256'])
        self.assertEqual(e.last_response_meta,{'existing':'unchanged'})

    def test_protocol_fingerprint_uses_effective_system_only(self):
        hashes=[]
        for style in ('openai','responses','anthropic'):
            e=engine(style)
            payload=e._build_payload([{'role':'user','content':'private input'}],'fixed system',None)
            fp=e._request_fingerprint(payload)
            self.assertEqual(fp['api_style'],style)
            self.assertEqual(len(fp['system_sha256']),64)
            self.assertNotIn('private input',json.dumps(fp))
            self.assertNotIn('test-key',json.dumps(fp))
            hashes.append(fp['system_sha256'])
        self.assertEqual(len(set(hashes)),1)


class ConcurrentFingerprintTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_plain_and_tool_requests_keep_their_own_hashes(self):
        e=engine();release=asyncio.Event();posted=[]
        async def post(url,*,json):
            posted.append(json)
            if json['messages'][0]['content'].startswith('first system'):
                await release.wait()
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
                'message':{'role':'assistant','content':'complete answer'}}]},
                request=httpx.Request('POST',url))
        e._get_client=AsyncMock(return_value=type('Client',(),{'post':staticmethod(post)})())
        tools=[{'type':'function','function':{'name':'lookup','parameters':{'type':'object'}}}]
        async def first():
            await e.chat([{'role':'user','content':'first input'}],system_prompt='first system')
            return dict(e.last_response_meta['request_fingerprint'])
        async def second():
            await e.chat_with_tools([{'role':'user','content':'second input'}],tools,
                                    system_prompt='second system')
            fp=dict(e.last_response_meta['request_fingerprint']);release.set();return fp
        first_fp,second_fp=await asyncio.gather(first(),second())
        self.assertEqual(first_fp,e._request_fingerprint(posted[0]))
        self.assertEqual(second_fp,e._request_fingerprint(posted[1]))
        self.assertNotEqual(first_fp['system_sha256'],second_fp['system_sha256'])
        self.assertNotEqual(first_fp['tools_sha256'],second_fp['tools_sha256'])
        self.assertTrue(all('request_fingerprint' not in payload for payload in posted))


if __name__=='__main__':unittest.main()

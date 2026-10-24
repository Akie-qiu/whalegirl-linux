"""Numeric accounting regressions; no external model calls or chat sends."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

from whalegirl_linux.store import Store
from whalegirl_linux.token_usage import TokenLedger,normalize_usage

ROOT=Path(__file__).resolve().parents[1]


class NormalizationTests(unittest.TestCase):
    def test_deepseek_cache_and_reasoning_are_subsets(self):
        value=normalize_usage({'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
            'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20,'completion_tokens_details':{'reasoning_tokens':5}})
        self.assertEqual([value[k] for k in ('input_tokens','output_tokens','total_tokens','cached_tokens','uncached_tokens','reasoning_tokens')],[100,20,120,80,20,5])
        self.assertIsNone(value['cache_write_tokens'])

    def test_standard_openai_details_and_unknown_usage(self):
        value=normalize_usage({'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':70}})
        self.assertEqual(value['uncached_tokens'],30)
        self.assertEqual(value['total_tokens'],120)
        self.assertTrue(all(v is None for v in normalize_usage({}).values()))

    def test_anthropic_exclusive_input_counts_are_combined_once(self):
        value=normalize_usage({'input_tokens':30,'output_tokens':10,'cache_read_input_tokens':50,'cache_creation_input_tokens':20},'anthropic')
        self.assertEqual(value['input_tokens'],100);self.assertEqual(value['total_tokens'],110)
        self.assertEqual(value['uncached_tokens'],30)


class LedgerTests(unittest.TestCase):
    def test_persistence_scope_unknowns_and_weighted_cache_rate(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True);path=Path(tempfile.mkdtemp(prefix='token-usage-',dir=folder))/'state.sqlite3'
        store=Store(path);book=TokenLedger(store)
        def record(raw,account='account',status='success'):
            book.record(account,{'model':'example','provider':'example.test','status':status,'elapsed_ms':100,**normalize_usage(raw)})
        record({'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':80})
        record({'prompt_tokens':900,'completion_tokens':10,'prompt_cache_hit_tokens':90})
        record({},status='error');record({'prompt_tokens':999,'completion_tokens':1},account='other')
        stats=book.snapshot('account','all')['summary'];self.assertEqual(stats['total_tokens'],1030)
        self.assertEqual(stats['cache_hit_rate'],17);self.assertEqual(stats['requests'],3);self.assertEqual(stats['failed'],1)
        self.assertEqual(stats['total_tokens_reports'],2);self.assertIsNone(stats['cache_write_tokens'])
        store.close();store=Store(path)
        try:self.assertEqual(TokenLedger(store).snapshot('account','all')['summary']['requests'],3)
        finally:store.close()

    def test_beijing_calendar_day(self):
        from datetime import datetime,timezone
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True);store=Store(Path(tempfile.mkdtemp(prefix='token-day-',dir=folder))/'state.sqlite3');book=TokenLedger(store)
        now=datetime(2026,10,22,16,30,tzinfo=timezone.utc).timestamp()
        book.record('account',{'created':now-3600,'model':'m','provider':'x','status':'success','input_tokens':1})
        book.record('account',{'created':now,'model':'m','provider':'x','status':'success','input_tokens':2})
        try:
            with patch('whalegirl_linux.token_usage.time.time',return_value=now):
                self.assertEqual(book.snapshot('account','today')['summary']['input_tokens'],2)
                self.assertEqual(book.snapshot('account','7d')['summary']['input_tokens'],3)
        finally:store.close()


class EngineAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_callback_failure_never_duplicate_successful_request(self):
        import httpx
        from whalegirl_linux.upstream.ai_engine import AIEngine
        calls=[];events=[]
        def response(request):
            calls.append(request)
            return httpx.Response(429) if len(calls)==1 else httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':80}})
        engine=AIEngine('https://example.test/v1','test',max_retries=2);client=httpx.AsyncClient(transport=httpx.MockTransport(response))
        engine._get_client=AsyncMock(return_value=client);engine.usage_callback=events.append
        try:
            with patch('whalegirl_linux.upstream.ai_engine.asyncio.sleep',new=AsyncMock()):
                self.assertEqual(await engine.chat([{'role':'user','content':'test'}]),'ok')
            self.assertEqual(len(events),2);self.assertEqual(events[0]['status'],'error');self.assertIsNone(events[0]['total_tokens'])
            self.assertEqual(events[1]['total_tokens'],120);self.assertEqual(len(calls),2)
            def broken(_):raise RuntimeError('accounting only')
            engine.usage_callback=broken
            self.assertEqual(await engine.chat([{'role':'user','content':'test'}]),'ok');self.assertEqual(len(calls),3)
        finally:await client.aclose()

    async def test_stream_final_usage_chunk_is_counted_once(self):
        import httpx
        from whalegirl_linux.upstream.ai_engine import AIEngine
        events=[];chunks=[{'choices':[{'delta':{'content':'ok'}}]},
            {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_cache_hit_tokens':80}}]
        body=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=body)))
        engine=AIEngine('https://example.test/v1','test');engine._get_client=AsyncMock(return_value=client);engine.usage_callback=events.append
        try:
            self.assertEqual(''.join([c async for c in engine.chat_stream([{'role':'user','content':'test'}])]),'ok')
            self.assertEqual(len(events),1);self.assertEqual(events[0]['total_tokens'],120)
        finally:await client.aclose()


class UsageApiTests(unittest.TestCase):
    def test_authentication_and_period_validation(self):
        from test_core import ApiTests
        fixture=ApiTests();fixture.setUp()
        try:
            self.assertEqual(fixture.client.get('/api/token-usage').status_code,401)
            result=fixture.client.get('/api/token-usage',headers=fixture.headers)
            self.assertEqual(result.status_code,200);self.assertEqual(result.json()['summary']['requests'],0)
            self.assertEqual(fixture.client.get('/api/token-usage?period=bad',headers=fixture.headers).status_code,400)
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()

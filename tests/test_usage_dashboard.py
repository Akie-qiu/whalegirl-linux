"""Accounting and authorization tests with synthetic data, no external calls."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import time

from whalegirl_linux.store import Store
from whalegirl_linux.token_usage import TokenLedger
from whalegirl_linux.usage_context import CURRENT_USAGE, scoped_usage, set_usage_context
from whalegirl_linux.usage_dashboard import UsageDashboard
from whalegirl_linux.usage_pricing import TZ, quote
from whalegirl_linux.usage_tools import UsageStatsTools

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT/'artifacts/usage-dashboard-20261110/tests'
RUNS.mkdir(parents=True, exist_ok=True)


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp()


def event(**updates):
    return dict(created=stamp('2026-11-10T10:00:00'), provider='api.deepseek.com',
                model='deepseek-flash', operation='chat', status='success', elapsed_ms=1234,
                input_tokens=1_000_000, cached_tokens=800_000, uncached_tokens=200_000,
                cache_write_tokens=None, output_tokens=100_000, total_tokens=1_100_000,
                reasoning_tokens=50_000, **updates)


class PriceTests(unittest.TestCase):
    def test_deepseek_peak_cache_and_reasoning_not_double_counted(self):
        price = quote(event())
        self.assertAlmostEqual(price['low'], 1.232)
        self.assertEqual(price['low'], price['high'])
        self.assertEqual(price['currency'], 'CNY')

    def test_offpeak_weekend_holiday_and_boundaries(self):
        for day in ('2026-11-10T08:59:59','2026-11-10T12:00:00','2026-11-10T18:00:00',
                    '2026-11-07T10:00:00','2026-10-01T10:00:00'):
            row=event();row['created']=stamp(day)
            self.assertAlmostEqual(quote(row)['low'], .616, msg=day)
        row=event();row['created']=stamp('2027-01-01T10:00:00')
        self.assertEqual(quote(row)['status'],'range')

    def test_unknown_model_missing_usage_and_cache_uncertainty(self):
        row=event();row['model']='deepseek-unverified-relay-name'
        self.assertIsNone(quote(row)['low'])
        row=event();row['output_tokens']=None
        self.assertIsNone(quote(row)['low'])
        row=event();row.update(cached_tokens=None,uncached_tokens=None)
        price=quote(row)
        self.assertAlmostEqual(price['low'],.84);self.assertAlmostEqual(price['high'],2.8)
        row['cached_tokens']=2_000_000
        self.assertEqual(quote(row)['status'],'unknown')

    def test_google_promo_and_anthropic_exclusive_cache_write(self):
        row=event();row.update(model='gemini-3.8-flash',provider='generativelanguage.googleapis.com')
        self.assertAlmostEqual(quote(row)['low'],.585)
        row['created']=stamp('2027-01-02T00:00:00')
        self.assertAlmostEqual(quote(row)['low'],1.17)
        row=event();row.update(model='claude-sonnet-4-6',provider='api.anthropic.com',
                              cached_tokens=500_000,cache_write_tokens=200_000,uncached_tokens=300_000)
        price=quote(row)
        self.assertAlmostEqual(price['low'],3.3);self.assertAlmostEqual(price['high'],3.75)

    def test_contradictory_misses_are_unknown_not_discounted(self):
        row=event();row['uncached_tokens']=300_000
        self.assertIsNone(quote(row)['low'])

    def test_zero_tts_tokens_never_imply_a_free_audio_generation(self):
        row=event();row.update(model='gemini-3.8-flash-tts',input_tokens=0,output_tokens=0,
                              cached_tokens=0,uncached_tokens=0,total_tokens=0)
        self.assertIsNone(quote(row)['low'])

    def test_search_per_successful_request_not_free_tokens(self):
        row=event();row.update(operation='search',input_tokens=None,output_tokens=None)
        self.assertEqual(quote(row)['low'],.005)
        row['status']='error';self.assertEqual(quote(row)['low'],0)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(Path(tempfile.mkdtemp(prefix='stats-',dir=RUNS))/'state.sqlite3')
        self.ledger=TokenLedger(self.store)
        s=SimpleNamespace(account_id='account',enabled=True,
            allowed_chat_ids=['a@chatroom','b@chatroom','owner','person'],owner_ids=['owner'])
        self.runtime=SimpleNamespace(store=self.store,config=SimpleNamespace(settings=s),
            chats_cache=[{'id':'a@chatroom','name':'示例A群'}],chat_labels={})
        self.stats=self.runtime.statistics=UsageDashboard(self.runtime)
        self.tools=UsageStatsTools(self.runtime)
        self.now=stamp('2026-11-10T15:00:00')

    def tearDown(self):
        self.store.close()

    def record(self,chat='',**changes):
        token=CURRENT_USAGE.set({})
        try:
            if chat:set_usage_context('account',chat,'synthetic-decision')
            row=event();row.update(changes)
            return self.ledger.record('account',row)
        finally:CURRENT_USAGE.reset(token)

    def snapshot(self,**kw):
        return self.stats.snapshot(now=self.now,**kw)

    def test_scope_preserves_unattributed_and_historical_rows(self):
        self.record('a@chatroom');self.record('b@chatroom',input_tokens=100,output_tokens=10,
            total_tokens=110,cached_tokens=80,uncached_tokens=20)
        self.record()
        # Original 15-column INSERT remains valid after additive migration.
        self.store.db.execute('INSERT INTO model_token_usage VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('legacy','account',self.now-10,'p','unknown','chat','error',0,None,None,None,None,None,None,None))
        self.store.db.commit()
        self.assertEqual(self.snapshot()['tokens']['summary']['requests'],4)
        scoped=self.snapshot(chat='a@chatroom')['tokens']['summary']
        self.assertEqual(scoped['requests'],1)
        self.assertEqual(scoped['unattributed_requests'],0)
        self.assertEqual(len(list(self.store.db.execute('PRAGMA table_info(model_token_usage)'))),15)
        self.assertNotIn('content',scoped)

    def test_price_snapshot_and_model_filter_and_currency_separation(self):
        self.record('a@chatroom',cost_value=.01,cost_currency='USD',cost_estimated=True)
        self.record('a@chatroom',model='gemini-3.8-flash')
        s=self.snapshot()['tokens']['summary']
        self.assertEqual({p['currency'] for p in s['official_costs']},{'CNY','USD'})
        self.assertEqual(s['reported_costs'][0]['value'],.01)
        self.assertEqual(s['reported_costs'][0]['estimated_requests'],1)
        self.assertEqual(self.snapshot(model='deepseek-flash')['tokens']['summary']['requests'],1)
        with patch('whalegirl_linux.usage_dashboard.quote',side_effect=AssertionError('must use snapshot')):
            self.assertEqual(self.snapshot(period='all')['tokens']['summary']['requests'],2)

    def test_calendar_period_and_future_exclusion(self):
        self.record(created=stamp('2026-11-09T23:59:59'))
        self.record(created=stamp('2026-11-10T00:00:00'))
        self.record(created=self.now+1)
        self.assertEqual(self.snapshot()['tokens']['summary']['requests'],1)
        self.assertEqual(self.snapshot(period='7d')['tokens']['summary']['requests'],2)
        self.assertEqual(len(self.snapshot()['timeline']),16)

    def test_delivery_counts_confirmed_actions_not_inbox_or_merges(self):
        for i,(state,kind,key) in enumerate([('sent','text','round1'),('sent','sticker','round1'),
            ('failed','text','round2'),('uncertain','voice','round3'),('sent','image','proactive:one')]):
            self.store.db.execute('INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at) '
                'VALUES(?,?,?,?,?,?,?,?)',(str(i),key,'account','a@chatroom',kind,'private-body',state,self.now-10))
        # Two observations of the same native incoming message must not double count.
        for key in ('pending','server'):
            self.store.observe([dict(key=key,account='account',chat='a@chatroom',sender_id='u',sender='private',
                kind=1,is_self=0,local_id=99,content='private-body',timestamp=self.now-20)])
        self.store.db.commit()
        data=self.snapshot(view='speech');s=data['speech']['summary']
        self.assertEqual((s['sent'],s['reply_rounds'],s['failed'],s['uncertain'],s['received']),(3,2,1,1,1))
        self.assertEqual(s['proactive_rounds'],1)
        self.assertNotIn('private-body',json.dumps(data))
        self.assertEqual(sum(p['sent'] for p in data['timeline']),3)

    def test_tool_scope_and_no_other_chat_or_provider_information(self):
        self.record('b@chatroom')
        first={'account':'account','chat':'a@chatroom','sender_id':'owner'}
        with patch('whalegirl_linux.usage_dashboard.time.time',return_value=self.now):
            result=self.tools.execute(first,{})
            self.assertEqual(result['scope'],'account')
            self.assertEqual(result['tokens']['requests'],1)
            self.assertEqual(self.tools.execute(first,{'scope':'current_chat'})['tokens']['requests'],0)
            self.assertNotIn('b@chatroom',json.dumps(result))
            self.assertTrue(self.tools.execute(first,{'scope':'account'})['ok'])
            first['sender_id']='person'
            self.assertFalse(self.tools.execute(first,{'scope':'account'})['ok'])
            first['chat']='person';first['sender_id']='person'
            self.assertFalse(self.tools.execute(first,{'scope':'account'})['ok'])
            first['chat']=first['sender_id']='owner'
            self.assertTrue(self.tools.execute(first,{'scope':'account'})['ok'])
            self.assertFalse(self.tools.execute(first,{'chat':'b@chatroom'})['ok'])

    def test_read_only_connection_and_restart_persistence(self):
        self.record('a@chatroom')
        connection=self.stats._connect()
        try:
            with self.assertRaises(Exception):connection.execute('DELETE FROM model_token_usage')
        finally:connection.close()
        new=UsageDashboard(self.runtime)
        self.assertEqual(new.snapshot(now=self.now)['tokens']['summary']['requests'],1)


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tasks_and_finally_reset(self):
        @scoped_usage
        async def run(chat):
            set_usage_context('a',chat,chat)
            await asyncio.sleep(0)
            return CURRENT_USAGE.get()['chat']
        self.assertEqual(await asyncio.gather(run('one'),run('two')),['one','two'])
        self.assertEqual(CURRENT_USAGE.get(),{})

    async def test_native_tool_reaches_actual_reply_and_attributes_both_requests(self):
        from test_chat_habits import ChatHabitTests
        fixture=ChatHabitTests()
        await fixture.asyncSetUp()
        try:
            first=fixture.event(content='你今天用了多少Token？')
            fixture.queue(first)
            engine=await fixture.runtime.model()
            engine.api_style='openai';engine.stream_enabled=False
            seen=[]
            async def native(messages,tools,**kwargs):
                self.assertIn('read_usage_stats',[tool['function']['name'] for tool in tools])
                data=event();data['created']=time.time();engine.usage_callback(data)
                return {'content':'','tool_calls':[{'id':'stats-1','function':{
                    'name':'read_usage_stats','arguments':'{"view":"tokens"}'}}]}
            async def answer(messages,**kwargs):
                seen.append(messages)
                data=event();data.update(created=time.time(),operation=kwargs.get('operation','chat'))
                engine.usage_callback(data)
                return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'记录里有一次查询啦，费用只是参考'}]})
            engine.chat_with_tools=native;engine.chat=answer
            await fixture.runtime.process_next()
            self.assertEqual(len(seen),1)
            self.assertIn('只读统计工具实际结果',seen[0][-1]['content'])
            self.assertEqual(len(fixture.gateway.sent),1,fixture.gateway.sent)
            self.assertIn('本群·今天',str(fixture.gateway.sent))
            rows=fixture.store.db.execute('SELECT chat,decision_id FROM model_usage_context').fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual({r[0] for r in rows},{first['chat']})
            self.assertEqual(len({r[1] for r in rows}),1)
            self.assertEqual(CURRENT_USAGE.get(),{})
        finally:await fixture.asyncTearDown()

    async def test_proactive_json_tool_reads_once_and_follows_up(self):
        from test_proactive_tools import ProactiveToolTests
        fixture=ProactiveToolTests()
        await fixture.asyncSetUp()
        try:
            calls=fixture.engine({'should_reply':True,'actions':[],'stats_request':{'view':'speech'}},
                {'should_reply':True,'actions':[{'type':'text','text':'今天还挺安静的'}]})
            self.assertTrue(await fixture.service.run_once())
            self.assertEqual(len(calls),2)
            self.assertIn('只读统计工具结果',str(calls[1][0]))
            self.assertEqual(len(fixture.gateway.sent),1)
        finally:await fixture.asyncTearDown()


class ApiTests(unittest.TestCase):
    def test_dashboard_uses_existing_admin_boundary(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from test_core import FakeGateway
        app=create_app(Path(tempfile.mkdtemp(prefix='api-',dir=RUNS)),gateway=FakeGateway(),run_background=False)
        headers={'Authorization':'Bearer '+app.state.config.admin_token}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/usage-dashboard').status_code,401)
            self.assertEqual(client.get('/api/usage-dashboard/options').status_code,401)
            for url in ('/api/usage-dashboard','/api/usage-dashboard/options','/usage-dashboard.css','/token-usage.js'):
                self.assertEqual(client.get(url,headers=headers).status_code,200,url)
            for params in ('period=bad','view=write','chat='+'x'*201,'model='+'x'*121):
                self.assertEqual(client.get('/api/usage-dashboard?'+params,headers=headers).status_code,400)

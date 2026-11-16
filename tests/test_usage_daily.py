"""Daily numeric truth and owner query scope; no model calls or WeChat sends."""
import copy
import json
import unittest

from whalegirl_linux.token_usage import normalize_usage
from whalegirl_linux.usage_tools import UsageStatsTools
import test_usage_dashboard as fixtures

stamp=fixtures.stamp


class ReasoningAccountingTests(unittest.TestCase):
    def test_reasoning_is_counted_once_not_as_extra_budget(self):
        for style, raw in [
            ('openai', {'prompt_tokens':100,'completion_tokens':40,'total_tokens':140,
                        'completion_tokens_details':{'reasoning_tokens':30}}),
            ('responses', {'input_tokens':100,'output_tokens':40,'total_tokens':140,
                          'output_tokens_details':{'reasoning_tokens':30}}),
            ('openai', {'prompt_tokens':100,'completion_tokens':40,'total_tokens':140,
                        'reasoning_tokens':30}),
        ]:
            with self.subTest(style=style,raw=raw):
                value=normalize_usage({**raw,'thinking_budget':32000,'max_tokens':64000},style)
                self.assertEqual((value['input_tokens'],value['output_tokens'],value['total_tokens'],
                                  value['reasoning_tokens']),(100,40,140,30))

    def test_unreported_usage_and_reasoning_remain_unknown(self):
        value=normalize_usage({'max_tokens':16384,'thinking_budget':8192})
        self.assertIsNone(value['total_tokens'])
        self.assertIsNone(value['reasoning_tokens'])
        value=normalize_usage({'prompt_tokens':10,'completion_tokens':2,
                              'completion_tokens_details':[],'prompt_tokens_details':'invalid'})
        self.assertEqual(value['total_tokens'],12)
        self.assertIsNone(value['reasoning_tokens'])

    def test_zero_reasoning_is_reported_and_does_not_use_flat_fallback(self):
        value=normalize_usage({'input_tokens':10,'output_tokens':2,'reasoning_tokens':99,
                              'output_tokens_details':{'reasoning_tokens':0}})
        self.assertEqual(value['reasoning_tokens'],0)


class DailyUsageTests(unittest.TestCase):
    setUp=fixtures.DashboardTests.setUp
    tearDown=fixtures.DashboardTests.tearDown
    record=fixtures.DashboardTests.record
    snapshot=fixtures.DashboardTests.snapshot

    def test_all_operations_failures_background_and_reasoning_in_daily_totals(self):
        amounts=[('chat',1_870_000),('proactive',100_000),('growth_learn',200_000),
                 ('learning',30_000),('moments',20_000),('tool_followup',10_000)]
        for operation,total in amounts:
            self.record(operation=operation,input_tokens=total-100,output_tokens=100,
                        total_tokens=total,reasoning_tokens=70,cached_tokens=0,
                        uncached_tokens=total-100)
        self.record('a@chatroom',operation='chat',status='error',input_tokens=None,
                    output_tokens=None,total_tokens=None,reasoning_tokens=None)
        self.record(operation='search',status='error',input_tokens=None,output_tokens=None,
                    total_tokens=None,reasoning_tokens=None)
        data=self.snapshot()
        summary=data['tokens']['summary'];days=data['tokens']['daily']
        self.assertEqual(summary['total_tokens'],2_230_000)
        self.assertEqual(summary['reasoning_tokens'],420)
        self.assertEqual(summary['missing_token_requests'],1)
        self.assertFalse(summary['total_tokens_complete'])
        self.assertEqual(len(days),1)
        self.assertEqual(days[0]['total_tokens'],summary['total_tokens'])
        self.assertEqual(days[0]['reasoning_tokens'],420)
        self.assertEqual(days[0]['requests'],8)

    def test_beijing_midnight_daily_and_period_total_reconcile(self):
        for created,total in [('2026-11-09T23:59:59',100),('2026-11-10T00:00:00',200)]:
            self.record(created=stamp(created),input_tokens=total-20,output_tokens=20,
                        total_tokens=total,reasoning_tokens=10,cached_tokens=0,uncached_tokens=total-20)
        data=self.snapshot(period='7d')['tokens']
        self.assertEqual(len(data['daily']),7)
        self.assertEqual(data['daily'][0]['day'],'2026-11-10')
        self.assertEqual(data['daily'][0]['total_tokens'],200)
        self.assertEqual(data['daily'][1]['total_tokens'],100)
        self.assertEqual(data['daily'][2]['total_tokens'],0)
        self.assertEqual(sum(d['total_tokens'] or 0 for d in data['daily']),data['summary']['total_tokens'])
        self.assertEqual(sum(d['reasoning_tokens'] or 0 for d in data['daily']),20)
        self.assertEqual(self.snapshot()['tokens']['summary']['total_tokens'],200)

    def test_missing_usage_does_not_become_zero_or_a_full_price(self):
        self.record(input_tokens=None,output_tokens=None,total_tokens=None,status='error')
        data=self.snapshot()['tokens']
        self.assertIsNone(data['summary']['total_tokens'])
        self.assertIsNone(data['daily'][0]['total_tokens'])
        self.assertEqual(data['daily'][0]['missing_token_requests'],1)
        self.assertFalse(data['summary']['total_tokens_exact'])
        self.assertFalse(data['summary']['official_costs'])

    def test_owner_group_and_private_default_to_account_explicit_group_stays_scoped(self):
        self.record('a@chatroom',input_tokens=574_650,output_tokens=100,total_tokens=574_750,
                    cached_tokens=0,uncached_tokens=574_650,reasoning_tokens=80)
        self.record('b@chatroom',input_tokens=1_869_900,output_tokens=100,total_tokens=1_870_000,
                    cached_tokens=0,uncached_tokens=1_869_900,reasoning_tokens=60)
        self.record(input_tokens=90,output_tokens=10,total_tokens=100,cached_tokens=0,
                    uncached_tokens=90,reasoning_tokens=5,operation='growth_learn')
        from unittest.mock import patch
        with patch('whalegirl_linux.usage_dashboard.time.time',return_value=self.now):
            for chat in ('owner','a@chatroom'):
                with self.subTest(chat=chat):
                    result=self.tools.execute({'account':'account','chat':chat,'sender_id':'owner'}, {})
                    self.assertEqual(result['scope'],'account')
                    self.assertEqual(result['scope_label'],'全账号')
                    self.assertEqual(result['tokens']['total_tokens'],2_444_850)
                    self.assertEqual(result['daily_tokens'][0]['total_tokens'],2_444_850)
                    self.assertEqual(result['tokens']['reasoning_tokens'],145)
                    self.assertIn('后台',result['attribution_notice'])
            scoped=self.tools.execute({'account':'account','chat':'a@chatroom','sender_id':'owner'},
                                      {'scope':'current_chat'})
        self.assertEqual(scoped['scope_label'],'本群')
        self.assertEqual(scoped['tokens']['total_tokens'],574_750)
        self.assertIn('不能称为全账号',scoped['attribution_notice'])
        self.assertNotIn('b@chatroom',json.dumps(scoped))

    def test_nonowner_cannot_claim_owner_scope_or_identity(self):
        from unittest.mock import patch
        with patch('whalegirl_linux.usage_dashboard.time.time',return_value=self.now):
            first={'account':'account','chat':'a@chatroom','sender_id':'person','sender':'owner'}
            result=self.tools.execute(first,{})
            self.assertEqual(result['scope'],'current_chat')
            self.assertFalse(self.tools.execute(first,{'scope':'account'})['ok'])
            first.update(chat='person',sender_id='owner')
            self.assertFalse(self.tools.execute(first,{'scope':'account'})['ok'])

    def test_scope_heading_is_visible_idempotent_and_does_not_change_figures(self):
        decision={'should_reply':True,'actions':[{'type':'text','text':'已记录 574750 Token'},
                                                {'type':'text','text':'参考费用，部分未知'}]}
        result={'ok':True,'reply_heading':'【本群 · 今天 · 北京时间】'}
        UsageStatsTools.label_reply(decision,result)
        first=copy.deepcopy(decision)
        UsageStatsTools.label_reply(decision,result)
        self.assertEqual(decision,first)
        self.assertTrue(decision['actions'][0]['text'].startswith(result['reply_heading']))
        self.assertIn('574750',decision['actions'][0]['text'])
        self.assertEqual(decision['actions'][1]['text'],'参考费用，部分未知')


if __name__=='__main__':
    unittest.main()

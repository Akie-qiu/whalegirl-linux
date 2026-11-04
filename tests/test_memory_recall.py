"""Regressions for missed long-term records and empty-scope misreporting."""
from pathlib import Path
import tempfile
import time
import unittest
import json

from whalegirl_linux.memory import MemoryBook, memory_prompt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from tests.test_chat_habits import Engine, Gateway

ROOT = Path(__file__).resolve().parents[1]


class MemoryRecallTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='memory-recall-', dir=runs))
        self.store = Store(self.root / 'state.sqlite3')
        self.book = MemoryBook(self.store)
        self.allowed = ['a@chatroom', 'b@chatroom', 'alice']

    def tearDown(self):
        self.store.close()

    def message(self, content, chat='a@chatroom', person='alice'):
        event = Runtime.event('account', chat, {'serverId':1, 'localId':1,
            'sender':person, 'senderName':person, 'type':1, 'content':content,
            'isSelf':False, 'timestamp':time.time()})
        self.store.observe([event])
        return event

    def remember(self, content, person='alice', kind='fact', scope='chat', actor='admin', chat='a@chatroom'):
        return self.book.change('account', self.allowed, {'chat':chat, 'subject':person,
            'kind':kind, 'scope':scope, 'content':content, 'source_key':''}, actor=actor)

    def test_overlapping_chinese_terms_retrieve_another_persons_relevant_fact(self):
        row = self.remember('刺猬习性是测试主题',person='bob')
        # No author/administrator fallback: exercise topical matching alone.
        self.store.db.execute("UPDATE memories SET actor='model' WHERE id=?", (row['id'],))
        rows = self.book.recall('account', self.allowed, 'alice', '能介绍一下刺猬习性吗',chat='a@chatroom')
        self.assertIn(row['id'], [r['id'] for r in rows])

    def test_older_personal_fact_survives_more_than_100_recent_events(self):
        fact = self.remember('偏好不加糖的饮料',scope='profile')
        for i in range(105):
            self.remember('本群第'+str(i)+'次讨论',kind='event')
        rows = self.book.recall('account',self.allowed,'alice','你好',chat='a@chatroom',limit=8)
        self.assertEqual(rows[0]['id'],fact['id'])

    def test_current_chat_admin_fact_is_available_without_keyword_hit(self):
        fact=self.remember('测试中的管理员修正',person='legacy-label')
        rows=self.book.recall('account',self.allowed,'alice','你好',chat='a@chatroom')
        self.assertEqual([r['id'] for r in rows],[fact['id']])

    def test_mentioned_person_preference_is_not_lost_to_main_speakers_events(self):
        fact=self.remember('喜欢无糖饮料',person='bob',scope='profile',kind='preference')
        for i in range(10):self.remember('本群短期事件'+str(i),kind='event')
        rows=self.book.recall('account',self.allowed,'alice','给他选一个',chat='a@chatroom',subjects=['bob'],limit=2)
        self.assertIn(fact['id'],[r['id'] for r in rows])

    def test_private_empty_scope_does_not_expose_group_events_or_claim_global_empty(self):
        self.remember('本群的独立事件',kind='event')
        event=self.message('查看你的长期记忆',chat='alice')
        settings=Settings(account_id='account',allowed_chat_ids=self.allowed)
        diagnostics={}
        prompt,_=memory_prompt(self.book,settings,event,[event],diagnostics=diagnostics)
        self.assertEqual(diagnostics['available_in_scope'],0)
        self.assertEqual(diagnostics['count'],0)
        self.assertNotIn('本群的独立事件',prompt)
        self.assertIn('不代表整个记忆库为空',prompt)

    def test_missing_model_scope_uses_profile_only_for_clear_enduring_self_fact(self):
        event=self.message('我喜欢无糖可乐')
        operation={'op':'create','chat':event['chat'],'subject':'alice','kind':'preference',
                   'content':'喜欢无糖可乐','source_key':event['key']}
        row=self.book.change('account',self.allowed,operation,actor='model',allowed_sources=[event['key']])
        self.assertEqual(row['scope'],'profile')
        rows=self.book.recall('account',self.allowed,'alice','我喜欢什么饮料',chat='alice')
        self.assertEqual(rows[0]['id'],row['id'])
        explicit=self.book.change('account',self.allowed,{**operation,'scope':'chat','content':'本群内保留的偏好'},
                                  actor='model',allowed_sources=[event['key']])
        self.assertEqual(explicit['scope'],'chat')

    def test_prompt_records_exact_loaded_ids_and_keeps_edit_versions(self):
        fact=self.remember('喜欢猫',scope='profile',kind='preference')
        event=self.message('你好')
        diagnostics={}
        prompt,_=memory_prompt(self.book,Settings(account_id='account',allowed_chat_ids=self.allowed),
                               event,[event],diagnostics=diagnostics)
        self.assertEqual(diagnostics['ids'],[fact['id']])
        self.assertEqual(diagnostics['profiles'],1)
        self.assertIn('"version": 1',prompt)
        self.assertIn('"sender": "alice"',prompt)


class MemoryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_recalled_record_reaches_actual_model_input_in_one_call(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='memory-runtime-',dir=runs))
        config=Config(folder)
        config.save(Settings(enabled=True,account_id='account',allowed_chat_ids=['alice'],
            debounce_single_seconds=0,debounce_burst_seconds=0,debounce_max_seconds=0),'test-key')
        store=Store(folder/'state.sqlite3');gateway=Gateway()
        runtime=Runtime(config,store,gateway,ROOT/'styles',engine_factory=Engine)
        try:
            row=runtime.memory_book.change('account',['alice'],{'chat':'alice','subject':'alice',
                'kind':'preference','scope':'profile','content':'测试偏好：喜欢无糖可乐','source_key':''})
            event=Runtime.event('account','alice',{'serverId':1,'localId':1,'sender':'alice',
                'senderName':'alice','type':1,'content':'我喜欢什么饮料','isSelf':False,'timestamp':time.time()})
            event['trigger']='direct';store.observe([event]);store.insert(event)
            engine=await runtime.model();captured=[];original=engine.chat
            async def capture(messages,**kwargs):
                captured.append(kwargs['system_prompt'])
                return await original(messages,**kwargs)
            engine.chat=capture
            engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'你喜欢无糖可乐呀'}]}]
            await runtime.process_next()
            self.assertEqual(engine.calls,1)
            self.assertIn('测试偏好：喜欢无糖可乐',captured[0])
            metrics=json.loads(store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(event['key'],)).fetchone()[0])
            self.assertEqual(metrics['long_term_memory']['ids'],[row['id']])
            self.assertEqual(gateway.sent,[('text','你喜欢无糖可乐呀')])
        finally:
            await runtime.close()


if __name__ == '__main__':
    unittest.main()

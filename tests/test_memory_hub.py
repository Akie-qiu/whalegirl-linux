"""Unified storage, attribution, lifecycle and the actual reply path."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from test_chat_habits import Engine, Gateway
from whalegirl_linux.app import create_app
from whalegirl_linux.context import build_context
from whalegirl_linux.memory import memory_parts
from whalegirl_linux.memory_hub import DurableMemoryQueue
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT=Path(__file__).resolve().parents[1]


def directory():
    (ROOT/'test-runs').mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='memory-hub-',dir=ROOT/'test-runs'))


def event(n=1,chat='a@chatroom',person='wxid_alice',text='我喜欢猫',**extra):
    return dict(key=f'account/{chat}/{n}',account='account',chat=chat,sender='昵称',sender_id=person,
                content=text,kind=1,is_self=0,local_id=n,timestamp=time.time(),payload_json='{}',trigger='direct',**extra)


class MemoryHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config=Config(directory())
        self.config.save(Settings(enabled=True,account_id='account',allowed_chat_ids=['a@chatroom','b@chatroom','wxid_alice'],
            stickers_enabled=False,images_chat_enabled=False,debounce_single_seconds=0,debounce_burst_seconds=0,
            debounce_max_seconds=0),'test-key')
        self.store=Store(self.config.directory/'state.sqlite3')
        self.runtime=Runtime(self.config,self.store,Gateway(),ROOT/'styles',engine_factory=Engine)
        self.hub=self.runtime.memory_hub;self.book=self.runtime.memory_book

    async def asyncTearDown(self):
        await self.runtime.close()

    def remember(self,content='喜欢猫',**extra):
        return self.book.change('account',self.config.settings.allowed_chat_ids,
            {'chat':'a@chatroom','subject':'wxid_alice','kind':'preference','scope':'profile','content':content,**extra})

    def learn(self,source,**extra):
        self.store.observe([source])
        return self.runtime.learning.book.propose(source,[source],dict(type='preference',scope='profile',
            subject=source['sender_id'],source_key=source['key'],evidence='我喜欢猫',content='喜欢猫',confidence=.96,**extra),self.config.settings)

    def recall(self,text='猫',chat='a@chatroom',person='wxid_alice',**extra):
        return self.hub.recall('account',self.config.settings.allowed_chat_ids,person,text,chat=chat,**extra)

    async def test_learning_and_explicit_memory_share_identity_and_evidence(self):
        first=event();self.store.observe([first])
        row=self.book.change('account',self.config.settings.allowed_chat_ids,
            {'chat':first['chat'],'subject':first['sender_id'],'kind':'preference','scope':'profile','content':'喜欢猫','source_key':first['key']})
        proposal=self.learn(first)
        mapping=self.store.db.execute("SELECT memory_id FROM memory_origins WHERE source='learning' AND origin_id=?",(str(proposal['id']),)).fetchone()
        self.assertEqual(mapping[0],row['id'])
        count=self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0]
        self.hub.migrate();self.hub.migrate()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0],count)
        self.assertEqual(len(self.recall(chat='b@chatroom')),1)
        self.assertGreaterEqual(len(self.hub.detail('account',self.config.settings.allowed_chat_ids,row['id'])['evidence']),1)

    async def test_scope_identity_and_private_venue_are_enforced_in_tools(self):
        profile=self.remember()
        group=self.remember('本群的水母夜',kind='event',scope='chat')
        self.assertEqual([r['id'] for r in self.recall(chat='b@chatroom')],[profile['id']])
        self.assertEqual(self.recall(person='wxid_other'),[])
        self.book.change('account',self.config.settings.allowed_chat_ids,
            {'op':'update','id':profile['id'],'version':profile['version'],'venue':'private'})
        results=self.book.model_ops('account',self.config.settings.allowed_chat_ids,[{'op':'get','id':profile['id']}],[],True,
                                   chat='b@chatroom',subjects=['wxid_alice'])
        self.assertIsNone(results[0]['result'])
        self.assertEqual([r['id'] for r in self.recall(chat='wxid_alice')],[profile['id']])
        self.assertNotIn(group['id'],[r['id'] for r in self.recall('水母夜',chat='b@chatroom')])

    async def test_cold_is_available_by_search_and_core_does_not_force_old_topic(self):
        cold=self.remember(tier='cold')
        core=self.remember('服务器磁盘故障',kind='event',scope='chat',tier='core')
        self.assertEqual(self.recall('今天心情不错'),[])
        results=self.book.model_ops('account',self.config.settings.allowed_chat_ids,[{'op':'search','query':'猫'}],[],True,
                                   chat='a@chatroom',subjects=['wxid_alice'])
        self.assertIn(cold['id'],[r['id'] for r in results[0]['result']])
        self.assertIn(core['id'],[r['id'] for r in self.recall('服务器磁盘')])

    async def test_forgetting_invalidates_learning_digest_raw_recall_and_queued_work(self):
        source=event();proposal=self.learn(source)
        row=self.recall()[0]
        self.store.save_digest('account','a@chatroom','旧摘要包含喜欢猫',source)
        self.runtime.learning.offer(source,[source],[dict(type='preference',scope='profile',subject=source['sender_id'],
            source_key=source['key'],evidence='我喜欢猫',content='偏爱猫咪',confidence=.99)])
        self.book.change('account',self.config.settings.allowed_chat_ids,{'op':'archive','id':row['id'],'version':row['version']})
        await self.runtime.learning.process_pending()
        self.assertEqual(self.recall(),[])
        self.assertEqual(self.store.digest('account','a@chatroom')['summary'],'')
        self.assertEqual(self.store.memory('account',['a@chatroom'],'a@chatroom','wxid_alice','喜欢猫'),[])
        legacy=self.runtime.learning.book.get('account',self.config.settings.allowed_chat_ids,proposal['id'])
        self.assertEqual(legacy['state'],'revoked')
        latest=event(2,text='猫是什么');self.store.observe([latest])
        messages,_,_=await build_context(self.store,self.runtime.gateway,self.config.settings,latest,[latest],{})
        self.assertNotIn('我喜欢猫',str(messages))

    async def test_dependency_withdrawal_respects_independent_sources(self):
        a=self.remember('第一条依据');b=self.remember('第二条依据')
        child=self.remember('相处经验',kind='interaction_rule',scope='chat',derived_from=[a['id'],b['id']])
        self.book.change('account',self.config.settings.allowed_chat_ids,{'op':'archive','id':a['id'],'version':a['version']})
        self.assertEqual(self.book.get('account',child['id'],self.config.settings.allowed_chat_ids)['status'],'active')
        self.book.change('account',self.config.settings.allowed_chat_ids,{'op':'archive','id':b['id'],'version':b['version']})
        self.assertEqual(self.book.get('account',child['id'],self.config.settings.allowed_chat_ids)['status'],'forgotten')

    async def test_temporal_correction_supersedes_old_value_and_undo_preserves_metadata(self):
        old=self.remember('以前喜欢辣',property_key='diet.spicy')
        new=self.remember('现在不吃辣',property_key='diet.spicy')
        self.assertEqual(self.book.get('account',old['id'],self.config.settings.allowed_chat_ids)['status'],'superseded')
        self.assertEqual([r['id'] for r in self.recall('吃辣')],[new['id']])
        changed=self.book.change('account',self.config.settings.allowed_chat_ids,
            {'op':'update','id':new['id'],'version':new['version'],'tier':'core','locked':True})
        revision=self.store.db.execute('SELECT id FROM memory_revisions WHERE memory_id=? ORDER BY id DESC',(new['id'],)).fetchone()[0]
        restored=self.book.undo('account',self.config.settings.allowed_chat_ids,revision)
        self.assertEqual(restored['tier'],'stable');self.assertFalse(restored['locked'])
        self.assertGreater(restored['version'],changed['version'])

    async def test_role_specific_experiences_do_not_leak_on_character_switch(self):
        self.remember('更喜欢轻声安慰',kind='interaction_rule',scope='chat')
        self.remember('喜欢猫')
        self.config.settings.active_persona_id='another-role'
        rows=self.recall('轻声安慰猫')
        self.assertEqual([r['kind'] for r in rows],['preference'])

    async def test_profile_text_uses_scoped_store_and_can_be_forgotten(self):
        source=event();self.store.observe([source])
        self.runtime.affection.evolve_profile('account',self.config.settings.allowed_chat_ids,'a@chatroom','wxid_alice',
            {'interaction_rule':'难过时先安慰'},self.config.settings,source_key=source['key'])
        row=next(r for r in self.recall('难过安慰') if r['kind']=='interaction_rule')
        self.assertEqual(self.recall('难过安慰',chat='b@chatroom'),[])
        self.book.change('account',self.config.settings.allowed_chat_ids,{'op':'archive','id':row['id'],'version':row['version']})
        self.assertNotIn('难过时先安慰',self.runtime.affection.dynamic_prompt(source,[source],self.config.settings))
        self.assertEqual(self.recall('难过安慰'),[])

    async def test_background_cooling_does_not_expire_stable_facts_or_core(self):
        stable=self.remember();core=self.remember('重要事件',kind='event',scope='chat',tier='core')
        ordinary=self.remember('一次普通事件',kind='event',scope='chat')
        self.store.db.execute('UPDATE memories SET updated=?',(time.time()-90*86400,))
        self.hub.maintenance(self.config.settings)
        self.assertEqual(self.book.get('account',ordinary['id'],self.config.settings.allowed_chat_ids)['tier'],'cold')
        for row in (stable,core):self.assertEqual(self.book.get('account',row['id'],self.config.settings.allowed_chat_ids)['status'],'active')

    async def test_budget_and_normal_reply_keep_one_model_call(self):
        for n in range(12):self.remember('喜欢猫 '+str(n)+'，'+('长期偏好资料'*25))
        source=event();self.store.observe([source]);self.store.insert(source)
        diagnostics={}
        _,dynamic,_=memory_parts(self.book,self.config.settings,source,[source],diagnostics=diagnostics)
        self.assertLessEqual(diagnostics['estimated_tokens'],self.config.settings.memory_token_budget)
        self.assertLessEqual(diagnostics['count'],8)
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'猫猫很乖呀'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.runtime.gateway.sent,[('text','猫猫很乖呀')])

    async def test_global_switch_and_auto_switch_have_distinct_semantics(self):
        row=self.remember();self.config.settings.memory_consolidation_enabled=False
        self.assertTrue(self.recall())
        self.assertIsNone(self.learn(event()))
        self.config.settings.memory_enabled=False
        self.assertEqual(self.recall(),[])
        self.assertEqual(self.book.model_ops('account',self.config.settings.allowed_chat_ids,[{'op':'get','id':row['id']}],[],True),[])

    async def test_persistent_queue_recovers_after_service_restart_without_double_insert(self):
        source=event();self.store.observe([source])
        payload=({'account':'account','chat':source['chat'],'key':source['key']},[{'key':source['key'],'is_self':0}],[],[],{})
        queue=DurableMemoryQueue(self.hub);queue.put_nowait(payload);queue.put_nowait(payload)
        self.assertEqual(queue.qsize(),1)
        recovered=DurableMemoryQueue(self.hub)
        self.assertEqual(recovered.get_nowait()[0]['key'],source['key']);recovered.task_done()
        self.assertTrue(recovered.empty())

    async def test_queue_retries_unexpected_errors_and_preserves_failed_work(self):
        source=event();self.store.observe([source])
        self.runtime.learning.offer(source,[source],[dict(type='preference',scope='profile',subject=source['sender_id'],
            source_key=source['key'],evidence='我喜欢猫',content='喜欢猫',confidence=.99)])
        original=self.runtime.learning.book.propose
        def fail(*args,**kwargs):raise RuntimeError('temporary database failure')
        self.runtime.learning.book.propose=fail
        for n in range(3):
            with self.assertRaises(RuntimeError):await self.runtime.learning.process_pending()
        saved=self.store.db.execute('SELECT state,attempts FROM memory_work_queue').fetchone()
        self.assertEqual(tuple(saved),('failed',3));self.assertTrue(self.runtime.learning.queue.empty())
        self.runtime.learning.book.propose=original

    async def test_loaded_is_not_evidence_and_reported_use_only_after_valid_loaded_ids(self):
        row=self.remember()
        source=event();self.store.observe([source])
        diagnostics={};memory_parts(self.book,self.config.settings,source,[source],diagnostics=diagnostics)
        self.hub.mark_used('account',[row['id'],9999],diagnostics['ids'])
        current=self.book.get('account',row['id'],self.config.settings.allowed_chat_ids)
        self.assertEqual(current['recall_count'],1);self.assertEqual(current['use_count'],1)
        self.assertEqual(current['tier'],'stable')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_evidence').fetchone()[0],0)

    async def test_minimum_budget_with_many_sources_and_locked_learning_duplicate(self):
        self.config.settings.memory_token_budget=400
        sources=[event(n) for n in range(1,9)]
        self.store.observe(sources)
        row=self.remember(tier='core',locked=True)
        self.learn(sources[0]);self.assertTrue(self.book.get('account',row['id'],self.config.settings.allowed_chat_ids)['locked'])
        diagnostics={};memory_parts(self.book,self.config.settings,sources[0],sources,diagnostics=diagnostics)
        self.assertLessEqual(diagnostics['estimated_tokens'],400)


class MemoryHubApiTests(unittest.TestCase):
    def test_unified_api_authentication_filters_revision_and_restore(self):
        app=create_app(directory(),gateway=Gateway(),engine_factory=Engine,run_background=False)
        app.state.config.save(Settings(account_id='account',allowed_chat_ids=['a@chatroom']))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/memory-hub').status_code,401)
            headers={'Authorization':'Bearer '+app.state.config.admin_token}
            row=client.post('/api/memory-hub',headers=headers,json={'chat':'a@chatroom','subject':'wxid_alice','kind':'preference',
                'scope':'profile','content':'喜欢猫','tier':'core','locked':True}).json()
            self.assertEqual(client.get('/api/memory-hub?tier=core',headers=headers).json()['total'],1)
            self.assertEqual(client.get('/api/memory-hub?chat=not-allowed',headers=headers).status_code,400)
            detail=client.get('/api/memory-hub/'+str(row['id']),headers=headers).json()
            self.assertEqual(detail['memory']['tier'],'core')
            for op in ('archive','restore'):
                row=client.post('/api/memory-hub',headers=headers,json={'op':op,'id':row['id'],'version':row['version']}).json()
            self.assertEqual(row['status'],'active')
            self.assertEqual(client.get('/memory-hub.js',headers=headers).status_code,200)

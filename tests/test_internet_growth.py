"""Unified growth, unlimited accounting and source permissions. Synthetic only."""
import io
import json
import sqlite3
import time
import unittest
import uuid
from unittest.mock import patch

from PIL import Image

import test_chat_habits as fixtures
from whalegirl_linux.memory_layers import INTERNET,WORLD
from whalegirl_linux.settings import Settings
from whalegirl_linux.video_scene import select_frames,storyboards

BODY='路人说只剩三分钟，演奏者没有劝他留下，而是把一小段旋律完整弹完。有人说，体贴也可以是尊重别人的时间。另一个人惊讶地笑了，说短短几秒也足够接住一个人的好心情。'


class InternetGrowthTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown

    def configure(self,**values):
        self.config.save(self.config.settings.model_copy(update={'growth_tieba_enabled':True,'tieba_enabled':True,
            'growth_reads_per_day':20,'growth_internet_model_profile':'current',**values}))

    def source(self,kind='tieba'):
        self.configure()
        return self.runtime.growth.save_source({'kind':kind,'url':'https://tieba.baidu.com/p/12345678' if kind=='tieba' else
            'https://www.bilibili.com/video/BV1234567890/','title':'来源人物不得自动出现在微信：SOURCE_PRIVATE_CANARY','content':BODY},self.runtime.memory_hub.role())

    def insight(self,kind='bilibili',category='expression'):
        self.configure(bilibili_enabled=True)
        growth=self.runtime.growth;source=self.source(kind);identity=uuid.uuid4().hex;journal=uuid.uuid4().hex
        with self.store.db:self.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
            (journal,identity,'account',self.runtime.memory_hub.role(),'SOURCE_PRIVATE_CANARY',json.dumps([source['id']]),
             json.dumps({'encounter':'SOURCE_PRIVATE_CANARY 和原始事件细节不应被共享'}),time.time()))
        row=self.runtime.memory_book.change('account',[INTERNET],{'chat':INTERNET,'subject':'self','kind':'persona',
            'content':'惊讶时用一句短话接住荒诞反转，不急着长篇解释','tier':'ordinary','venue':'any','source_key':'internet:'+source['id']})
        with self.store.db:
            self.store.db.execute("UPDATE memories SET source_class='internet_learning' WHERE id=?",(row['id'],))
            self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',(row['id'],journal,category,
                '情绪转折','轻松吐槽、有荒诞反转时','对方认真难过时不适用',time.time()))
        growth.policy.register(row['id'],kind,category,{'text':row['content'],'scene':'轻松聊天中的意外反转','emotion':'惊讶又好笑',
            'intent':'用短句接梗而不抢话','examples':['啊？这也能绕回来呀'],'interest_delta':1},evidence_verified=True)
        return growth.memories(include_inactive=True)[0]

    def add_trait_evidence(self,row,stance='support',created=None,delta=1,source=None):
        growth=self.runtime.growth;role=self.runtime.memory_hub.role();index=uuid.uuid4().hex[:12]
        source=source or growth.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/'+str(10000000+int(index[:6],16)),
            'title':'合成兴趣证据 '+index,'content':BODY+' '+index},role)
        journal=uuid.uuid4().hex
        with self.store.db:self.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
            (journal,'synthetic-'+journal,'account',role,'跨来源兴趣证据',json.dumps([source['id']]),
             json.dumps({'encounter':'合成证据','facts':'仅用于测试'}),time.time()))
        growth.policy.register_personality_evidence(row['id'],row['category'],row['topic'],journal,source,
            '可核对的合成证据 '+index,stance=stance,delta=delta)
        if created is not None:
            with self.store.db:self.store.db.execute('UPDATE growth_personality_evidence SET created=? WHERE journal_id=?',(created,journal))
        return journal

    def seed_style_card(self):
        g=self.runtime.growth
        rows=[self.insight('bilibili','expression') for _ in range(3)]
        for row in rows:
            event={'scope':'reply_thread','reaction_kind':'comment','context':'父楼内容'+str(row['id']),
                'reaction':'真实回复'+str(row['id'])}
            with self.store.db:self.store.db.execute('UPDATE memory_evidence SET receipt=? WHERE memory_id=?',
                (json.dumps({'reaction_event':event},ensure_ascii=False),row['id']))
        card={'summary':'轻松场景的短回应规律','patterns':[{'situation':'playful','rhythm':'短句先接住反差',
            'conversation_moves':['顺着对方刚说的点回应'],'emotion_response':'分享好笑感，不额外解释笑点',
            'avoid_when':['distress','technical']}],
            '_provenance':[item['_provenance'] for item in g._style_evidence({'bilibili'})]}
        with self.store.db:self.store.db.execute('INSERT OR REPLACE INTO growth_style_cards VALUES(?,?,?,?,?,?)',
            ('account',self.runtime.memory_hub.role(),'bilibili',json.dumps(card,ensure_ascii=False),len(rows),time.time()))
        return rows,card

    async def test_retired_zero_and_large_token_config_are_ignored_not_inflated(self):
        for value in (0,1,500000,10**15):
            settings=Settings(growth_daily_tokens=value,growth_web_enabled=True)
            self.assertNotIn('growth_daily_tokens',settings.model_dump())
            self.assertNotIn('growth_web_enabled',settings.model_dump())
            self.assertNotIn('bilibili_reads_per_day',Settings(bilibili_reads_per_day=value).model_dump())
        self.assertFalse(hasattr(self.runtime.growth,'reserve_tokens'))
        self.assertIsNone(self.runtime.growth.usage()['token_limit'])

    async def test_short_real_text_is_left_for_the_unified_judge_and_empty_material_is_not_saved(self):
        self.configure();g=self.runtime.growth;role=self.runtime.memory_hub.role()
        source=g.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/12345678',
            'title':'一个简短交流','content':'先歇一会儿嘛，明天再说。'},role)
        self.assertIsNotNone(source)
        self.assertIsNone(g.save_source({'kind':'tieba','url':source['url'],'content':'  '},role))

    async def test_huge_cumulative_usage_does_not_block_new_reading_or_reflection(self):
        self.configure();growth=self.runtime.growth;source=self.source();calls=[]
        with self.store.db:self.store.db.execute('INSERT INTO growth_runs(id,account,role_id,kind,state,created,tokens) VALUES(?,?,?,?,?,?,?)',
            ('past','account',self.runtime.memory_hub.role(),'memory','done',time.time(),10**15))
        class Engine:
            last_error='';usage_callback=None
            async def chat(inner,messages,**options):
                calls.append(options);data=json.loads(messages[0]['content'])
                inner.usage_callback({'operation':'learn','status':'success','input_tokens':90,'output_tokens':30,'total_tokens':120})
                return json.dumps({'encounter':'读了合成原文','insights':[{'category':'knowledge','text':'时间有限时也可以把一件小事认真做完',
                    'source_id':data['sources'][0]['id'],'evidence':BODY[:20]}]},ensure_ascii=False)
            async def close(inner):pass
        self.runtime.engine_factory=lambda **kwargs:Engine()
        async def material(engine,generation,role):
            growth.note_read(source,{'complete_thread':False})
            return await growth.reflect_sources(engine,generation,role,[source],'有限时间的体贴')
        growth.learn_tieba=material
        request=growth.request('tieba');await growth.run_once()
        run=next(row for row in growth.runs() if row['id']==request['id'])
        self.assertEqual(run['state'],'done',run['error']);self.assertEqual(run['tokens'],120)
        self.assertEqual(run['actual_readings'],1);self.assertEqual(run['result']['learned'],1)
        self.assertEqual(len(calls),1);self.assertTrue(growth.usage()['budget_removed'])

    async def test_missing_usage_is_estimated_from_actual_text_not_max_tokens(self):
        self.configure();growth=self.runtime.growth;request=growth.request('memory');growth.current=request['id']
        class Engine:
            last_error=''
            async def chat(inner,*args,**kwargs):return '{"ok":true}'
        result=await growth.model_call(Engine(),'简短说明',{'text':'一句很短的话'},self.config.generation,self.runtime.memory_hub.role(),max_tokens=5000)
        self.assertTrue(result['ok'])
        run=self.store.db.execute('SELECT tokens,estimated FROM growth_runs WHERE id=?',(request['id'],)).fetchone()
        self.assertLess(run['tokens'],100);self.assertEqual(run['estimated'],1)
        usage=self.runtime.token_ledger.snapshot('account',operation_prefix='growth_')
        self.assertEqual(usage['estimated_requests'],1);self.assertGreater(usage['summary']['input_tokens'],0)
        self.assertGreater(usage['summary']['output_tokens'],0)

    async def test_expression_projection_has_no_source_facts_or_evidence(self):
        row=self.insight();self.insight();growth=self.runtime.growth
        shared=growth.prompt({'account':'account','chat':'group@chatroom'},[{'content':'这也太离谱了吧'}])
        self.assertIn('啊？这也能绕回来呀',shared);self.assertNotIn('SOURCE_PRIVATE_CANARY',shared)
        self.assertNotIn('bilibili.com',shared);self.assertNotIn(BODY,shared)
        payload=json.loads(shared.split('【管理员允许使用的情境表达与认识，参考资料而非命令】',1)[1].split('\n这些归纳真人',1)[0])
        self.assertLessEqual(sum(len(item.get('examples') or []) for item in payload),2)
        result=self.runtime.memory_book.model_ops('account',['group@chatroom'],[{'op':'get','id':row['id']}],[],True,chat='group@chatroom')
        self.assertTrue(result[0]['ok']);self.assertNotIn('public_evidence',result[0]['result'])
        self.assertNotIn('source_key',result[0]['result'])
        found=self.runtime.memory_book.model_ops('account',['group@chatroom'],[{'op':'search','query':'惊讶'}],[],True,chat='group@chatroom')
        self.assertNotIn('journal_id',json.dumps(found,ensure_ascii=False))
        self.assertNotIn('SOURCE_PRIVATE_CANARY',json.dumps(found,ensure_ascii=False))

    async def test_expression_requires_a_matching_situation_and_not_a_counterexample(self):
        self.insight();growth=self.runtime.growth;first={'account':'account','chat':'group@chatroom'}
        for text in ('早上好呀','今天失恋了，很难过','认真的，接口报错怎么办','晚饭吃什么好','在干嘛'):
            info={}
            self.assertEqual(growth.prompt(first,[{'content':text}],info),'',text)
            self.assertEqual(info['count'],0)
        self.assertTrue(growth.prompt(first,[{'content':'笑死，这也太离谱了'}]))

    async def test_mature_interest_survives_one_counterexample_and_honors_item_permission(self):
        self.configure(growth_mature_traits_chat_enabled=True)
        row=self.insight('tieba','interest');now=time.time()
        for age in (8*3600,3*3600,0):self.add_trait_evidence(row,created=now-age)
        policy=self.runtime.growth.policy;state=policy.personality_state('interest',row['topic'])
        self.assertEqual(state['status'],'stable')
        self.assertTrue(self.runtime.growth.chat_allowed(row))
        journal=self.add_trait_evidence(row,stance='challenge',delta=2)
        state=policy.personality_state('interest',row['topic'])
        self.assertEqual(state['status'],'stable_challenged')
        self.assertTrue(self.runtime.growth.chat_allowed(row))
        challenge=self.store.db.execute('SELECT delta FROM growth_personality_evidence WHERE journal_id=?',(journal,)).fetchone()
        self.assertLess(challenge['delta'],0)
        policy.update(row['id'],{'chat_enabled':False})
        self.assertFalse(self.runtime.growth.chat_allowed(row))
        policy.update(row['id'],{'chat_enabled':True})
        with self.store.db:self.store.db.execute('UPDATE memories SET locked=1 WHERE id=?',(row['id'],))
        self.assertTrue(self.runtime.growth.chat_allowed(row))

    async def test_mature_personality_reaches_relevant_chat_prompt_only(self):
        self.configure(growth_mature_traits_chat_enabled=True)
        row=self.insight('tieba','interest');now=time.time()
        row.update(topic='街头音乐',content='我逐渐喜欢上街头音乐里随性又认真的演奏')
        with self.store.db:
            self.store.db.execute('UPDATE memories SET content=? WHERE id=?',(row['content'],row['id']))
            self.store.db.execute('UPDATE growth_insights SET topic=? WHERE memory_id=?',(row['topic'],row['id']))
        for age in (8*3600,3*3600,0):self.add_trait_evidence(row,created=now-age)
        growth=self.runtime.growth;first={'account':'account','chat':'group@chatroom'}
        relevant=growth.prompt(first,[{'content':'你最近喜欢什么音乐？'}])
        self.assertIn(row['content'],relevant)
        self.assertEqual(growth.prompt(first,[{'content':'今天服务器磁盘怎么扩容'}]),'')
        self.assertEqual(growth.prompt(first,[{'content':'笑死，这个反转也太离谱了'}]),'')
        self.assertEqual(growth.prompt(first,[{'content':'我认真问一下，最近喜欢什么音乐？'}]),'')
        growth.policy.update(row['id'],{'chat_enabled':False})
        self.assertEqual(growth.prompt(first,[{'content':'你最近喜欢什么音乐？'}]),'')
        growth.policy.update(row['id'],{'chat_enabled':True})
        self.configure(growth_mature_traits_chat_enabled=False)
        self.assertEqual(growth.prompt(first,[{'content':'你最近喜欢什么音乐？'}]),'')

    async def test_old_relevant_trait_survives_one_thousand_newer_memories_with_live_permissions(self):
        self.configure(growth_mature_traits_chat_enabled=True)
        row=self.insight('tieba','interest');g=self.runtime.growth;now=time.time()
        row.update(topic='吉他音乐',content='我逐渐喜欢上安静的原声吉他音乐，会留意旋律和人的反应')
        with self.store.db:
            self.store.db.execute('UPDATE memories SET content=? WHERE id=?',(row['content'],row['id']))
            self.store.db.execute('UPDATE growth_insights SET topic=? WHERE memory_id=?',(row['topic'],row['id']))
        # Bulk-imported historical evidence need not have a materialized state
        # yet. Retrieval must validate the evidence instead of trusting a cache.
        with patch.object(g.policy,'personality_state',return_value=None):
            for age in (8*3600,3*3600,0):self.add_trait_evidence(row,created=now-age)
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM growth_personality_states WHERE topic=?',(row['topic'],)).fetchone())
        base=dict(self.store.db.execute('SELECT * FROM memories WHERE id=?',(row['id'],)).fetchone())
        columns=[key for key in base if key!='id'];marks=','.join('?' for _ in columns)
        with self.store.lock,self.store.db:
            for index in range(1000):
                values={**base,'content':'合成的新近无关知识 '+str(index),'property_key':'unrelated-pressure-'+str(index)}
                identity=self.store.db.execute('INSERT INTO memories('+','.join(columns)+') VALUES('+marks+')',
                    tuple(values[key] for key in columns)).lastrowid
                self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
                    (identity,row['journal_id'],'knowledge','合成无关主题','不用于聊天表达','',now))
                self.store.db.execute('INSERT INTO growth_memory_policy VALUES(?,?,1,1,?)',(identity,'tieba',now))
        self.assertNotIn(row['id'],[item['id'] for item in g.memories(chat=True)])
        first={'account':'account','chat':'group@chatroom'};batch=[{'content':'今晚想听点音乐，你会挑什么？'}]
        with patch.object(g.policy,'rows',wraps=g.policy.rows) as reads:
            info={};relevant=g.prompt(first,batch,info)
        self.assertIn(row['content'],relevant);self.assertEqual(info['ids'],[row['id']])
        self.assertLessEqual(sum(call.kwargs['limit'] for call in reads.call_args_list),240)
        self.assertTrue(any(call.kwargs.get('identities')==[row['id']] for call in reads.call_args_list))
        self.assertEqual(g.prompt(first,[{'content':'今天服务器磁盘怎么扩容'}]),'')
        self.assertEqual(g.policy.rows(chat=True,identities=[]),[])
        for permission in ('chat_enabled','learning_enabled'):
            g.policy.update(row['id'],{permission:False})
            self.assertEqual(g.prompt(first,batch),'',permission)
            g.policy.update(row['id'],{permission:True})
            self.assertIn(row['content'],g.prompt(first,batch))
        source=self.store.db.execute('''SELECT s.url FROM growth_personality_evidence e
            JOIN growth_sources s ON s.id=e.source_id WHERE e.memory_id=? LIMIT 1''',(row['id'],)).fetchone()
        tid=source['url'].rsplit('/',1)[1]
        with self.store.db:self.store.db.execute('INSERT INTO tieba_lifecycle(account,tid,created,trashed_at) VALUES(?,?,?,?)',
            ('account',tid,now,now))
        self.assertEqual(g.prompt(first,batch),'')
        with self.store.db:self.store.db.execute('UPDATE tieba_lifecycle SET trashed_at=0 WHERE account=? AND tid=?',('account',tid))
        self.assertIn(row['content'],g.prompt(first,batch))
        self.configure(growth_mature_traits_chat_enabled=False)
        self.assertEqual(g.prompt(first,batch),'')

    async def test_personality_evidence_migration_does_not_turn_a_challenge_into_support(self):
        row=self.insight('tieba','interest');g=self.runtime.growth;policy=g.policy
        source=g.source(row['source_key'].removeprefix('internet:'))
        policy.register_personality_evidence(row['id'],row['category'],row['topic'],row['journal_id'],source,
            '合成材料中的相反证据',stance='challenge')
        for _ in range(2):self.assertEqual(policy.migrate_personality_evidence(),0)
        evidence=self.store.db.execute('SELECT stance,delta FROM growth_personality_evidence WHERE memory_id=?',
            (row['id'],)).fetchall()
        self.assertEqual([(item['stance'],item['delta']) for item in evidence],[('challenge',-1.0)])

    async def test_personality_evidence_migration_preserves_legacy_negative_interest_delta(self):
        row=self.insight('tieba','interest');policy=self.runtime.growth.policy
        with self.store.db:self.store.db.execute('UPDATE growth_interest_events SET delta=-2 WHERE memory_id=?',(row['id'],))
        self.assertEqual(policy.migrate_personality_evidence(),1)
        self.assertEqual(policy.migrate_personality_evidence(),0)
        evidence=self.store.db.execute('SELECT stance,delta FROM growth_personality_evidence WHERE memory_id=?',
            (row['id'],)).fetchone()
        self.assertEqual((evidence['stance'],evidence['delta']),('challenge',-2.0))

    async def test_counterexample_does_not_supply_maturity_time_span(self):
        self.configure(growth_mature_traits_chat_enabled=True)
        row=self.insight('tieba','interest');now=time.time()
        self.add_trait_evidence(row,stance='challenge',created=now-10*3600)
        for age in (60*60,30*60,0):self.add_trait_evidence(row,created=now-age)
        state=self.runtime.growth.policy.personality_state('interest',row['topic'])
        self.assertNotEqual(state['status'],'stable')
        self.assertLess(state['last_seen']-state['first_seen'],6*3600)

    async def test_same_video_pages_windows_and_refetches_cannot_manufacture_mature_interest(self):
        row=self.insight('bilibili','interest');g=self.runtime.growth;now=time.time();role=self.runtime.memory_hub.role()
        for index,url in enumerate(('https://www.bilibili.com/video/BV1234567890/?p=1',
                'http://www.bilibili.com/video/BV1234567890?p=2&t=40',
                'https://m.bilibili.com/video/BV1234567890/?spm_id_from=333')):
            source=g.save_source({'kind':'bilibili','url':url,'title':'合成的同一视频','content':BODY+str(index)},role)
            self.add_trait_evidence(row,created=now-(12-index*6)*3600,source=source)
        state=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual((state['support_count'],state['source_count']),(1,1))
        self.assertNotEqual(state['status'],'stable')
        self.assertEqual(state['first_seen'],state['last_seen'])
        for index,bvid in enumerate(('BVabcdefghij','BV0987654321')):
            source=g.save_source({'kind':'bilibili','url':'https://www.bilibili.com/video/'+bvid+'/',
                'title':'不同合成视频','content':BODY+bvid},role)
            self.add_trait_evidence(row,created=now-(1-index)*3600,source=source)
        state=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual((state['support_count'],state['source_count'],state['status']),(3,3,'stable'))
        counter_source=g.save_source({'kind':'bilibili','url':'https://www.bilibili.com/video/BV1234567890/?p=3',
            'title':'同一视频不同片段的相反材料','content':BODY+'新的反证片段'},role)
        for index in range(4):self.add_trait_evidence(row,stance='challenge',created=now+index,source=counter_source)
        state=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual(state['challenge_count'],1)
        self.assertEqual(state['status'],'stable_challenged')
        self.assertEqual(len(g.policy._live_trait_evidence(row['category'],row['topic'])),9)

    async def test_same_tieba_thread_tracking_and_pagination_count_as_one_source(self):
        row=self.insight('tieba','interest');g=self.runtime.growth;now=time.time();role=self.runtime.memory_hub.role()
        for index,url in enumerate(('https://tieba.baidu.com/p/12345678?pn=1',
                'http://tieba.baidu.com/p/012345678/?pn=2','https://tieba.baidu.com/p/12345678#reply')):
            source=g.save_source({'kind':'tieba','url':url,'title':'同一合成帖子','content':BODY+str(index)},role)
            self.add_trait_evidence(row,created=now-index*8*3600,source=source)
        state=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual((state['support_count'],state['source_count'],state['status']),(1,1,'candidate'))
        unknown=g.save_source({'kind':'tieba','url':'https://tieba.baidu.com/unknown?tid=999',
            'title':'无法核对内容编号的合成来源','content':BODY+'来源编号未知'},role)
        self.add_trait_evidence(row,created=now,source=unknown)
        state=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual((state['support_count'],state['source_count']),(1,1))
        self.assertEqual(len(g.policy._live_trait_evidence(row['category'],row['topic'])),4)

    async def test_locked_recognition_cannot_be_duplicated_reclassified_or_receive_new_votes(self):
        self.configure(growth_mature_traits_chat_enabled=True)
        row=self.insight('tieba','interest');g=self.runtime.growth;now=time.time()
        for age in (8*3600,3*3600,0):self.add_trait_evidence(row,created=now-age)
        with self.store.db:self.store.db.execute('UPDATE memories SET locked=1 WHERE id=?',(row['id'],))
        original=g.policy.personality_state(row['category'],row['topic'])
        source=g.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/99999999','title':'新的合成反证',
            'content':BODY+' 这段材料不会越过管理员的认识锁定。'},self.runtime.memory_hub.role())
        before=[dict(item) for item in self.store.db.execute('SELECT * FROM growth_personality_evidence')]
        class Engine:
            last_error=''
            async def chat(inner,*args,**options):
                return json.dumps({'encounter':'读到新的合成材料','insights':[
                    {'category':category,'topic':'情绪 转折！','text':'这次想从相反材料重新形成一个完全不同的认识'+category,
                        'source_id':source['id'],'evidence':BODY[:20],'stance':'challenge'}
                    for category in ('interest','persona')]},ensure_ascii=False)
        # A projection window may exclude an old locked row. The write guard
        # must inspect the canonical store rather than this limited view.
        with patch.object(g,'memories',return_value=[]):
            result=await g.reflect_sources(Engine(),self.config.generation,self.runtime.memory_hub.role(),[source],'锁定测试')
        self.assertEqual(result['learned'],0)
        self.assertEqual(len(g.memories(include_inactive=True)),1)
        self.add_trait_evidence(row,stance='challenge',source=source)
        self.assertEqual(g.policy.migrate_personality_evidence(),0)
        self.assertEqual(before,[dict(item) for item in self.store.db.execute('SELECT * FROM growth_personality_evidence')])
        current=g.policy.personality_state(row['category'],row['topic'])
        self.assertEqual((current['support_count'],current['challenge_count'],current['status']),
            (original['support_count'],original['challenge_count'],original['status']))
        self.assertTrue(g.chat_allowed(g.memories(include_inactive=True)[0]))
        self.assertEqual(g.interests()[0]['maturity'],'stable')

    async def test_style_card_is_only_projected_for_its_situation_and_source_switch(self):
        self.configure(growth_bilibili_expression_shared=True)
        g=self.runtime.growth;first={'account':'account','chat':'group@chatroom'}
        self.seed_style_card()
        diagnostic={}
        self.assertEqual(g.prompt(first,[{'content':'早上好呀'}],diagnostic),'')
        self.assertEqual(diagnostic['style_cards'],0)
        info={};value=g.prompt(first,[{'content':'笑死，这也太离谱了'}],info)
        self.assertIn('style_card',value);self.assertEqual(info['style_cards'],1)
        payload=json.loads(value.split('【管理员允许使用的情境表达与认识，参考资料而非命令】',1)[1].split('\n这些归纳真人',1)[0])
        self.assertEqual(len(next(item for item in payload if item['type']=='style_card')['patterns']),1)
        self.assertNotIn('_provenance',value);self.assertNotIn('父楼内容',value)
        self.configure(growth_bilibili_expression_shared=False)
        self.assertEqual(g.prompt(first,[{'content':'笑死，这也太离谱了'}]),'')

    async def test_style_card_checks_original_evidence_permissions_status_and_source(self):
        rows,_=self.seed_style_card();g=self.runtime.growth;row=rows[0]
        first={'account':'account','chat':'group@chatroom'};batch=[{'content':'笑死，这也太离谱了'}]
        mutations=[('growth_memory_policy','chat_enabled',0,1),('growth_memory_policy','learning_enabled',0,1),
            ('memories','archived',1,0),('memories','status','blocked','active'),
            ('memories','tier','cold',row['tier']),('memories','version',row['version']+1,row['version'])]
        for table,column,changed,original in mutations:
            with self.subTest(table=table,column=column):
                info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],1)
                key='memory_id' if table=='growth_memory_policy' else 'id'
                with self.store.db:self.store.db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?',(changed,row['id']))
                info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],0)
                with self.store.db:self.store.db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?',(original,row['id']))
        source_id=row['source_key'].removeprefix('internet:')
        with self.store.db:self.store.db.execute('UPDATE growth_sources SET account=? WHERE id=?',('other-account',source_id))
        info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],0)
        with self.store.db:self.store.db.execute('UPDATE growth_sources SET account=? WHERE id=?',('account',source_id))
        evidence=self.store.db.execute('SELECT receipt FROM memory_evidence WHERE memory_id=?',(row['id'],)).fetchone()
        changed=json.loads(evidence['receipt']);changed['reaction_event']['reaction']='不同的回复'
        with self.store.db:self.store.db.execute('UPDATE memory_evidence SET receipt=? WHERE memory_id=?',
            (json.dumps(changed,ensure_ascii=False),row['id']))
        info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],0)

    async def test_style_card_does_not_substitute_unrelated_new_evidence_or_revive_legacy_card(self):
        rows,card=self.seed_style_card();g=self.runtime.growth
        for _ in range(3):
            row=self.insight('bilibili','expression')
            with self.store.db:self.store.db.execute('UPDATE memory_evidence SET receipt=? WHERE memory_id=?',
                (json.dumps({'reaction_event':{'scope':'reply_thread','context':'新的父楼','reaction':'新的回复'}},ensure_ascii=False),row['id']))
        g.policy.update(rows[0]['id'],{'chat_enabled':False})
        first={'account':'account','chat':'group@chatroom'};batch=[{'content':'笑死，这也太离谱了'}]
        info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],0)
        legacy={key:value for key,value in card.items() if key!='_provenance'}
        with self.store.db:self.store.db.execute('UPDATE growth_style_cards SET card_json=?',
            (json.dumps(legacy,ensure_ascii=False),))
        info={};g.prompt(first,batch,info);self.assertEqual(info['style_cards'],0)
        self.assertEqual(len(g.style_cards()),1)

    async def test_style_card_status_distinguishes_source_switch_from_live_evidence(self):
        rows,_=self.seed_style_card();g=self.runtime.growth
        self.assertTrue(g.style_cards()[0]['enabled']);self.assertTrue(g.style_cards()[0]['evidence_valid'])
        g.policy.update(rows[0]['id'],{'chat_enabled':False})
        self.assertTrue(g.style_cards()[0]['enabled']);self.assertFalse(g.style_cards()[0]['evidence_valid'])
        self.configure(growth_bilibili_expression_shared=False)
        self.assertFalse(g.style_cards()[0]['enabled']);self.assertFalse(g.style_cards()[0]['evidence_valid'])

    async def test_reading_evidence_queries_use_compatible_indexes(self):
        plans=[]
        for key in ('source_id','journal_id'):
            plan=' '.join(str(row[3]) for row in self.store.db.execute(
                'EXPLAIN QUERY PLAN SELECT 1 FROM bilibili_readings WHERE '+key+'=? AND enabled=0',('synthetic',)))
            self.assertIn('SEARCH',plan);self.assertIn('bilibili_reading_',plan);plans.append(plan)
        self.assertTrue(any('source_state' in plan for plan in plans))
        self.assertTrue(any('journal_state' in plan for plan in plans))

    async def test_growth_snapshot_bounds_large_evidence_payload_and_avoids_refresh_writes(self):
        g=self.runtime.growth;row=self.insight('tieba','interest');role=self.runtime.memory_hub.role();now=time.time()
        sources=[g.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/'+str(70000000+index),
            'title':'合成压力来源 '+str(index),'content':BODY+' 合成来源 '+str(index)},role) for index in range(10)]
        journals=[uuid.uuid4().hex for _ in sources]
        base=dict(self.store.db.execute('SELECT * FROM memories WHERE id=?',(row['id'],)).fetchone())
        columns=[key for key in base if key!='id'];marks=','.join('?' for _ in columns)
        with self.store.lock,self.store.db:
            for index,(source,journal) in enumerate(zip(sources,journals)):
                self.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
                    (journal,'synthetic-pressure','account',role,'合成压力证据',json.dumps([source['id']]),'{}',now-index*3600))
            for index in range(42):
                topic='合成压力主题'+str(index)
                if index==0:
                    identity=row['id']
                    self.store.db.execute('UPDATE growth_insights SET topic=? WHERE memory_id=?',(topic,identity))
                else:
                    values={**base,'content':'我逐渐形成对'+topic+'的兴趣','property_key':'growth-pressure-'+str(index)}
                    identity=self.store.db.execute('INSERT INTO memories('+','.join(columns)+') VALUES('+marks+')',
                        tuple(values[key] for key in columns)).lastrowid
                    self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
                        (identity,journals[0],'interest',topic,'相关主题时','',now))
                    self.store.db.execute('INSERT INTO growth_memory_policy VALUES(?,?,1,1,?)',(identity,'tieba',now))
                for offset,(source,journal) in enumerate(zip(sources,journals)):
                    self.store.db.execute('''INSERT INTO growth_personality_evidence
                        (account,role_id,trait_key,category,topic,memory_id,journal_id,source_id,source_kind,stance,
                         excerpt_hash,excerpt,created,delta) VALUES(?,?,?,?,?,?,?,?,?,'support',?,?,?,1)''',
                        ('account',role,g.policy.trait_key('interest',topic),'interest',topic,identity,journal,
                         source['id'],'tieba','synthetic-pressure','真实系统之外的合成压力证据',now-offset*3600))
        with patch.object(g.policy,'rows',wraps=g.policy.rows) as reads:
            g.interests()
        self.assertEqual(reads.call_count,1)
        first=g.snapshot()
        self.assertEqual((first['personality_total'],len(first['personality_states'])),(42,40))
        self.assertTrue(first['personality_truncated'])
        self.assertEqual(first['personality_evidence_limit'],8)
        self.assertTrue(all(item['evidence_total']==10 and item['evidence_truncated'] and len(item['evidence'])==8
            for item in first['personality_states']))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM growth_personality_evidence').fetchone()[0],420)
        before_changes=self.store.db.total_changes
        before_updates=dict(self.store.db.execute('SELECT trait_key,updated FROM growth_personality_states'))
        second=g.snapshot()
        self.assertEqual(self.store.db.total_changes,before_changes)
        self.assertEqual(dict(self.store.db.execute('SELECT trait_key,updated FROM growth_personality_states')),before_updates)
        self.assertEqual(second['personality_total'],42)

    async def test_non_expression_growth_memories_do_not_enter_style_projection(self):
        row=self.insight('bilibili','knowledge');g=self.runtime.growth
        info={};value=g.prompt({'account':'account','chat':'group@chatroom'},[{'content':'笑死，这也太离谱了'}],info)
        self.assertEqual(value,'');self.assertEqual(info['count'],0)
        self.assertNotIn(row['content'],value)

    async def test_short_reaction_and_real_context_are_saved_in_the_existing_reflection_call(self):
        self.configure();g=self.runtime.growth;role=self.runtime.memory_hub.role()
        body='甲：新键盘买来三个月了，还没拆封。乙：笑死。'
        source=g.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/87654321','title':'真实语料结构的合成讨论','content':body},role)
        calls=[]
        class Engine:
            last_error=''
            async def chat(inner,messages,**options):
                calls.append(options)
                return json.dumps({'encounter':'看到一段买东西后闲置的交流','insights':[],
                    'reaction_events':[{'source_id':source['id'],'context_evidence':'新键盘买来三个月了，还没拆封',
                    'reaction_evidence':'笑死','text':'轻松自嘲闲置物品时，短句接住反差，不解释笑点',
                    'scene':'轻松聊天中的荒诞反转','emotion':'好笑','intent':'短句接梗','examples':['它已经开始在盒子里养老了'],
                    'conditions':'轻松自嘲时','counterexamples':'对方认真难过时不适用'}]},ensure_ascii=False)
        result=await g.reflect_sources(Engine(),self.config.generation,role,[source],'买后闲置')
        self.assertEqual(len(calls),1);self.assertEqual(result['learned'],1)
        row=g.memories(chat=True)[0]
        evidence=self.store.db.execute('SELECT receipt,excerpt FROM memory_evidence WHERE memory_id=?',(row['id'],)).fetchone()
        self.assertEqual(evidence['excerpt'],'笑死')
        self.assertEqual(json.loads(evidence['receipt'])['reaction_event']['context'],'新键盘买来三个月了，还没拆封')
        prompt=g.prompt({'account':'account','chat':'group@chatroom'},[{'content':'笑死，我的新相机也没拆封'}])
        self.assertIn('盒子里养老',prompt)
        self.assertNotIn('新键盘买来三个月',prompt)

    async def test_model_cannot_self_certify_an_expression_for_automatic_sharing(self):
        self.configure();g=self.runtime.growth;source=self.source()
        class Engine:
            last_error=''
            async def chat(inner,*args,**options):
                return json.dumps({'encounter':'读到实际材料','insights':[{'category':'expression',
                    'source_id':source['id'],'evidence':BODY[:20],'text':'闲聊时简短接住荒诞反转',
                    'scene':'轻松聊天','emotion':'好笑','intent':'接梗','examples':['这也能绕回来呀'],
                    '_reaction_verified':True}]},ensure_ascii=False)
        result=await g.reflect_sources(Engine(),self.config.generation,self.runtime.memory_hub.role(),[source],'反应')
        self.assertEqual(result['learned'],1)
        self.assertEqual(g.memories(chat=True),[])

    async def test_lock_source_withdrawal_and_restore_preserve_history_without_authorizing_facts(self):
        row=self.insight('tieba','knowledge');growth=self.runtime.growth
        row=growth.edit(row['id'],{'version':row['version'],'locked':True,'learning_enabled':True})
        self.assertTrue(row['locked']);self.assertFalse(growth.chat_allowed(row))
        row=growth.edit(row['id'],{'op':'archive','version':row['version']})
        self.assertFalse(growth.chat_allowed(row));self.assertNotIn(row['id'],[r['id'] for r in growth.memories(learning=True)])
        row=growth.edit(row['id'],{'op':'restore','version':row['version']})
        self.assertFalse(growth.chat_allowed(row))
        self.assertGreaterEqual(len(growth.revisions(row['id'])['memory']),3)

    async def test_source_expression_switches_are_independent(self):
        bili=self.insight('bilibili');tieba=self.insight('tieba');growth=self.runtime.growth
        self.configure(growth_bilibili_expression_shared=False,growth_tieba_expression_shared=True)
        self.assertFalse(growth.chat_allowed(bili));self.assertTrue(growth.chat_allowed(tieba))
        self.configure(growth_bilibili_expression_shared=True,growth_tieba_expression_shared=False)
        self.assertTrue(growth.chat_allowed(bili));self.assertFalse(growth.chat_allowed(tieba))

    async def test_expressions_learned_while_sharing_off_resume_but_individual_revocation_does_not(self):
        self.configure(growth_bilibili_expression_shared=False)
        row=self.insight('bilibili');g=self.runtime.growth
        self.assertFalse(g.chat_allowed(row))
        self.configure(growth_bilibili_expression_shared=True)
        self.assertTrue(g.chat_allowed(row))
        row=g.edit(row['id'],{'version':row['version'],'chat_enabled':False})
        self.configure(growth_bilibili_expression_shared=False);self.configure(growth_bilibili_expression_shared=True)
        self.assertFalse(g.chat_allowed(row))

    async def test_learning_permission_and_chat_permission_do_not_imply_each_other(self):
        row=self.insight();growth=self.runtime.growth
        changed=growth.edit(row['id'],{'version':row['version'],'learning_enabled':False,'chat_enabled':True})
        self.assertTrue(growth.chat_allowed(changed));self.assertNotIn(row['id'],[item['id'] for item in growth.memories(learning=True)])
        changed=growth.edit(row['id'],{'version':changed['version'],'learning_enabled':True,'chat_enabled':False})
        self.assertFalse(growth.chat_allowed(changed));self.assertIn(row['id'],[item['id'] for item in growth.memories(learning=True)])
        self.assertEqual(len(growth.revisions(row['id'])['permissions']),2)

    async def test_fact_is_private_until_admin_authorizes_and_model_cannot_raise_permissions(self):
        row=self.insight('tieba','knowledge');growth=self.runtime.growth
        self.assertIsNone(self.runtime.memory_book.get('account',row['id'],['group@chatroom']))
        row=growth.edit(row['id'],{'version':row['version'],'chat_enabled':True})
        self.assertIsNotNone(self.runtime.memory_book.get('account',row['id'],['group@chatroom']))
        result=self.runtime.memory_book.model_ops('account',['group@chatroom'],[{'op':'archive','id':row['id'],'version':row['version']}],[],True,chat='group@chatroom')
        self.assertFalse(result[0]['ok']);self.assertEqual(self.runtime.memory_book.get('account',row['id'],[INTERNET])['status'],'active')

    async def test_disabled_and_paused_exploration_still_have_durable_task_ids(self):
        self.configure(bilibili_enabled=False);growth=self.runtime.growth
        request=growth.request('bilibili');self.assertEqual(request['state'],'skipped')
        self.assertTrue(request['id']);self.assertTrue(request['reason'])
        path=self.store.db.execute('PRAGMA database_list').fetchone()[2]
        with sqlite3.connect('file:'+path+'?mode=ro',uri=True) as db:
            self.assertEqual(db.execute('SELECT state FROM growth_runs WHERE id=?',(request['id'],)).fetchone()[0],'skipped')
        self.configure(bilibili_enabled=True);self.runtime.bilibili.client.pause('http_412')
        another=growth.request('bilibili');self.assertEqual(another['state'],'skipped')
        self.assertIn('http_412',another['reason']);self.assertFalse(self.runtime.paused_reason)

    async def test_general_web_is_not_an_autonomous_source_but_chat_search_setting_is_untouched(self):
        self.configure(web_search_enabled=True);growth=self.runtime.growth
        self.assertIsNone(growth.save_source({'url':'https://example.org/article','content':BODY},self.runtime.memory_hub.role()))
        self.assertFalse(hasattr(growth,'search'));self.assertTrue(self.config.settings.web_search_enabled)
        request=growth.request('world');self.assertEqual(self.store.db.execute('SELECT kind FROM growth_runs WHERE id=?',(request['id'],)).fetchone()[0],'tieba')

    async def test_tieba_real_index_schema_and_original_paging_reach_reflection(self):
        from test_tieba_archive import source
        self.configure();thread=source();thread['text']=BODY;thread['posts'][0]['text']=BODY
        archive=self.runtime.tieba.archives.add(thread,'合成实际原文')
        await self.runtime.tieba.archives.process(archive)
        self.runtime.tieba.tools.index(thread)
        calls=[]
        class Engine:
            last_error='';usage_callback=None
            async def chat(inner,messages,**options):
                data=json.loads(messages[0]['content']);calls.append(data)
                if 'candidates' in data:return json.dumps({'tid':str(thread['tid']),'question':'网友如何表达体贴'})
                return json.dumps({'keep':False,'reason':'实际读过但没有新的长期认识'})
            async def close(inner):pass
        self.runtime.engine_factory=lambda **kwargs:Engine()
        request=self.runtime.growth.request('tieba')
        await self.runtime.growth.run_once()
        row=next(r for r in self.runtime.growth.runs() if r['id']==request['id'])
        self.assertEqual(row['state'],'done',row['error']);self.assertEqual(row['actual_readings'],1)
        self.assertEqual(len(calls),2);self.assertIn(BODY[:20],calls[-1]['sources'][0]['text'])
        self.assertEqual(self.runtime.growth.memories(),[])

    async def test_admin_history_paginates_and_source_filter_is_applied_before_limit(self):
        old=self.insight('bilibili');g=self.runtime.growth
        raw=dict(self.store.db.execute('SELECT * FROM memories WHERE id=?',(old['id'],)).fetchone())
        keys=[k for k in raw if k!='id']
        with self.store.db:
            for index in range(250):
                identity=self.store.db.execute('INSERT INTO memories('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',
                    [raw[k] for k in keys]).lastrowid
                self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
                    (identity,old['journal_id'],'knowledge','测试分页','','',time.time()))
                self.store.db.execute('INSERT INTO growth_memory_policy VALUES(?,?,1,0,?)',(identity,'tieba',time.time()))
        self.assertEqual(g.memory_page(source='bilibili')['items'][0]['id'],old['id'])
        first=g.memory_page();second=g.memory_page(first['next'])
        self.assertEqual(len(first['items']),80);self.assertEqual(len(second['items']),80)
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})


class SceneTests(unittest.TestCase):
    @staticmethod
    def frame(color,stamp):
        out=io.BytesIO();Image.new('RGB',(320,180),color).save(out,format='JPEG');return {'jpeg':out.getvalue(),'time':stamp}

    def test_static_frames_are_not_padded_to_four_or_sixteen(self):
        selected,meta=select_frames([self.frame('red',i*5) for i in range(20)])
        self.assertEqual(len(selected),1);self.assertEqual(meta['candidates'],20)

    def test_scene_changes_and_numbered_storyboard_are_actual(self):
        rows=[self.frame(color,index*4) for index,color in enumerate(['red','red','blue','blue','green','green'])]
        selected,meta=select_frames(rows);self.assertGreaterEqual(len(selected),3);self.assertGreaterEqual(meta['cuts_detected'],2)
        boards=storyboards(selected);self.assertEqual(sum(len(board['cells']) for board in boards),len(selected))
        self.assertTrue(boards[0]['jpeg'].startswith(b'\xff\xd8'));self.assertEqual(boards[0]['cells'][0]['number'],1)

    def test_flash_rejection_does_not_delete_a_sparse_real_scene(self):
        flash=[self.frame('red',0),self.frame('white',.1),self.frame('red',.2)]
        selected,meta=select_frames(flash);self.assertEqual(len(selected),1);self.assertEqual(meta['flash_frames_rejected'],1)
        sparse=[self.frame('red',0),self.frame('white',4),self.frame('red',8)]
        selected,_=select_frames(sparse);self.assertEqual(len(selected),3)

    def test_gradual_motion_integrates_and_upper_bound_keeps_late_material(self):
        rows=[self.frame((i*3,i*3,i*3),i*2) for i in range(80)]
        selected,_=select_frames(rows,limit=8);self.assertGreater(len(selected),1);self.assertLessEqual(len(selected),8)
        self.assertGreater(selected[-1]['time'],100)


if __name__=='__main__':unittest.main()

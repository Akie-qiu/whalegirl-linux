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
            'intent':'用短句接梗而不抢话','examples':['啊？这也能绕回来呀'],'interest_delta':1})
        return growth.memories(include_inactive=True)[0]

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
        row=self.insight();growth=self.runtime.growth
        shared=growth.prompt({'account':'account','chat':'group@chatroom'},[{'content':'这也太离谱了吧'}])
        self.assertIn('啊？这也能绕回来呀',shared);self.assertNotIn('SOURCE_PRIVATE_CANARY',shared)
        self.assertNotIn('bilibili.com',shared);self.assertNotIn(BODY,shared)
        result=self.runtime.memory_book.model_ops('account',['group@chatroom'],[{'op':'get','id':row['id']}],[],True,chat='group@chatroom')
        self.assertTrue(result[0]['ok']);self.assertNotIn('public_evidence',result[0]['result'])
        self.assertNotIn('source_key',result[0]['result'])
        found=self.runtime.memory_book.model_ops('account',['group@chatroom'],[{'op':'search','query':'惊讶'}],[],True,chat='group@chatroom')
        self.assertNotIn('journal_id',json.dumps(found,ensure_ascii=False))
        self.assertNotIn('SOURCE_PRIVATE_CANARY',json.dumps(found,ensure_ascii=False))

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

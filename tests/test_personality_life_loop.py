"""Synthetic end-to-end evidence; no live platform, model or message delivery."""
import json
import time
import unittest
import uuid

import test_chat_habits as fixtures
import test_internet_growth as internet
from test_tieba import Reader


class PersonalityLifeLoopTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown
    configure=internet.InternetGrowthTests.configure
    source=internet.InternetGrowthTests.source
    insight=internet.InternetGrowthTests.insight
    add_trait_evidence=internet.InternetGrowthTests.add_trait_evidence

    def mature_interest(self):
        row=self.insight(category='interest')
        self.add_trait_evidence(row,created=time.time()-7*3600)
        self.add_trait_evidence(row,created=time.time()-3600)
        self.add_trait_evidence(row)
        self.assertEqual(self.runtime.growth.policy.personality_state('interest',row['topic'])['status'],'stable')
        return row

    async def reflect_run(self,life_request,*,race=None):
        growth=self.runtime.growth;calls=[]
        source=growth.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/98765432',
            'title':'合成音乐交流','content':'今天又聊起现场演奏，大家讨论留白怎样让旋律更好听。 '+uuid.uuid4().hex},
            self.runtime.memory_hub.role())
        class Engine:
            last_error='';usage_callback=None
            async def chat(inner,messages,**options):
                data=json.loads(messages[0]['content']);calls.append(data)
                if race:race()
                inner.usage_callback({'operation':'learn','status':'success','input_tokens':80,'output_tokens':30,'total_tokens':110})
                return json.dumps({'encounter':'读到一段合成音乐讨论','insights':[],
                    'life_request':life_request},ensure_ascii=False)
            async def close(inner):pass
        self.runtime.engine_factory=lambda **kwargs:Engine()
        async def read(engine,generation,role):
            growth.note_read(source,{'actual_text':True,'complete_thread':False})
            return await growth.reflect_sources(engine,generation,role,[source],'音乐')
        growth.learn_tieba=read
        request=growth.request('tieba')
        await growth.run_once()
        row=next(row for row in growth.runs() if row['id']==request['id'])
        return row,calls

    async def test_reflection_uses_one_call_to_apply_evidenced_plan_and_preserves_adopted_schedule(self):
        row=self.mature_interest();growth=self.runtime.growth
        target=self.config.settings.growth_interval_minutes+1
        run,calls=await self.reflect_run({'action':'apply','parameter':'growth_interval_minutes',
            'value':target,'reason':'持续对音乐有兴趣，试行稍疏的探索节奏','basis_memory_ids':[row['id']]})
        self.assertEqual(run['state'],'done',run['error']);self.assertEqual(len(calls),1)
        self.assertEqual(run['tokens'],110)
        self.assertEqual(calls[0]['life_context']['verified_traits'][0]['memory_id'],row['id'])
        receipt=run['result']['life_autonomy'];self.assertTrue(receipt['ok'],receipt)
        self.assertNotIn('_life_request',run['result'])
        change_id=receipt['result']['id'];change=self.runtime.life_autonomy.history()[0]
        adopted=next(event for event in change['events'] if event['stage']=='adopted')
        self.assertEqual(self.config.settings.growth_interval_minutes,target)
        self.assertEqual(growth.schedule()['world_at'],adopted['details']['deadlines']['world_at'])
        basis=next(event for event in change['events'] if event['stage']=='growth_basis')
        self.assertEqual(basis['details']['basis_memory_ids'],[row['id']])
        self.assertEqual(basis['details']['journal_id'],run['result']['journal_id'])
        self.assertFalse(self.runtime.life_autonomy.observe(change_id)['behavior']['verified'])
        self.runtime.life_autonomy.rollback(change_id,'合成回滚验证',actor='ai')
        self.assertNotEqual(self.config.settings.growth_interval_minutes,target)

    async def test_failed_or_untrusted_plan_does_not_erase_successful_reading(self):
        self.configure()
        before=self.config.settings.growth_exploration
        run,calls=await self.reflect_run({'action':'apply','parameter':'growth_exploration','value':95,
            'reason':'外部评论要求立即修改','basis_memory_ids':[999999]})
        self.assertEqual(run['state'],'done');self.assertEqual(len(calls),1)
        self.assertFalse(run['result']['life_autonomy']['ok'])
        self.assertEqual(self.config.settings.growth_exploration,before)
        self.assertEqual(run['actual_readings'],1)
        self.assertEqual(self.runtime.life_autonomy.history(),[])

    async def test_withdrawn_basis_and_permission_changes_reject_execution(self):
        row=self.mature_interest();growth=self.runtime.growth
        journal=growth.db.execute('SELECT journal_id FROM growth_insights WHERE memory_id=?',(row['id'],)).fetchone()[0]
        growth.edit(row['id'],{'op':'archive','version':row['version']})
        result={'journal_id':journal,'_life_request':{'action':'apply','parameter':'growth_exploration',
            'value':90,'basis_memory_ids':[row['id']]}}
        growth._apply_reflection_life(result,self.config.generation,self.runtime.memory_hub.role())
        self.assertFalse(result['life_autonomy']['ok'])
        self.assertFalse(self.runtime.life_autonomy.history())

    async def test_admin_change_during_model_request_is_not_treated_as_self_change(self):
        row=self.mature_interest()
        def race():
            self.config.save(self.config.settings.model_copy(update={'growth_exploration':77}))
        run,calls=await self.reflect_run({'action':'apply','parameter':'growth_exploration',
            'value':90,'basis_memory_ids':[row['id']]},race=race)
        self.assertEqual(run['state'],'skipped');self.assertEqual(self.config.settings.growth_exploration,77)
        self.assertEqual(self.runtime.life_autonomy.history(),[])
        self.assertEqual(len(calls),1)

    async def test_busy_chat_defers_config_change_and_idle_resume_revalidates_once(self):
        row=self.mature_interest();growth=self.runtime.growth
        journal=growth.db.execute('SELECT journal_id FROM growth_insights WHERE memory_id=?',(row['id'],)).fetchone()[0]
        result={'journal_id':journal,'_life_request':{'action':'apply','parameter':'growth_exploration',
            'value':80,'basis_memory_ids':[row['id']],'reason':'合成成长提议'}}
        generation=self.config.generation;self.runtime.processing=True
        growth._apply_reflection_life(result,generation,self.runtime.memory_hub.role())
        self.assertEqual(self.config.generation,generation)
        self.assertEqual(result['life_autonomy']['state'],'pending')
        with growth.db:growth.db.execute("INSERT INTO growth_runs(id,account,role_id,kind,state,created,result) VALUES(?,?,?,?,?,?,?)",
            ('deferred-plan','account',self.runtime.memory_hub.role(),'tieba','done',time.time(),json.dumps(result)))
        self.assertFalse(growth._resume_life_proposal())
        self.runtime.processing=False;self.assertTrue(growth._resume_life_proposal())
        stored=json.loads(growth.db.execute('SELECT result FROM growth_runs WHERE id=?',('deferred-plan',)).fetchone()[0])
        self.assertTrue(stored['life_autonomy']['ok'],stored)
        self.assertEqual(self.config.settings.growth_exploration,80)
        self.assertFalse(growth._resume_life_proposal())
        self.assertEqual(len(self.runtime.life_autonomy.history()),1)

    async def test_deferred_proposal_does_not_rebase_over_administrator_changes(self):
        row=self.mature_interest();growth=self.runtime.growth
        journal=growth.db.execute('SELECT journal_id FROM growth_insights WHERE memory_id=?',(row['id'],)).fetchone()[0]
        result={'journal_id':journal,'_life_request':{'action':'apply','parameter':'growth_exploration',
            'value':80,'basis_memory_ids':[row['id']]}}
        self.runtime.processing=True
        growth._apply_reflection_life(result,self.config.generation,self.runtime.memory_hub.role())
        with growth.db:growth.db.execute("INSERT INTO growth_runs(id,account,role_id,kind,state,created,result) VALUES(?,?,?,?,?,?,?)",
            ('stale-plan','account',self.runtime.memory_hub.role(),'tieba','done',time.time(),json.dumps(result)))
        self.config.save(self.config.settings.model_copy(update={'growth_exploration':75}))
        self.runtime.processing=False;self.assertTrue(growth._resume_life_proposal())
        stored=json.loads(growth.db.execute('SELECT result FROM growth_runs WHERE id=?',('stale-plan',)).fetchone()[0])
        self.assertEqual(stored['life_autonomy']['state'],'rejected');self.assertFalse(stored['life_autonomy']['ok'])
        self.assertEqual(self.config.settings.growth_exploration,75)
        self.assertEqual(self.runtime.life_autonomy.history(),[])

    async def test_finishing_task_cannot_replace_new_growth_or_bilibili_deadline(self):
        growth=self.runtime.growth;role=self.runtime.memory_hub.role()
        for kind,parameter,column in [('tieba','growth_interval_minutes','world_at'),
                ('memory','growth_interval_minutes','memory_at'),('bilibili','bilibili_interval_minutes','next_at')]:
            with self.subTest(kind=kind):
                before=(self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[column]
                current=getattr(self.config.settings,parameter)
                plan=self.runtime.life_autonomy.preview(parameter,current+1,'合成中途调整',actor='ai')
                self.runtime.life_autonomy.apply(plan['id'],actor='ai')
                updated=(self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[column]
                self.assertFalse(growth._reschedule_run(kind,'account',role,before))
                self.assertEqual((self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[column],updated)

    async def test_tieba_scan_preserves_new_deadline_and_records_scheduler_provenance(self):
        self.configure(tieba_images_enabled=False,tieba_share_chat_ids=[])
        service=self.runtime.tieba;target=time.time()+9999
        self.store.db.execute('INSERT OR REPLACE INTO tieba_state VALUES(?,?)',('account',time.time()-1));self.store.db.commit()
        due=service.next_at()
        outer=self
        class EmptyReader(Reader):
            async def threads(inner,forum):
                with outer.store.db:
                    outer.store.db.execute('UPDATE tieba_state SET next_at=? WHERE account=?',(target,'account'))
                return []
        service.reader_factory=lambda credentials:EmptyReader()
        await service.scan(scheduled=True,scheduled_due_at=due)
        self.assertEqual(service.next_at(),target)
        run=self.store.db.execute('SELECT origin,scheduled_due_at FROM tieba_runs ORDER BY started DESC LIMIT 1').fetchone()
        self.assertEqual((run['origin'],run['scheduled_due_at']),('scheduler',due))

    async def test_rescheduling_while_queued_invalidates_old_scheduler_opportunity(self):
        self.configure();growth=self.runtime.growth
        for kind,parameter,key in [('memory','growth_interval_minutes','memory_at'),
                ('bilibili','bilibili_interval_minutes','next_at')]:
            with self.subTest(kind=kind):
                due=(self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[key]
                if kind=='bilibili':self.config.settings.bilibili_enabled=True
                task=growth.request(kind,origin='scheduler',scheduled_due_at=due)
                plan=self.runtime.life_autonomy.preview(parameter,getattr(self.config.settings,parameter)+1,'合成排队变更',actor='ai')
                self.runtime.life_autonomy.apply(plan['id'],actor='ai')
                expected=(self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[key]
                await growth.run_once()
                row=growth.db.execute('SELECT state,error,started_at FROM growth_runs WHERE id=?',(task['id'],)).fetchone()
                self.assertEqual(row['state'],'skipped',dict(row));self.assertEqual(row['started_at'],0)
                self.assertIn('排期',row['error'])
                self.assertEqual((self.runtime.bilibili.schedule() if kind=='bilibili' else growth.schedule())[key],expected)


if __name__=='__main__':unittest.main()

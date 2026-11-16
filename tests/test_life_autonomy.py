"""Synthetic regressions for auditable life-parameter transactions."""
import json
import time
import unittest
from unittest.mock import patch

import test_chat_habits as fixtures


class LifeAutonomyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown

    async def test_preview_is_read_only_and_ai_cannot_apply_admin_preview(self):
        service=self.runtime.life_autonomy
        before=self.config.settings.growth_exploration
        preview=service.preview('growth_exploration',before+10,'想多试些新题材',actor='admin')
        self.assertEqual(self.config.settings.growth_exploration,before)
        with self.assertRaisesRegex(ValueError,'只能应用自己发起'):
            service.execute_request({'action':'apply','change_id':preview['id']})
        applied=service.apply(preview['id'],actor='admin')
        self.assertEqual(applied['state'],'applied')
        self.assertEqual(self.config.settings.growth_exploration,before+10)
        self.assertFalse(service.observe(preview['id'])['behavior']['verified'])

    async def test_real_selection_is_required_before_behavior_is_observed(self):
        service=self.runtime.life_autonomy
        before=self.config.settings.growth_exploration
        preview=service.preview('growth_exploration',before+1,'合成验证开放度采用',actor='ai')
        service.apply(preview['id'],actor='ai')
        run_id='synthetic-growth-selection'
        with self.store.db:
            self.store.db.execute("INSERT INTO growth_runs(id,account,role_id,kind,state,created,started_at,finished) VALUES(?,?,?,?,?,?,?,?)",
                (run_id,'account',self.runtime.memory_hub.role(),'bilibili','done',time.time(),time.time(),time.time()))
        self.runtime.growth.event('candidate_selected','合成选择证据',
            {'bvid':'BV1234567890','exploration_openness':before+1},state='planning',run_id=run_id)
        self.assertEqual(service.record_behavior('growth_exploration',before+1,
            {'origin':'selection','source':'bilibili','bvid':'BV1234567890','run_id':run_id}),1)
        observed=service.observe(preview['id'])
        self.assertEqual(observed['state'],'observed')
        self.assertEqual(observed['behavior']['count'],1)
        self.assertEqual(observed['behavior']['evidence'][0]['source'],'bilibili')

    async def test_interval_requires_elapsed_scheduler_run_not_manual_activity(self):
        service=self.runtime.life_autonomy
        before=self.config.settings.bilibili_interval_minutes
        target=min(1440,before+1)
        preview=service.preview('bilibili_interval_minutes',target,'合成验证定时采用',actor='ai')
        service.apply(preview['id'],actor='ai')
        self.assertEqual(service.record_behavior('bilibili_interval_minutes',target,
            {'origin':'manual','kind':'bilibili','run_id':'manual'}),0)
        self.assertFalse(service.observe(preview['id'])['behavior']['verified'])
        with self.store.db:
            self.store.db.execute('UPDATE life_parameter_changes SET applied_at=? WHERE id=?',(time.time()-target*60-1,preview['id']))
        run_id='synthetic-scheduled-growth'
        due=time.time()-60
        # A real run whose due time predates the setting transaction cannot
        # prove that the new interval was adopted.
        with self.store.db:
            self.store.db.execute('UPDATE life_parameter_changes SET applied_at=? WHERE id=?',(due-target*60+1,preview['id']))
        result={'request':{'origin':'scheduler','scheduled_due_at':due},'readings':1}
        with self.store.db:
            self.store.db.execute("INSERT INTO growth_runs(id,account,role_id,kind,state,created,started_at,finished,result) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id,'account',self.runtime.memory_hub.role(),'bilibili','done',due-2,due+1,due+10,json.dumps(result)))
        # The parameter's original deadline is later than this due time.
        self.assertEqual(service.record_behavior('bilibili_interval_minutes',target,
            {'origin':'scheduler','kind':'bilibili','run_id':run_id,'scheduled_due_at':due}),0)
        with self.store.db:
            self.store.db.execute('UPDATE life_parameter_changes SET applied_at=? WHERE id=?',(due-target*60,preview['id']))
        self.assertEqual(service.record_behavior('bilibili_interval_minutes',target,
            {'origin':'scheduler','kind':'bilibili','run_id':run_id,'scheduled_due_at':due}),1)
        self.assertEqual(service.observe(preview['id'])['state'],'observed')

    async def test_rollback_cannot_be_revived_by_observe(self):
        service=self.runtime.life_autonomy
        before=self.config.settings.growth_exploration
        preview=service.preview('growth_exploration',before+1,'rollback lifecycle',actor='admin')
        service.apply(preview['id'])
        service.rollback(preview['id'])
        self.config.save(self.config.settings.model_copy(update={'growth_exploration':before+1}))
        self.assertEqual(service.observe(preview['id'])['state'],'rolled_back')

    async def test_stale_preview_and_rollback_conflicts_do_not_overwrite_new_values(self):
        service=self.runtime.life_autonomy
        start=self.config.settings.growth_exploration
        first=service.preview('growth_exploration',start+1,'first',actor='admin')
        second=service.preview('growth_exploration',start+2,'second',actor='admin')
        service.apply(first['id'])
        with self.assertRaisesRegex(ValueError,'安全预览失效'):
            service.apply(second['id'])
        current=self.config.settings.model_copy(update={'growth_exploration':start+3})
        self.config.save(current)
        with self.assertRaisesRegex(ValueError,'不执行回滚'):
            service.rollback(first['id'],'conflict')
        self.assertEqual(self.config.settings.growth_exploration,start+3)

    async def test_disabled_service_rejects_all_ai_routes_but_keeps_admin_management(self):
        service=self.runtime.life_autonomy
        start=self.config.settings.growth_exploration
        applied=service.propose_and_apply('growth_exploration',start+1,'合成试行',actor='ai')
        pending=service.preview('growth_exploration',start+2,'合成待处理',actor='ai')
        self.config.save(self.config.settings.model_copy(update={'life_autonomy_enabled':False}))
        requests=[{'action':'inspect'}, {'action':'observe','change_id':applied['id']},
            {'action':'preview','parameter':'growth_exploration','value':start+2},
            {'action':'apply','change_id':pending['id']},
            {'action':'rollback','change_id':applied['id']}]
        for request in requests:
            with self.subTest(action=request['action']),self.assertRaisesRegex(ValueError,'生活自治已关闭'):
                service.execute_request(request)
        for operation,args in ((service.preview,('growth_exploration',start+2)),
                (service.apply,(pending['id'],)),(service.rollback,(applied['id'],))):
            with self.assertRaisesRegex(ValueError,'生活自治已关闭'):
                operation(*args,actor='ai')
        self.assertEqual(self.config.settings.growth_exploration,start+1)
        self.assertFalse(service.catalog()['enabled'])
        admin=service.propose_and_apply('growth_exploration',start+3,'管理员仍可管理',actor='admin')
        self.assertEqual(service.rollback(admin['id'])['value'],start+1)

    async def test_rollback_restores_exact_previous_deadlines(self):
        service=self.runtime.life_autonomy
        account,role=service._scope();now=time.time()
        self.runtime.growth.schedule();self.runtime.bilibili.schedule()
        with self.store.db:
            self.store.db.execute('UPDATE growth_state SET memory_at=?,world_at=? WHERE account=? AND role_id=?',
                (now+2700,now+4500,account,role))
            self.store.db.execute('UPDATE bilibili_schedule SET next_at=?,curiosity=? WHERE account=? AND role=?',
                (now+3500,'保留现有好奇心',account,role))
            self.store.db.execute('INSERT OR REPLACE INTO tieba_state(account,next_at) VALUES(?,?)',(account,now+1700))
        for parameter in ('growth_interval_minutes','bilibili_interval_minutes','tieba_interval_minutes'):
            with self.subTest(parameter=parameter):
                before=service._scheduler_state(parameter)
                value=getattr(self.config.settings,parameter)
                change=service.propose_and_apply(parameter,value+1,'合成精确回滚',actor='admin')
                self.assertNotEqual(service._scheduler_state(parameter),before)
                result=service.rollback(change['id'],'放弃本次试行')
                self.assertEqual(result['scheduler']['restore_kind'],'saved_previous_deadline')
                self.assertEqual(service._scheduler_state(parameter),before)
                self.assertEqual(getattr(self.config.settings,parameter),value)
        self.assertEqual(self.runtime.bilibili.schedule()['curiosity'],'保留现有好奇心')

    async def test_fresh_scheduler_keeps_real_initial_deadline_on_rollback(self):
        service=self.runtime.life_autonomy
        self.assertEqual(service._scheduler_state('bilibili_interval_minutes'),{'next_at':0})
        value=self.config.settings.bilibili_interval_minutes
        change=service.propose_and_apply('bilibili_interval_minutes',value+1,'首次调度的合成验证',actor='admin')
        before=service.history(1)[0]['preview']['scheduler_before_apply']
        self.assertGreater(before['next_at'],time.time()+200)
        service.rollback(change['id'])
        self.assertEqual(service._scheduler_state('bilibili_interval_minutes'),before)

    async def test_partial_config_save_failure_restores_disk_and_runtime(self):
        service=self.runtime.life_autonomy;parameter='bilibili_interval_minutes'
        original=self.config.settings.bilibili_interval_minutes
        service._scheduler_baseline(parameter);deadline=service._scheduler_state(parameter)
        change=service.preview(parameter,original+1,'合成写盘后失败',actor='admin')
        save=self.config.save;attempts=[]
        def fail_after_first_write(settings,*args,**kwargs):
            attempts.append(settings.bilibili_interval_minutes)
            save(settings,*args,**kwargs)
            if len(attempts)==1:raise OSError('synthetic failure after persistence')
        with patch.object(self.config,'save',side_effect=fail_after_first_write):
            with self.assertRaisesRegex(ValueError,'已恢复原设置和排期'):
                service.apply(change['id'])
        self.assertEqual(attempts,[original+1,original])
        self.assertEqual(self.config.settings.bilibili_interval_minutes,original)
        self.assertEqual(json.loads(self.config.path.read_text('utf-8'))[parameter],original)
        self.assertEqual(service._scheduler_state(parameter),deadline)
        self.assertEqual(service.history(1)[0]['state'],'failed')

    async def test_scheduler_failure_compensates_already_changed_setting_and_deadline(self):
        service=self.runtime.life_autonomy;parameter='growth_interval_minutes'
        original=getattr(self.config.settings,parameter)
        service._scheduler_baseline(parameter);before=service._scheduler_state(parameter)
        change=service.preview(parameter,original+1,'合成调度失败',actor='admin')
        adopt=service._adopt_scheduler
        def fail_after_scheduling(*args,**kwargs):
            adopt(*args,**kwargs)
            raise OSError('synthetic scheduler readback failure')
        with patch.object(service,'_adopt_scheduler',side_effect=fail_after_scheduling):
            with self.assertRaisesRegex(ValueError,'已恢复原设置和排期'):
                service.apply(change['id'])
        self.assertEqual(getattr(self.config.settings,parameter),original)
        self.assertEqual(json.loads(self.config.path.read_text('utf-8'))[parameter],original)
        self.assertEqual(service._scheduler_state(parameter),before)
        history=service.history(1)[0]
        self.assertEqual(history['state'],'failed')
        self.assertTrue(history['events'][-1]['details']['restored'])
        self.assertEqual(service._head(parameter)['revision'],0)

    async def test_audit_commit_failure_also_compensates_and_does_not_advance_head(self):
        service=self.runtime.life_autonomy;parameter='growth_exploration'
        original=getattr(self.config.settings,parameter)
        change=service.preview(parameter,original+1,'合成事务提交失败',actor='admin')
        write_head=service._write_head
        def fail_after_head(*args,**kwargs):
            write_head(*args,**kwargs)
            raise OSError('synthetic audit commit failure')
        with patch.object(service,'_write_head',side_effect=fail_after_head):
            with self.assertRaisesRegex(ValueError,'已恢复原设置和排期'):
                service.apply(change['id'])
        self.assertEqual(getattr(self.config.settings,parameter),original)
        self.assertEqual(service._head(parameter)['revision'],0)
        self.assertEqual(service.history(1)[0]['state'],'failed')

    async def test_success_event_failure_cannot_leave_an_unreported_applied_setting(self):
        service=self.runtime.life_autonomy;parameter='growth_exploration'
        original=getattr(self.config.settings,parameter)
        change=service.preview(parameter,original+1,'合成事件落盘失败',actor='admin')
        insert_event=service._insert_event
        def fail_on_applied(identity,stage,details):
            if stage=='applied':raise OSError('synthetic event persistence failure')
            return insert_event(identity,stage,details)
        with patch.object(service,'_insert_event',side_effect=fail_on_applied):
            with self.assertRaisesRegex(ValueError,'已恢复原设置和排期'):
                service.apply(change['id'])
        self.assertEqual(getattr(self.config.settings,parameter),original)
        self.assertEqual(service._head(parameter)['revision'],0)
        history=service.history(1)[0]
        self.assertEqual(history['state'],'failed')
        self.assertNotIn('applied',[event['stage'] for event in history['events']])

    async def test_rollback_failure_restores_setting_and_deadline_before_rollback(self):
        service=self.runtime.life_autonomy;parameter='bilibili_interval_minutes'
        original=getattr(self.config.settings,parameter)
        change=service.propose_and_apply(parameter,original+1,'合成回滚失败',actor='admin')
        before=service._scheduler_state(parameter);restore=service._restore_scheduler;calls=[]
        def fail_first_restore(*args,**kwargs):
            calls.append(True);result=restore(*args,**kwargs)
            if len(calls)==1:raise OSError('synthetic partial rollback failure')
            return result
        with patch.object(service,'_restore_scheduler',side_effect=fail_first_restore):
            with self.assertRaisesRegex(ValueError,'已恢复回滚前设置和排期'):
                service.rollback(change['id'])
        self.assertEqual(getattr(self.config.settings,parameter),original+1)
        self.assertEqual(service._scheduler_state(parameter),before)
        self.assertEqual(service.history(1)[0]['state'],'adopted')
        self.assertEqual(service.history(1)[0]['events'][-1]['stage'],'rollback_failed')
        self.assertEqual(service.rollback(change['id'])['value'],original)

    async def test_preview_detects_values_changed_away_and_back(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        stale=service.preview('growth_exploration',start+1,'旧预览',actor='admin')
        away=service.propose_and_apply('growth_exploration',start+2,'新决定',actor='admin')
        service.rollback(away['id'])
        self.assertEqual(self.config.settings.growth_exploration,start)
        with self.assertRaisesRegex(ValueError,'安全预览失效'):
            service.apply(stale['id'])
        # Direct console saves also invalidate a preview, even if their final
        # value happens to be identical to its original value.
        direct=service.preview('growth_exploration',start+1,'管理页旧预览',actor='admin')
        self.config.save(self.config.settings.model_copy(update={'growth_exploration':start+2}))
        self.config.save(self.config.settings.model_copy(update={'growth_exploration':start}))
        with self.assertRaisesRegex(ValueError,'安全预览失效'):
            service.apply(direct['id'])

    async def test_old_transaction_cannot_rollback_newer_same_value_transaction(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        first=service.propose_and_apply('growth_exploration',start+1,'第一个决定',actor='admin')
        second=service.propose_and_apply('growth_exploration',start+1,'之后维持同值的决定',actor='admin')
        with self.assertRaisesRegex(ValueError,'不执行回滚'):
            service.rollback(first['id'])
        self.assertEqual(self.config.settings.growth_exploration,start+1)
        self.assertFalse(service.observe(first['id'])['active'])
        self.assertTrue(service.observe(second['id'])['active'])

    async def test_only_current_transaction_receives_real_selection_evidence(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        first=service.propose_and_apply('growth_exploration',start+1,'旧决定',actor='ai')
        second=service.propose_and_apply('growth_exploration',start+1,'新决定',actor='ai')
        run_id='current-transaction-selection';now=time.time()
        with self.store.db:
            self.store.db.execute("INSERT INTO growth_runs(id,account,role_id,kind,state,created,started_at,finished) VALUES(?,?,?,?,?,?,?,?)",
                (run_id,'account',self.runtime.memory_hub.role(),'bilibili','done',now,now,now))
        self.runtime.growth.event('candidate_selected','当前决定的选择证据',
            {'bvid':'BV1234567890','exploration_openness':start+1},state='planning',run_id=run_id)
        evidence={'origin':'selection','source':'bilibili','bvid':'BV1234567890','run_id':run_id}
        self.assertEqual(service.record_behavior('growth_exploration',start+1,evidence),1)
        self.assertEqual(service.record_behavior('growth_exploration',start+1,evidence),0)
        self.assertEqual(service.observe(first['id'])['state'],'applied')
        self.assertFalse(service.observe(first['id'])['active'])
        self.assertEqual(service.observe(second['id'])['state'],'observed')
        self.assertEqual(service.observe(second['id'])['behavior']['count'],1)
        service.rollback(second['id'])
        self.assertEqual(service.record_behavior('growth_exploration',start+1,evidence),0)
        self.assertFalse(service.observe(second['id'])['active'])

    async def test_native_inspect_cannot_override_action_in_tool_arguments(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        result={'tool_calls':[{'function':{'name':'life_inspect','arguments':json.dumps({
            'action':'apply','parameter':'growth_exploration','value':start+1,'reason':'覆盖操作尝试'})}}]}
        decision=service.native_decision(result)
        self.assertEqual(decision['life_request']['action'],'inspect')
        service.execute_request(decision['life_request'])
        self.assertEqual(self.config.settings.growth_exploration,start)

    async def test_inspect_exposes_compact_recent_transaction_ids_without_credentials(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        change=service.propose_and_apply('growth_exploration',start+1,'查看后续观察入口',actor='ai')
        inspected=service.inspect()
        self.assertEqual(inspected['transactions'][0]['id'],change['id'])
        self.assertEqual(inspected['transactions'][0]['initiator'],'ai')
        self.assertNotIn('events',inspected['transactions'][0])
        self.assertNotIn('test-key',json.dumps(inspected))

    async def test_tieba_interval_requires_durable_matching_scheduler_origin(self):
        service=self.runtime.life_autonomy;parameter='tieba_interval_minutes'
        target=getattr(self.config.settings,parameter)+1
        change=service.propose_and_apply(parameter,target,'贴吧定时行为验证',actor='admin')
        due=time.time()-30;run_id='tieba-persisted-origin-check'
        with self.store.db:
            self.store.db.execute('UPDATE life_parameter_changes SET applied_at=? WHERE id=?',
                (due-target*60,change['id']))
            self.store.db.execute('''INSERT INTO tieba_runs
                (id,account,started,finished,state,read_count,origin,scheduled_due_at) VALUES(?,?,?,?,?,?,?,?)''',
                (run_id,'account',due+1,due+2,'done',1,'manual',due))
        evidence={'origin':'scheduler','kind':'tieba','run_id':run_id,'scheduled_due_at':due}
        # Merely labelling a manual read as scheduled at the call site is not
        # evidence that the runtime actually adopted the configured interval.
        self.assertEqual(service.record_behavior(parameter,target,evidence),0)
        with self.store.db:
            self.store.db.execute("UPDATE tieba_runs SET origin='scheduler',scheduled_due_at=? WHERE id=?",(due+1,run_id))
        self.assertEqual(service.record_behavior(parameter,target,evidence),0)
        with self.store.db:
            self.store.db.execute('UPDATE tieba_runs SET scheduled_due_at=? WHERE id=?',(due,run_id))
        self.assertEqual(service.record_behavior(parameter,target,evidence),1)
        self.assertEqual(service.observe(change['id'])['state'],'observed')

    async def test_legacy_applied_change_remains_rollbackable_with_explicit_schedule_fallback(self):
        service=self.runtime.life_autonomy;parameter='bilibili_interval_minutes'
        previous=getattr(self.config.settings,parameter);now=time.time();identity='legacy-applied-change'
        self.config.save(self.config.settings.model_copy(update={parameter:previous+1}))
        # Before applied_at and exact scheduler snapshots were introduced,
        # adopted rows only had created/updated timestamps.
        with self.store.db:
            self.store.db.execute('''INSERT INTO life_parameter_changes
                (id,account,role_id,parameter,previous_value,target_value,reason,state,created,updated)
                VALUES(?,?,?,?,?,?,?,'adopted',?,?)''',
                (identity,*service._scope(),parameter,previous,previous+1,'旧版已应用记录',now,now))
        result=service.rollback(identity)
        self.assertEqual(result['value'],previous)
        self.assertEqual(result['scheduler']['restore_kind'],'legacy_recomputed_deadline')
        self.assertEqual(service.observe(identity)['state'],'rolled_back')

    async def test_parameter_heads_survive_service_reinitialization(self):
        service=self.runtime.life_autonomy;start=self.config.settings.growth_exploration
        first=service.propose_and_apply('growth_exploration',start+1,'第一个决定',actor='admin')
        second=service.propose_and_apply('growth_exploration',start+1,'新决定',actor='admin')
        reloaded=type(service)(self.runtime)
        self.assertEqual(reloaded._head('growth_exploration')['change_id'],second['id'])
        with self.assertRaisesRegex(ValueError,'不执行回滚'):
            reloaded.rollback(first['id'])


if __name__=='__main__':
    unittest.main()

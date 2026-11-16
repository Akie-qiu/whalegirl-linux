"""Private initiative after a normal reply; all models and sends are synthetic."""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

import test_proactive as fixtures


class PrivateInitiativeTests(unittest.IsolatedAsyncioTestCase):
    message = fixtures.ProactiveTests.message
    configure = fixtures.ProactiveTests.configure

    async def asyncSetUp(self):
        await fixtures.ProactiveTests.asyncSetUp(self)
        self.configure(autonomy_enabled=True, proactive_private_enabled=True,
                       proactive_chat_ids=['friend'], proactive_cooldown_minutes=60,
                       proactive_idle_minutes=0, proactive_decision_minutes=5, proactive_level='active')
        self.event = {**self.event, 'chat':'friend', 'key':'account/friend/1',
                      'sender_id':'friend', 'timestamp':time.time()-7200}
        self.store.observe([self.event])
        self.store.baseline('account','friend')
        self.runtime.autonomy.sync_mode()
        # This is the same accept path that records an ordinary chat decision.
        self.runtime.autonomy.accept(self.event,[self.event],{'should_reply':False})
        self.reply = {'should_reply':False,'reason':'合成测试：暂时没有想说的','actions':[]}

    async def asyncTearDown(self):
        await self.runtime.close()

    def opportunity(self,now=None):
        return self.service.eligible('friend',time.time() if now is None else now)

    async def test_passive_turn_consuming_anchor_no_longer_permanently_blocks_private_initiative(self):
        self.assertFalse(self.runtime.autonomy.opportunity('friend',anchor=self.event['key']))
        self.assertTrue(self.opportunity())
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.calls,1)
        self.assertEqual(self.service.snapshot()['runs'][0]['state'],'ignored')
        self.assertEqual(self.gateway.sent,[])

    async def test_recent_normal_reply_still_gets_the_configured_quiet_interval(self):
        self.store.insert({**self.event,'key':'passive-answer'},'sent')
        sent = self.store.latest_sent('account','friend')
        self.assertFalse(self.opportunity(sent+3599))
        self.assertTrue(self.opportunity(sent+3601))

    async def test_silence_is_reconsidered_after_cooldown_instead_of_every_timer_tick(self):
        await self.service.run_once()
        state = self.service.chat_state('friend')
        self.assertFalse(self.opportunity(state['last_decision']+30))
        self.assertFalse(self.opportunity(state['last_decision']+3599))
        self.assertTrue(self.opportunity(state['last_decision']+3601))
        self.assertFalse(await self.service.run_once())
        self.assertEqual(self.calls,1)

    async def test_confirmed_fake_send_blocks_another_unanswered_opener(self):
        self.reply = {'should_reply':True,'actions':[{'type':'text','text':'那关后来过了吗'}]}
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.gateway.sent,[('friend','那关后来过了吗')])
        self.assertEqual(self.service.snapshot()['runs'][0]['state'],'sent')
        self.assertFalse(self.opportunity(time.time()+2*86400))

    async def test_preexisting_unanswered_opener_is_not_reenabled_by_idle_opportunity(self):
        self.service.touch('friend',waiting_after=self.event['timestamp']+1)
        self.assertFalse(self.opportunity(time.time()+7*86400))

    async def test_explicit_due_followup_still_has_its_existing_opportunity(self):
        self.service.touch('friend',waiting_after=self.event['timestamp']+1)
        self.runtime.autonomy.accept(self.event,[self.event],{'should_reply':False,'intent_ops':[
            {'op':'plan','topic':'合成测试：询问游戏进展','source_key':self.event['key'],
             'after_minutes':1,'expires_minutes':60}]})
        self.assertTrue(self.opportunity(time.time()+120))

    async def test_contact_permissions_private_switch_and_explicit_pause_still_win(self):
        for changes in ({'proactive_private_enabled':False}, {'proactive_chat_ids':[]},
                        {'allowed_chat_ids':[fixtures.CHAT]}, {'proactive_enabled':False}):
            before = self.config.settings
            self.configure(**changes)
            self.assertFalse(self.opportunity())
            self.config.save(before)
        self.service.pause('friend')
        self.assertFalse(self.opportunity())
        self.assertEqual(self.calls,0)

    async def test_quiet_and_person_busy_states_are_not_bypassed(self):
        self.runtime.chat_control.apply('friend','quiet',minutes=30)
        self.assertFalse(self.opportunity())
        self.runtime.chat_control.apply('friend','resume')
        self.service.execute('INSERT INTO proactive_people VALUES(?,?,?,?,?)',
            ('account','friend','friend',time.time()+7200,'合成测试：对方忙碌'))
        self.assertFalse(self.opportunity())

    async def test_global_and_per_contact_daily_send_limits_still_apply(self):
        self.configure(proactive_per_day=0)
        self.assertFalse(self.opportunity())
        self.configure(proactive_per_day=20,proactive_chat_per_day=1)
        self.service.execute('INSERT INTO proactive_runs(id,account,chat,state,created,updated) VALUES(?,?,?,?,?,?)',
            ('sent-fixture','account','friend','sent',time.time(),time.time()))
        self.assertFalse(self.opportunity())

    async def test_zero_cooldown_keeps_the_configured_model_decision_gap(self):
        self.configure(proactive_cooldown_minutes=0)
        now=time.time()
        self.service.touch('friend',last_decision=now)
        self.assertFalse(self.opportunity(now+149))
        self.assertTrue(self.opportunity(now+151))

    async def test_groups_still_need_new_evidence_and_never_receive_private_idle_opportunities(self):
        self.configure(proactive_chat_ids=[fixtures.CHAT])
        source = self.service.latest_human(fixtures.CHAT)
        self.runtime.autonomy.considered(source,source['key'])
        self.assertIsNone(self.service.private_idle_at(fixtures.CHAT,self.service.chat_state(fixtures.CHAT),source))
        self.assertFalse(self.service.eligible(fixtures.CHAT,time.time()+2*86400))

    async def test_private_status_is_read_only_and_exposes_timing_without_creating_a_task(self):
        before = self.store.db.total_changes
        state = self.service.snapshot()['chats']['friend']
        self.assertLess(state['private_idle_at'],time.time())
        self.assertFalse(state['waiting_for_reply'])
        self.assertEqual(self.store.db.total_changes,before)
        self.assertEqual(self.calls,0)

    async def test_foreground_work_finishes_before_private_send_without_another_model_call(self):
        self.reply = {'should_reply':True,'actions':[{'type':'text','text':'那关后来过了吗'}]}
        self.hook = lambda:setattr(self.runtime,'processing','另一个会话正在回复')
        original_sleep = asyncio.sleep
        async def release(_):
            self.assertFalse(self.runtime.chat_turn_lock.locked())
            self.runtime.processing = ''
            await original_sleep(0)
        with patch('whalegirl_linux.proactive.asyncio.sleep',release):
            self.assertTrue(await self.service.run_once())
        self.assertEqual(self.calls,1)
        self.assertEqual(self.gateway.sent,[('friend','那关后来过了吗')])

    async def test_new_private_message_cancels_a_reply_waiting_for_other_chat(self):
        self.reply = {'should_reply':True,'actions':[{'type':'text','text':'这句旧话题不能发'}]}
        self.hook = lambda:setattr(self.runtime,'processing','另一个会话正在回复')
        original_sleep = asyncio.sleep
        async def incoming(_):
            self.runtime.processing = ''
            self.store.observe([{**self.event,'key':'account/friend/2','local_id':2,'timestamp':time.time()}])
            await original_sleep(0)
        with patch('whalegirl_linux.proactive.asyncio.sleep',incoming):
            await self.service.run_once()
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.service.snapshot()['runs'][0]['state'],'skipped')

    async def test_send_resource_wait_is_bounded_without_relaxing_the_lock(self):
        self.runtime.processing = '另一个会话正在回复'
        diagnostics={}
        first={**self.event,'payload_json':json.dumps({'anchor':self.event['key']})}
        self.assertFalse(await self.service.private_send_slot(first,self.config.generation,diagnostics,timeout=0))
        self.assertEqual(diagnostics['proactive_send_wait']['outcome'],'timeout')
        self.assertEqual(self.runtime.processing,'另一个会话正在回复')
        self.assertEqual(self.calls,0)

    async def test_contact_permission_can_be_revoked_during_resource_wait(self):
        self.runtime.processing = '另一个会话正在回复'
        diagnostics={}
        first={**self.event,'payload_json':json.dumps({'anchor':self.event['key']})}
        original_sleep=asyncio.sleep
        async def revoke(_):
            self.configure(proactive_private_enabled=False)
            await original_sleep(0)
        with patch('whalegirl_linux.proactive.asyncio.sleep',revoke):
            self.assertFalse(await self.service.private_send_slot(first,self.config.generation,diagnostics))
        self.assertEqual(diagnostics['proactive_send_wait']['outcome'],'permission_changed')
        self.assertEqual(self.gateway.sent,[])

    async def test_unsent_preemption_can_retry_at_decision_gap_without_waiting_an_hour(self):
        now=time.time()
        self.service.touch('friend',last_decision=now)
        self.service.execute('INSERT INTO proactive_runs(id,account,chat,state,created,updated) VALUES(?,?,?,?,?,?)',
            ('preempted','account','friend','skipped',now,now))
        self.assertFalse(self.opportunity(now+149))
        self.assertTrue(self.opportunity(now+151))

    async def test_preemption_retry_does_not_skip_the_interval_after_real_conversation(self):
        self.store.insert({**self.event,'key':'passive-answer'},'sent')
        sent=self.store.latest_sent('account','friend')
        self.service.touch('friend',last_decision=sent)
        self.service.execute('INSERT INTO proactive_runs(id,account,chat,state,created,updated) VALUES(?,?,?,?,?,?)',
            ('preempted','account','friend','skipped',sent,sent))
        self.assertFalse(self.opportunity(sent+151))
        self.assertTrue(self.opportunity(sent+3601))


if __name__=='__main__':unittest.main()

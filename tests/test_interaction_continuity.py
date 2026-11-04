"""Synthetic multi-turn contracts; no model network or WeChat sends."""
import json
import time
import unittest
from unittest.mock import patch

import test_experience_growth as experience_fixtures
from whalegirl_linux.context import build_context
from whalegirl_linux.experience import metadata
from whalegirl_linux.time_context import before_reply
from whalegirl_linux.conversation import habit_static_prompt, warmth_prompt


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = experience_fixtures.ExperienceTests.asyncSetUp
    asyncTearDown = experience_fixtures.ExperienceTests.asyncTearDown
    event = experience_fixtures.ExperienceTests.event
    queue = experience_fixtures.ExperienceTests.queue
    answer = experience_fixtures.ExperienceTests.answer
    configure = experience_fixtures.ExperienceTests.configure
    episode = experience_fixtures.ExperienceTests.episode
    remember = experience_fixtures.ExperienceTests.remember

    def opening(self):
        source = self.event(content='我这回真不玩这个游戏了',sender='friend-a')
        row = self.remember(source,phase='uncertain',summary='群友说暂时不玩，还不清楚是认真决定还是牢骚')
        return source,row

    def continue_event(self, source, row, *, phase='resolved'):
        self.store.observe([source])
        self.service.dynamic_prompt(source,[source])
        value = self.episode(source,continues_id=row['id'],phase=phase,
            summary='群友解释刚才只是牢骚，仍在玩，不将退游当作人物事实')
        result = self.experience.capture(source,[source],value)
        self.experience.settle(source,'silent')
        return result

    async def test_clarification_revises_one_episode_and_preserves_both_sources(self):
        original,row=self.opening()
        later=self.event(2,content='没退啦刚才只是发个牢骚',sender='friend-a')
        changed=self.continue_event(later,row)
        self.assertEqual(changed['id'],row['id'])
        saved=self.runtime.memory_book.get('account',row['id'],[later['chat']])
        self.assertIn('仍在玩',saved['content'])
        self.assertEqual(metadata(saved)['phase'],'resolved')
        self.assertEqual(metadata(saved)['delivery'],'observed')
        evidence={r[0] for r in self.store.db.execute('SELECT source_key FROM memory_evidence WHERE memory_id=?',(row['id'],))}
        self.assertEqual(evidence,{original['key'],later['key']})
        self.assertNotIn(later['key'],self.runtime.memory_book.suppressed_sources('account',[later['chat']]))
        self.assertNotIn(original['key'],self.runtime.memory_book.suppressed_sources('account',[later['chat']]))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0],1)
        self.assertEqual(self.experience.current_events(later),[])

    async def test_another_participant_can_clarify_a_shared_event_without_reassigning_identity(self):
        _,row=self.opening()
        later=self.event(2,content='他刚刚在开玩笑，我们还约了今晚一起玩',sender='friend-b')
        self.continue_event(later,row,phase='open')
        saved=self.experience.current_events(later)[0]
        self.assertEqual(saved['subject'],'friend-a')
        self.assertEqual(set(metadata(saved)['participants']),{'friend-a','friend-b'})
        self.assertEqual(saved['scope'],'chat')

    async def test_continuation_requires_an_offered_current_version(self):
        _,row=self.opening()
        source=self.event(2,content='这次我已经说清楚了')
        self.store.observe([source])
        value=self.episode(source,continues_id=row['id'],phase='resolved')
        self.assertIsNone(self.experience.capture(source,[source],value))
        self.service.dynamic_prompt(source,[source])
        self.runtime.memory_book.change('account',[source['chat']],
            {'op':'update','id':row['id'],'version':row['version']+1,'content':'管理员已核对这段情况'})
        self.assertIsNone(self.experience.capture(source,[source],value))

    async def test_archived_locked_and_foreign_episodes_cannot_be_reopened(self):
        _,row=self.opening()
        source=self.event(2,content='我想补充一下刚才的事情')
        self.store.observe([source])
        self.service.dynamic_prompt(source,[source])
        current=self.runtime.memory_book.get('account',row['id'],[source['chat']])
        self.runtime.memory_book.change('account',[source['chat']],
            {'op':'archive','id':row['id'],'version':current['version']})
        self.assertIsNone(self.experience.capture(source,[source],self.episode(source,continues_id=row['id'],phase='open')))
        self.assertEqual(self.experience.current_events(source),[])

    async def test_events_are_bounded_by_time_chat_role_and_switch(self):
        source,row=self.opening()
        self.assertEqual(len(self.experience.current_events(source)),1)
        self.assertEqual(self.experience.current_events(source,now=source['timestamp']+1900),[])
        self.configure(active_persona_id='different')
        self.assertEqual(self.experience.current_events(source),[])
        self.configure(active_persona_id='')
        foreign={**source,'chat':'another@chatroom'}
        self.configure(allowed_chat_ids=[source['chat'],foreign['chat']])
        self.assertEqual(self.experience.current_events(foreign),[])
        self.configure(experience_growth_enabled=False)
        self.assertEqual(self.experience.current_events(source),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0],1)

    async def test_replaying_clarification_does_not_create_or_reinforce_an_event(self):
        _,row=self.opening()
        source=self.event(2,content='没退啦刚才只是发个牢骚')
        self.continue_event(source,row)
        count=self.store.db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0]
        self.assertIsNone(self.experience.capture(source,[source],self.episode(source,phase='open')))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0],count)

    async def test_event_context_does_not_renew_it_and_presents_at_most_two(self):
        for i in range(1,5):
            self.remember(self.event(i,content=f'今晚第{i}场游戏还没确定时间'),phase='open',summary=f'第{i}场游戏的时间仍未确定')
        source=self.event(8,content='刚来，大家在聊什么')
        rows=self.experience.current_events(source)
        before={r['id']:r['version'] for r in rows}
        self.assertEqual(len(rows),2)
        self.assertIn('不是待办',self.service.dynamic_prompt(source,[source]))
        self.assertEqual({r['id']:r['version'] for r in self.experience.current_events(source)},before)

    async def test_new_frame_preserves_all_people_own_messages_quotes_and_current_time_boundary(self):
        first=self.event(content='我今晚不想继续打了',sender='friend-a')
        own={**self.event(2,content='那先歇一会儿',sender='account'),'is_self':1}
        later=self.event(3,content='其实是刚才连跪了有点上头',sender='friend-a')
        self.store.observe([first,own,later])
        messages,keys,counts=await build_context(self.store,None,self.config.settings,later,[later],{})
        prompt=messages[0]['content']
        self.assertIn('你自己（微信中已出现的发言）',prompt)
        self.assertIn('本轮新发生的交流',prompt)
        self.assertNotIn('本次需要回复的消息',prompt)
        self.assertIn(first['content'],prompt)
        self.assertIn(later['content'],prompt)
        self.assertEqual(counts['context_mode'],'complete_recent')
        self.assertEqual(keys,[])
        clocked=before_reply(prompt,'[dynamic clock]')
        self.assertLess(clocked.index('[dynamic clock]'),clocked.index('【本轮新发生的交流】'))

    async def test_legacy_frame_and_core_persona_are_preserved(self):
        persona='完整核心人格：我是温柔的deepseek酱，也有自己的喜好'
        self.configure(system_prompt_override=persona,experience_growth_enabled=False)
        source=self.event(content='今天过得怎么样')
        self.store.observe([source])
        messages,_,_=await build_context(self.store,None,self.config.settings,source,[source],{})
        self.assertTrue(any(marker in messages[0]['content'] for marker in (
            '【本次需要回复的消息】', '【当前需要回复的消息｜本轮主题优先】')))
        self.configure(experience_growth_enabled=True)
        habits=habit_static_prompt(self.config.settings)
        warmth=warmth_prompt(self.config.settings)
        self.assertIn('完整角色卡优先',habits)
        self.assertIn('不是每轮必做',habits)
        self.assertNotIn('避免只回嗯',warmth)
        self.assertEqual(self.config.settings.system_prompt_override,persona)

    def receipt(self, source, state, text, index=0):
        self.store.insert(source)
        self.service.log(source,'speak')
        identity=self.store.action_start(source['key'],source,index,{'type':'text','display':text})
        self.store.action_finish(identity,state)
        return identity

    async def test_learning_sees_actual_reply_chain_but_only_new_humans_are_evidence(self):
        now=time.time()
        first={**self.event(content='今天只想吐槽一下，不想听建议'),'timestamp':now-90}
        self.store.observe([first])
        identity=self.receipt(first,'sent','好，我听着')
        self.store.db.execute('UPDATE outbound_actions SET sent_at=? WHERE id=?',(now-70,identity))
        self.store.db.commit()
        own={**self.event(2,content='好，我听着',sender='account'),'is_self':1,'timestamp':now-70}
        later={**self.event(3,content='这样听我说就挺好的，谢谢'),'timestamp':now-20}
        self.store.observe([own,later])
        data=self.experience.learning_exchange(later,[later])
        self.assertEqual(data['learnable_keys'],[later['key']])
        self.assertEqual(data['confirmed_actions'][0]['content'],'好，我听着')
        self.assertEqual({r['speaker_role'] for r in data['exchange']},{'human','account_history'})
        self.assertTrue(all(not r['learnable_source'] for r in data['exchange'] if r['speaker_role']=='account_history'))

    async def test_learning_never_calls_failed_uncertain_or_foreign_role_actions_successful(self):
        now=time.time()
        first={**self.event(content='我想先说完再听意见'),'timestamp':now-80}
        self.store.observe([first])
        for i,state in enumerate(('failed','uncertain','sent')):
            identity=self.receipt(first,state,'候选回复'+str(i),index=i)
            self.store.db.execute('UPDATE outbound_actions SET sent_at=? WHERE id=?',(now-60,identity))
        self.store.db.commit()
        later={**self.event(2,content='好，我继续讲刚才的事'),'timestamp':now-10}
        self.store.observe([later])
        data=self.experience.learning_exchange(later,[later])
        self.assertEqual([a['content'] for a in data['confirmed_actions']],['候选回复2'])
        self.configure(active_persona_id='another')
        self.assertEqual(self.experience.learning_exchange(later,[later])['confirmed_actions'],[])

    async def test_background_learning_consumes_mixed_exchange_in_one_existing_call(self):
        from test_chat_habits import Engine
        rows=[self.event(i,content='我更希望先把话说完再听建议') for i in range(1,7)]
        self.store.observe(rows)
        captured=[]
        class CapturingEngine(Engine):
            async def chat(inner,messages,**kwargs):
                captured.append((json.loads(messages[0]['content']),kwargs))
                return json.dumps({'learning_proposals':[]})
        self.runtime.engine_factory=CapturingEngine
        self.assertTrue(await self.service.grow_once())
        data,options=captured[0]
        self.assertEqual(set(data['learnable_keys']),{r['key'] for r in rows})
        self.assertIn('单个笑声',options['system_prompt'])
        self.assertFalse(await self.service.grow_once())
        self.assertEqual(len(captured),1)
        self.assertEqual(self.gateway.sent,[])

    async def test_new_decision_still_uses_one_call_for_silence_or_mixed_actions(self):
        source=self.event(content='今天就想有人听我说，不用给建议',trigger='natural')
        self.queue(source)
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':False,'actions':[]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.gateway.sent,[])

    async def test_default_clock_state_does_not_force_repeated_sleep_dialogue(self):
        from whalegirl_linux.conversation import persona_live_dynamic_prompt,anti_formula_check
        source=self.event(content='这道题怎么理解')
        prompt=persona_live_dynamic_prompt(self.store,self.config.settings,source,[source])
        self.assertNotIn('深夜自然犯困',prompt)
        self.store.observe([{**self.event(i,content='本鲸想想再说',sender='account'),'is_self':1} for i in range(2,5)])
        observed=anti_formula_check(self.store,'account',source['chat'],gentle=True)
        self.assertIn('本鲸',observed['banned_self_refs'])

    async def test_learning_context_rejects_foreign_sources_and_never_uses_self_as_evidence(self):
        source=self.event(content='今天的事情说清楚了')
        foreign={**self.event(2,content='别的群里的私人事情'),'chat':'other@chatroom'}
        own={**self.event(3,content='我来总结你喜欢什么'),'is_self':1}
        self.store.observe([source,foreign,own])
        data=self.experience.learning_exchange(source,[source,foreign,own])
        self.assertEqual(data['learnable_keys'],[source['key']])
        self.assertNotIn('别的群里的私人事情',json.dumps(data,ensure_ascii=False))
        self.configure(memory_auto_capture=False)
        self.assertEqual(self.experience.learning_exchange(source,[source])['exchange'],[])

    async def test_locked_episode_is_readable_but_not_offered_for_rewriting(self):
        source,row=self.opening()
        current=self.runtime.memory_book.get('account',row['id'],[source['chat']])
        self.runtime.memory_book.change('account',[source['chat']],
            {'op':'update','id':row['id'],'version':current['version'],'locked':True})
        later=self.event(2,content='补充一下刚才的情况')
        self.store.observe([later])
        self.assertIn('目前理解',self.service.dynamic_prompt(later,[later]))
        self.assertNotIn(row['id'],later['_interaction_versions'])
        self.assertIsNone(self.experience.capture(later,[later],self.episode(later,continues_id=row['id'],phase='resolved')))

    async def test_older_evidence_cannot_overwrite_a_newer_understanding(self):
        original,row=self.opening()
        older={**self.event(2,content='这是更早说的另一段话'),'timestamp':original['timestamp']-60}
        self.store.observe([older])
        self.service.dynamic_prompt(older,[older])
        self.assertIsNone(self.experience.capture(older,[older],self.episode(older,continues_id=row['id'],phase='open')))

    async def test_many_own_messages_cannot_starve_the_new_human_learning_cursor(self):
        from test_chat_habits import Engine
        first=self.event(content='我会先把事情想清楚再说')
        self.store.observe([first])
        self.store.observe([{**self.event(i,content='历史账号发言',sender='account'),'is_self':1} for i in range(2,70)])
        humans=[self.event(i,content='我想先说完再听意见') for i in range(70,75)]
        self.store.observe(humans)
        captured=[]
        class CapturingEngine(Engine):
            async def chat(inner,messages,**kwargs):
                captured.append(json.loads(messages[0]['content']))
                return json.dumps({'learning_proposals':[]})
        self.runtime.engine_factory=CapturingEngine
        self.assertTrue(await self.service.grow_once())
        self.assertEqual(set(captured[0]['learnable_keys']),{first['key'],*[r['key'] for r in humans]})
        self.assertLessEqual(sum(r['speaker_role']=='account_history' for r in captured[0]['exchange']),20)


if __name__=='__main__':
    unittest.main()

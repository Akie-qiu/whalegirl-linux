"""Offline social/episodic scenarios; all accounts, messages and models are synthetic."""
import json
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import test_autonomous_growth as world_fixtures
import test_chat_habits as fixtures
from whalegirl_linux.app import create_app
from whalegirl_linux.autonomy import AutonomyService
from whalegirl_linux.conversation import persona_live_dynamic_prompt
from whalegirl_linux.memory import memory_parts
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.store import Store


class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    event = fixtures.ChatHabitTests.event
    queue = fixtures.ChatHabitTests.queue
    answer = fixtures.ChatHabitTests.answer

    async def asyncSetUp(self):
        await fixtures.ChatHabitTests.asyncSetUp(self)
        self.configure(autonomy_enabled=True, experience_growth_enabled=True, learning_eagerness=85,
                       min_send_interval=0, chat_cooldown=0, reply_part_interval=.3)
        self.runtime.account_id = 'account'
        self.runtime.gateway_state = {'status':'logged_in'}
        self.service = self.runtime.autonomy
        self.service.sync_mode()
        self.experience = self.service.experience

    async def asyncTearDown(self):
        await self.runtime.close()

    def configure(self, **values):
        self.config.save(self.config.settings.model_copy(update=values))

    def episode(self, event, **values):
        return {'source_key':event['key'], 'evidence':event['content'],
                'summary':'听到群友说答辩通过，替对方松了一口气', 'feeling':'替对方开心', **values}

    def remember(self, event, **values):
        self.store.observe([event])
        result = self.experience.capture(event, [event], self.episode(event, **values))
        self.experience.settle(event, 'silent')
        return result

    async def test_episodes_use_canonical_memory_and_audited_receipts(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        row = self.remember(event)
        self.assertEqual(row['source_class'], 'experience')
        self.assertEqual(row['kind'], 'shared_moment')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0], 1)
        diary = self.experience.diary()
        self.assertEqual((diary[0]['id'], diary[0]['delivery']), (row['id'], 'observed'))
        detail = self.runtime.memory_hub.detail('account', [event['chat']], row['id'])
        self.assertTrue(detail)
        evidence = self.store.db.execute('SELECT excerpt FROM memory_evidence WHERE memory_id=?', (row['id'],)).fetchone()
        self.assertEqual(evidence['excerpt'], event['content'])

    async def test_replay_different_wording_and_restart_do_not_duplicate_experience(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        row = self.remember(event)
        self.assertIsNone(self.experience.capture(event, [event], self.episode(event, summary='群友顺利完成答辩，很值得庆祝')))
        restarted = AutonomyService(self.runtime)
        self.assertIsNone(restarted.experience.capture(event, [event], self.episode(event)))
        self.assertEqual(len(restarted.experience.diary()), 1)
        archived = self.runtime.memory_book.change('account', [event['chat']],
            {'op':'archive', 'id':row['id'], 'version':self.experience.diary()[0]['version']})
        self.assertTrue(archived['archived'])
        self.assertIsNone(restarted.experience.capture(event, [event], self.episode(event)))
        self.assertEqual(restarted.experience.diary(), [])

    async def test_alternate_projection_of_same_native_message_is_not_a_new_experience(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        self.remember(event)
        alternate = {**event, 'key':'account/group@chatroom/alternate-ui-id'}
        self.store.observe([alternate])
        self.assertIsNone(self.experience.capture(alternate,[alternate],self.episode(alternate)))
        self.assertEqual(len(self.experience.diary()),1)

    async def test_fabricated_foreign_quoted_and_self_evidence_is_rejected(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        self.store.observe([event])
        self.assertIsNone(self.experience.capture(event, [event], self.episode(event, source_key='invented')))
        self.assertIsNone(self.experience.capture(event, [event], self.episode(event, evidence='我准备出国留学')))
        foreign = {**event, 'chat':'other@chatroom'}
        self.assertIsNone(self.experience.capture(event, [foreign], self.episode(event)))
        quote = {**self.event(2, content='[当前发言] 恭喜\n[引用原话｜对方] 我准备出国留学'),
                 'payload_json':json.dumps({'reply':{'content':'我准备出国留学'}})}
        self.store.observe([quote])
        self.assertIsNone(self.experience.capture(quote, [quote], self.episode(quote, evidence='我准备出国留学')))
        own = {**self.event(3, content=event['content']), 'is_self':1}
        self.store.observe([own])
        self.assertIsNone(self.experience.capture(own, [own], self.episode(own)))

    async def test_memory_off_and_auto_capture_off_stop_new_experiences(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        self.store.observe([event])
        for field in ('experience_growth_enabled', 'memory_enabled', 'memory_consolidation_enabled', 'memory_auto_capture'):
            self.configure(**{field:False})
            self.assertIsNone(self.experience.capture(event, [event], self.episode(event)))
            self.configure(**{field:True})
        self.assertEqual(self.experience.diary(), [])

    async def test_private_episodes_and_other_personas_cannot_leak_into_group_recall(self):
        self.configure(allowed_chat_ids=['group@chatroom','private-person','other@chatroom'])
        private = {**self.event(content='今天答辩通过了，终于能休息一下'),
                   'key':'account/private-person/1', 'chat':'private-person'}
        row = self.remember(private)
        group = self.event(2,content='答辩后来怎样了')
        self.store.observe([group])
        text = memory_parts(self.runtime.memory_book, self.config.settings, group, [group])[1]
        self.assertNotIn(row['content'], text)
        renamed = {**private, 'key':'account/private-person/2', 'sender':'换了昵称', 'content':'答辩终于结束了'}
        self.store.observe([renamed])
        text = memory_parts(self.runtime.memory_book, self.config.settings, renamed, [renamed])[1]
        self.assertIn(row['content'], text)
        self.configure(active_persona_id='other-persona')
        self.assertEqual(self.experience.diary(), [])
        self.assertNotIn(row['content'], memory_parts(self.runtime.memory_book,self.config.settings,renamed,[renamed])[1])
        self.configure(active_persona_id='')
        self.assertEqual(len(self.experience.diary()), 1)

    async def test_one_main_call_can_reply_or_be_silent_and_keep_an_experience(self):
        event = self.event(content='今天答辩通过了，终于能休息一下', trigger='natural')
        self.queue(event)
        engine = await self.runtime.model()
        engine.answers = [{'should_reply':False, 'actions':[], 'experience_update':self.episode(event)}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 1)
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.experience.diary()[0]['delivery'], 'observed')

    async def test_sent_and_uncertain_are_never_mislabeled_as_each_other(self):
        for mid, expected in ((1, 'sent'), (2, 'uncertain')):
            event = self.event(mid, content='今天答辩通过了，终于能休息一下')
            self.queue(event)
            if expected == 'uncertain':
                self.gateway.uncertain_at = len(self.gateway.sent) + 1
            engine = await self.answer([{'type':'text','text':'终于能歇一会儿了' if mid == 1 else '这回能好好歇歇了'}], experience_update=self.episode(event))
            self.assertEqual(self.experience.diary()[0]['delivery'], expected)
            self.assertFalse(self.runtime.paused_reason)
        self.assertEqual(engine.calls, 2)

    async def test_switch_off_removes_episode_input_and_new_style_without_erasing_data(self):
        event = self.event(content='今天答辩通过了，终于能休息一下')
        row = self.remember(event)
        self.configure(experience_growth_enabled=False)
        self.assertNotIn('体验与相处', self.service.static_prompt())
        text = memory_parts(self.runtime.memory_book,self.config.settings,event,[event])[1]
        self.assertNotIn(row['content'], text)
        self.assertEqual(len(self.experience.diary()), 1)

    async def test_new_mode_uses_observations_without_extreme_taunt_or_invented_life(self):
        event = self.event(content='有点累，陪我聊会儿', trigger='natural')
        self.store.observe([event])
        prompt = persona_live_dynamic_prompt(self.store,self.config.settings,event,[event],natural=True)
        self.assertNotIn('2～18', prompt)
        self.assertNotIn('当下生活小剧场', prompt)
        self.assertIn('群聊实况', self.service.dynamic_prompt(event,[event]))
        self.configure(experience_growth_enabled=False)
        self.assertIn('2～18', persona_live_dynamic_prompt(self.store,self.config.settings,event,[event],natural=True))

    async def test_group_style_and_echo_are_measured_from_distinct_real_people(self):
        now = time.time()
        rows = [self.event(i, content='这下真成高手了', sender='person'+str(i)) for i in range(1,4)]
        for r in rows:r['timestamp']=now-4+r['local_id']
        self.store.observe(rows)
        prompt = self.service.dynamic_prompt(rows[-1], [rows[-1]])
        self.assertIn('3人参与', prompt)
        self.assertIn('同一队形', prompt)
        self.store.observe(rows)
        self.assertIn('3人参与', self.service.dynamic_prompt(rows[-1],[rows[-1]]))

    async def test_unsaid_thought_is_relevant_optional_and_not_a_timer_wakeup(self):
        event = self.event(content='今晚打算去吃火锅')
        self.store.observe([event])
        self.service.accept(event,[event], {'should_reply':False, 'intent_ops':[
            {'op':'plan','kind':'unsaid','source_key':event['key'],'topic':'火锅店的蘸料搭配',
             'after_minutes':0,'expires_minutes':120}]})
        self.assertEqual(len(self.service.pending('account',event['chat'])), 1)
        self.assertEqual(self.service.pending('account',event['chat'],due=True), [])
        self.assertFalse(self.service.opportunity(event['chat'],anchor=event['key']))
        unrelated = self.event(2,content='电脑接口又出问题了')
        self.assertNotIn('蘸料',self.service.dynamic_prompt(unrelated,[unrelated]))
        related = self.event(3,content='火锅蘸料怎么配')
        self.assertIn('蘸料',self.service.dynamic_prompt(related,[related]))

    async def test_intents_and_mood_updates_do_not_cross_personas_or_double_apply(self):
        event = self.event(content='明天有一场重要答辩')
        self.store.observe([event])
        decision = {'should_reply':False,'mood_update':{'source_key':event['key'],'mood':'proud','energy_delta':4},
                    'intent_ops':[{'op':'plan','source_key':event['key'],'topic':'答辩后问问结果',
                                   'after_minutes':60,'expires_minutes':1440}]}
        self.service.accept(event,[event],decision)
        before = dict(self.store.db.execute('SELECT * FROM persona_mood_state').fetchone())
        self.service.accept(event,[event],decision)
        self.assertEqual(before, dict(self.store.db.execute('SELECT * FROM persona_mood_state').fetchone()))
        intent = self.service.pending('account',event['chat'])[0]
        self.configure(active_persona_id='another')
        self.assertEqual(self.service.pending('account',event['chat']), [])
        self.service.accept(event,[event], {'intent_ops':[{'op':'cancel','id':intent['id']}]})
        self.assertEqual(self.service.snapshot()['intents'], [])
        self.configure(active_persona_id='')
        self.assertEqual(len(self.service.pending('account',event['chat'])),1)

    async def test_mood_keeps_its_real_cause_when_another_person_changes_the_topic(self):
        event = self.event(content='你刚写的调试说明很有用，这回终于修好了', sender='friend-a')
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'proud','energy_delta':3,'reason':'得到具体的感谢，替对方松了口气'}})
        next_event = self.event(2,content='刚进群，大家晚上好',sender='friend-b')
        self.store.observe([next_event])
        context = self.experience.emotional_context(next_event)
        self.assertIn('friend-a',context)
        self.assertNotIn('friend-b',context)
        self.assertIn(event['content'],context)
        self.assertIn('得到具体的感谢',context)
        for prompt in (self.service.dynamic_prompt(next_event,[next_event]),
                       self.service.dynamic_prompt(next_event,[next_event],proactive=True)):
            self.assertNotIn(event['content'],prompt)
            self.assertNotIn('得到具体的感谢',prompt)
            self.assertIn('proud',prompt)
        self.assertEqual(self.gateway.sent,[])

    async def test_mood_cause_expires_without_neutral_messages_renewing_it(self):
        event = self.event(content='这句安慰正好说到心里去了，谢谢你')
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'proud','energy_delta':2,'reason':'安慰被接住了'}})
        updated = self.store.db.execute('SELECT updated FROM persona_mood_state').fetchone()[0]
        next_event = self.event(2,content='晚饭想吃什么')
        self.store.observe([next_event])
        self.service.accept(next_event,[next_event],{'should_reply':False})
        self.assertEqual(self.store.db.execute('SELECT updated FROM persona_mood_state').fetchone()[0],updated)
        self.assertIn('安慰被接住了',self.experience.emotional_context(next_event,now=updated+120))
        self.assertEqual(self.experience.emotional_context(next_event,now=updated+3600),'')

    async def test_neutral_update_and_admin_reset_do_not_revive_the_previous_cause(self):
        from whalegirl_linux.conversation import admin_persona_state_action
        event = self.event(content='刚刚有点误会，不过现在说清楚了')
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'flustered','energy_delta':2,'reason':'误会后有点不好意思'}})
        next_event = self.event(2,content='没事啦，大家继续聊')
        self.store.observe([next_event])
        self.service.accept(next_event,[next_event],{'should_reply':False,'mood_update':{
            'source_key':next_event['key'],'mood':'normal','energy_delta':0,'reason':'误会已经澄清'}})
        self.assertEqual(self.experience.emotional_context(next_event),'')
        admin_persona_state_action(self.store,self.config.settings,'account',[event['chat']],
                                  {'op':'reset_mood','chat':event['chat'],'mood':'proud'})
        self.assertEqual(self.experience.emotional_context(next_event),'')

    async def test_emotional_source_does_not_cross_chats_roles_or_disabled_switches(self):
        event = self.event(content='谢谢你一直记得我不吃辣这件事')
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'proud','energy_delta':2,'reason':'记住的偏好确实帮到了对方'}})
        self.assertTrue(self.experience.emotional_context(event))
        other = {**event,'chat':'another@chatroom'}
        self.configure(allowed_chat_ids=[event['chat'],other['chat']])
        self.assertEqual(self.experience.emotional_context(other),'')
        self.configure(active_persona_id='another')
        self.assertEqual(self.experience.emotional_context(event),'')
        self.configure(active_persona_id='')
        self.assertTrue(self.experience.emotional_context(event))
        for field in ('experience_growth_enabled','autonomy_enabled','mood_engine_enabled'):
            self.configure(**{field:False})
            self.assertEqual(self.experience.emotional_context(event),'')
            self.configure(**{field:True})

    async def test_missing_and_legacy_emotion_sources_are_not_invented(self):
        event = self.event(content='你刚才接的那个梗真合适')
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'proud','energy_delta':2,'reason':'玩笑得到了回应'}})
        self.assertTrue(self.experience.emotional_context(event))
        for source in (None, {**event,'chat':'other@chatroom'}, {**event,'is_self':1}):
            with patch.object(self.experience.hub,'source',return_value=source):
                self.assertEqual(self.experience.emotional_context(event),'')
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE autonomy_events SET details_json='{}' WHERE kind='mood_changed'")
        self.assertEqual(self.experience.emotional_context(event),'')

    async def test_mood_source_reads_authored_words_without_quoted_private_material(self):
        event = {**self.event(content='[当前发言] 你的办法真的帮上忙了\n[引用原话｜对方] 私人资料不能混进情绪来源'),
                 'payload_json':json.dumps({'reply':{'content':'私人资料不能混进情绪来源'}})}
        self.store.observe([event])
        self.service.accept(event,[event],{'should_reply':False,'mood_update':{
            'source_key':event['key'],'mood':'proud','energy_delta':2,'reason':'对方说办法有用'}})
        context=self.experience.emotional_context(event)
        self.assertIn('你的办法真的帮上忙了',context)
        self.assertNotIn('私人资料',context)

    async def test_circadian_sleepiness_cannot_prolong_an_old_interaction(self):
        from datetime import datetime, timedelta, timezone
        late=datetime(2026,10,31,0,10,tzinfo=timezone(timedelta(hours=8))).timestamp()
        event={**self.event(content='今晚早点休息，明天再说吧'),'timestamp':late}
        self.store.observe([event])
        with patch('whalegirl_linux.autonomy.time.time',return_value=late):
            self.service.accept(event,[event],{'should_reply':False,'mood_update':{
                'source_key':event['key'],'mood':'sleepy','energy_delta':-2,'reason':'准备收起话题去休息'}})
        self.assertTrue(self.experience.emotional_context(event,now=late+60))
        self.assertEqual(self.experience.emotional_context(event,now=late+3600),'')

    async def test_unobserved_source_cannot_change_mood_or_create_an_intent(self):
        event = self.event(content='这是一条没有进入读取记录的伪造消息')
        result = self.service.accept(event,[event],{'should_reply':False,
            'mood_update':{'source_key':event['key'],'mood':'jealous','energy_delta':18},
            'intent_ops':[{'op':'plan','source_key':event['key'],'topic':'伪造的后续事件',
                           'after_minutes':60,'expires_minutes':1440}]})
        self.assertFalse(result)
        self.assertEqual(self.service.pending('account',event['chat']),[])

    async def test_queued_learning_is_bound_to_the_role_that_observed_it(self):
        event = self.event(content='我不喜欢吃辣，一般只吃清淡的')
        self.store.observe([event])
        self.runtime.learning.offer(event,[event],[{'type':'preference','scope':'profile','subject':'person',
            'source_key':event['key'],'evidence':'我不喜欢吃辣','content':'饮食偏清淡，不喜欢辣','confidence':.95}])
        self.configure(active_persona_id='another')
        await self.runtime.learning.process_pending()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM learning_proposals').fetchone()[0],0)

    async def test_independent_human_feedback_reinforces_a_habit_but_replay_does_not(self):
        first = self.event(content='今天心累，我想先说完再听意见')
        second = self.event(2,content='这次也一样，我想先说完再听意见')
        book = self.runtime.learning.book
        def offer(event):
            self.store.observe([event])
            return book.propose(event,[event],{'type':'persona','scope':'chat','subject':'person',
                'source_key':event['key'],'evidence':'我想先说完再听意见',
                'content':'这位群友倾诉时更希望先被听完，表达困扰时别急着给建议','confidence':.96},self.config.settings)
        one = offer(first)
        self.assertEqual(self.runtime.growth.adopt_feedback(),0)
        self.assertEqual(offer(first)['version'],one['version'])
        reinforced = offer(second)
        self.assertGreater(reinforced['version'],one['version'])
        self.assertEqual(self.runtime.growth.adopt_feedback(),1)
        self.assertEqual(self.runtime.growth.adopt_feedback(),0)

    async def test_legacy_intentions_migrate_without_changing_their_ids_or_deadlines(self):
        legacy_store = Store(self.folder/'legacy.sqlite3')
        with legacy_store.lock, legacy_store.db:
            legacy_store.db.execute('''CREATE TABLE autonomy_intents(id TEXT PRIMARY KEY,account TEXT,chat TEXT,
                source_key TEXT,topic TEXT,fingerprint TEXT,due REAL,expires REAL,state TEXT,
                created REAL,updated REAL,UNIQUE(account,chat,fingerprint))''')
            legacy_store.db.execute('INSERT INTO autonomy_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('legacy-id','account','group@chatroom','source','答辩后问结果','legacy-fingerprint',
                 time.time()+60,time.time()+3600,'pending',time.time(),time.time()))
        legacy_runtime = Runtime(self.config,legacy_store,fixtures.Gateway(),fixtures.ROOT/'styles',engine_factory=fixtures.Engine)
        try:
            rows = legacy_runtime.autonomy.pending('account','group@chatroom')
            self.assertEqual(len(rows),1)
            self.assertEqual((rows[0]['id'],rows[0]['kind'],rows[0]['role_id']),('legacy-id','followup','default'))
        finally:
            await legacy_runtime.close()

    async def test_background_observation_keeps_a_real_episode_without_sending(self):
        rows = [self.event(i,content='今天答辩通过了，终于能休息一下',sender='person'+str(i)) for i in range(1,7)]
        self.store.observe(rows)
        engines = []
        def factory(**kwargs):
            engine = fixtures.Engine(**kwargs)
            engine.answers = [{'learning_proposals':[], 'experience_update':self.episode(rows[-1])}]
            engines.append(engine)
            return engine
        self.runtime.engine_factory = factory
        self.assertTrue(await self.service.grow_once())
        self.assertFalse(await self.service.grow_once())
        self.assertEqual(len(self.experience.diary()),1)
        self.assertEqual(self.experience.diary()[0]['delivery'],'observed')
        self.assertEqual(sum(e.calls for e in engines),1)
        self.assertEqual(self.gateway.sent,[])

    async def test_actual_public_reading_supplies_one_optional_opportunity(self):
        event = self.event(content='大家平时喜欢聊什么')
        self.store.observe([event])
        self.service.considered(event,event['key'])
        self.assertFalse(self.service.opportunity(event['chat'],anchor=event['key']))
        g = world_fixtures.GrowthTests.fake(self)
        g.request('tieba')
        await g.run_once()
        # Expression sharing alone is never a factual proactive opening.
        self.assertFalse(self.service.opportunity(event['chat'],anchor=event['key']))
        row=g.memories()[0]
        with self.store.db:self.store.db.execute("UPDATE growth_insights SET category='knowledge' WHERE memory_id=?",(row['id'],))
        g.edit(row['id'],{'version':row['version'],'chat_enabled':True})
        self.assertTrue(self.service.opportunity(event['chat'],anchor=event['key']))
        first = {**event, 'key':'proactive:test', 'trigger':'proactive'}
        prompt = self.service.dynamic_prompt(first,[event],proactive=True)
        self.assertIn('管理员允许用于聊天的公开认识',prompt)
        self.assertNotIn('https://tieba.baidu.com',prompt)
        self.service.accept(first,[event],{'should_reply':False,'actions':[]},proactive=True)
        self.assertFalse(self.service.opportunity(event['chat'],anchor=event['key']))
        self.assertEqual(self.gateway.sent,[])
        current=g.memories()[0];g.edit(current['id'],{'version':current['version'],'chat_enabled':False})
        self.assertIsNone(self.experience.fresh_reading(first))

    async def test_new_reading_does_not_bypass_a_strict_group_or_quiet_state(self):
        event = self.event(content='大家平时喜欢聊什么')
        self.store.observe([event])
        self.store.baseline('account',event['chat'])
        self.configure(proactive_enabled=True,proactive_chat_ids=[event['chat']],
                       trigger_only_chat_ids=[event['chat']])
        g = world_fixtures.GrowthTests.fake(self)
        g.request('world');await g.run_once()
        self.assertFalse(self.runtime.proactive.permitted(event['chat']))
        self.configure(trigger_only_chat_ids=[])
        self.runtime.chat_control.apply(event['chat'],'quiet',minutes=0)
        self.assertFalse(self.runtime.proactive.permitted(event['chat']))

    async def test_full_core_persona_and_static_prefix_do_not_change_after_experience(self):
        persona = '完整核心人设测试：温柔为主，轻微傲娇和腹黑；我是deepseek酱。'
        self.configure(system_prompt_override=persona)
        before = self.service.static_prompt()
        self.remember(self.event(content='今天答辩通过了，终于能休息一下'))
        self.assertEqual(self.config.settings.system_prompt_override,persona)
        self.assertEqual(self.service.static_prompt(),before)

    async def test_console_api_toggles_and_archives_in_the_existing_memory_book(self):
        app = create_app(self.folder/'console',gateway=fixtures.Gateway(),engine_factory=fixtures.Engine,run_background=False)
        cfg = app.state.config
        cfg.save(self.config.settings, 'test-key')
        headers = {'Authorization':'Bearer '+cfg.admin_token,'Origin':'http://127.0.0.1'}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/autonomy').status_code,401)
            response = client.post('/api/proactive/config',headers=headers,json={'values':{'experience_growth_enabled':False}})
            self.assertEqual(response.status_code,200)
            self.assertFalse(cfg.settings.experience_growth_enabled)
            self.assertTrue(cfg.settings.autonomy_enabled)
            self.assertEqual(cfg.settings.context_messages,100)
            self.assertIn('experiences',client.get('/api/autonomy',headers=headers).json())
            runtime = app.state.runtime
            cfg.save(cfg.settings.model_copy(update={'experience_growth_enabled':True}))
            source = self.event(content='今天答辩通过了，终于能休息一下')
            runtime.store.observe([source])
            row = runtime.autonomy.experience.capture(source,[source],self.episode(source),observed=True)
            result = client.post('/api/memory-hub',headers=headers,json={'op':'archive','id':row['id'],'version':row['version']})
            self.assertEqual(result.status_code,200)
            self.assertEqual(client.get('/api/autonomy',headers=headers).json()['experiences'],[])


if __name__ == '__main__':
    unittest.main()

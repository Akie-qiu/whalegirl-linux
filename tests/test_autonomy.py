"""Agency/legacy switch, real-source learning and durable follow-up regressions."""
import json
import time
import unittest
from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app

import test_chat_habits as habit_fixtures
Engine = habit_fixtures.Engine
import test_proactive as proactive_fixtures
from whalegirl_linux.conversation import pair_sticker, participation, update_mood_after_turn
from whalegirl_linux.settings import Settings


class AutonomyTests(unittest.IsolatedAsyncioTestCase):
    event = habit_fixtures.ChatHabitTests.event
    queue = habit_fixtures.ChatHabitTests.queue
    answer = habit_fixtures.ChatHabitTests.answer

    async def asyncSetUp(self):
        await habit_fixtures.ChatHabitTests.asyncSetUp(self)
        self.configure(autonomy_enabled=True, learning_eagerness=85)
        self.runtime.account_id = 'account'
        self.runtime.gateway_state = {'status':'logged_in'}
        self.service = self.runtime.autonomy
        self.service.sync_mode()

    def configure(self, **values):
        self.config.save(self.config.settings.model_copy(update=values))

    async def asyncTearDown(self):
        await self.runtime.close()

    def intention(self, event, **values):
        decision = {'should_reply':False, 'actions':[], 'intent_ops':[
            {'op':'plan','topic':'答辩后关心结果','source_key':event['key'],
             'after_minutes':60,'expires_minutes':1440, **values}]}
        self.service.accept(event, [event], decision)
        return self.service.pending(event['account'], event['chat'])

    async def test_main_model_can_stay_silent_in_one_call(self):
        event = self.event(content='这轮你们接着聊就好', trigger='natural')
        self.queue(event)
        engine = await self.runtime.model()
        engine.answers = [{'should_reply':False,'actions':[], 'participation_reason':'两位群友正在互相回答'}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 1)
        self.assertEqual(self.gateway.sent, [])
        self.assertEqual(self.store.get(event['key'])['state'], 'ignored')
        self.assertEqual(self.service.snapshot()['events'][0]['kind'], 'silent')

    async def test_sticker_only_does_not_require_text_or_extra_inference(self):
        self.queue(self.event())
        engine = await self.answer([{'type':'sticker','id':'a'*32}])
        self.assertEqual(self.gateway.sent, [('sticker','a'*32)])
        self.assertEqual(engine.calls, 1)

    async def test_switch_restores_old_participation_and_pairing(self):
        event = self.event(content='大家聊', trigger='natural')
        self.store.observe([event])
        actions = [{'type':'text','text':'接住了'}]
        decision = {'sticker_suggestion':'a'*32}
        new = self.config.settings.model_copy(update={'quiet_start_hour':0,'quiet_end_hour':23})
        self.assertTrue(participation(new,self.store,event,[event],now=1770000000)[0])
        self.assertEqual(len(pair_sticker(decision,actions,{'a'*32:'开心'},new,draw=1)[0]), 2)
        old = new.model_copy(update={'autonomy_enabled':False})
        self.assertFalse(participation(old,self.store,event,[event],now=1770000000)[0])
        self.assertEqual(len(pair_sticker(decision,actions,{'a'*32:'开心'},old,draw=1)[0]), 1)
        self.configure(autonomy_enabled=False)
        self.assertEqual(self.service.static_prompt(), '')
        self.assertEqual(self.service.dynamic_prompt(event), '')

    async def test_direct_request_is_not_filtered_by_natural_request_pacing(self):
        event = self.event()
        social = {}
        self.assertTrue(self.service.admit(event,[event],social))
        self.assertTrue(self.service.admit(event,[event],social))
        natural = {**event,'trigger':'natural'}
        self.assertFalse(self.service.admit(natural,[natural],social))

    async def test_natural_pacing_defers_instead_of_discarding_the_last_message(self):
        event = self.event(trigger='natural')
        self.queue(event)
        self.service.admit(event,[event],{})
        await self.runtime.process_next()
        self.assertEqual(self.store.get(event['key'])['state'], 'queued')
        self.assertIn(event['chat'], self.service.waiting_chats())
        self.configure(autonomy_enabled=False)
        self.assertEqual(self.service.waiting_chats(), set())

    async def test_intentions_are_grounded_deduplicated_and_can_be_completed(self):
        event = self.event(content='我明天下午答辩')
        self.store.observe([event])
        first = self.intention(event)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(self.intention(event)), 1)
        self.assertIn('我明天下午答辩', self.service.dynamic_prompt(event))
        later = self.event(2,content='答辩已经过了')
        self.store.observe([later])
        self.service.accept(later,[later], {'should_reply':True,'intent_ops':[{'op':'complete','id':first[0]['id']}]})
        self.assertEqual(self.service.pending('account',event['chat']), [])

    async def test_fabricated_source_or_other_chat_cannot_create_or_cancel_intent(self):
        event = self.event()
        self.store.observe([event])
        self.assertEqual(self.intention(event,source_key='made-up'), [])
        self.assertEqual(self.intention(event,source_key=[]), [])
        existing = self.intention(event)[0]
        foreign = {**event,'chat':'other@chatroom'}
        self.service.accept(foreign,[foreign], {'intent_ops':[{'op':'cancel','id':existing['id']}]})
        self.assertEqual(len(self.service.pending('account',event['chat'])), 1)

    async def test_switch_pauses_due_intentions_without_erasing_them(self):
        event = self.event(); self.store.observe([event]); self.intention(event)
        self.configure(autonomy_enabled=False)
        self.assertEqual(self.service.pending('account',event['chat'],due=True,now=time.time()+7200), [])
        self.assertEqual(len(self.service.snapshot()['intents']), 1)
        self.configure(autonomy_enabled=True)
        self.assertEqual(len(self.service.pending('account',event['chat'],due=True,now=time.time()+7200)), 1)

    async def test_new_mode_uses_grounded_mood_instead_of_keyword_trigger(self):
        event = self.event(content='大肥鱼')
        self.store.observe([event])
        update_mood_after_turn(self.store,self.config.settings,event,[event])
        self.service.accept(event,[event], {'should_reply':False,'mood_update':{
            'mood':'normal','energy_delta':1,'source_key':event['key'],'reason':'只是平常称呼'}})
        row = self.store.db.execute('SELECT mood FROM persona_mood_state WHERE account=? AND chat=?',
                                    ('account',event['chat'])).fetchone()
        self.assertEqual(row['mood'], 'normal')

    async def test_background_learning_observes_unsent_messages_once(self):
        rows = [self.event(i,content='我不喜欢吃辣，平常只吃清淡的',trigger='natural') for i in range(1,7)]
        self.store.observe(rows)
        calls=[]
        def factory(**kwargs):
            engine=Engine(**kwargs)
            engine.answers=[{'learning_proposals':[{'type':'preference','scope':'profile','subject':'person',
                'source_key':rows[-1]['key'],'evidence':'我不喜欢吃辣','content':'不喜欢吃辣，偏好清淡','confidence':.95}]}]
            calls.append(engine)
            return engine
        self.runtime.engine_factory=factory
        self.assertTrue(await self.service.grow_once())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM learning_proposals').fetchone()[0], 1)
        self.assertFalse(await self.service.grow_once())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.gateway.sent, [])

    async def test_disabling_during_background_call_discards_new_result(self):
        self.store.observe([self.event(i) for i in range(1,7)])
        config=self.config
        class SlowEngine(Engine):
            async def chat(self,*args,**kwargs):
                config.save(config.settings.model_copy(update={'autonomy_enabled':False}))
                return json.dumps({'learning_proposals':[]})
        self.runtime.engine_factory=SlowEngine
        self.assertFalse(await self.service.grow_once())
        self.assertEqual(self.service.snapshot()['events'], [])

    async def test_admin_api_switches_both_directions_and_keeps_existing_settings(self):
        app = create_app(self.folder/'console', gateway=habit_fixtures.Gateway(),
                         engine_factory=Engine, run_background=False)
        config = app.state.config
        headers = {'Authorization':'Bearer '+config.admin_token, 'Origin':'http://127.0.0.1'}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/autonomy').status_code, 401)
            for active in (True, False):
                values = config.settings.model_dump()
                values['autonomy_enabled'] = active
                self.assertEqual(client.post('/api/config',headers=headers,json={'settings':values}).status_code, 200)
                state = client.get('/api/autonomy',headers=headers).json()
                self.assertEqual(state['enabled'], active)
                self.assertEqual(config.settings.context_messages, 40)
                self.assertEqual(config.settings.proactive_chat_ids, [])


class AutonomousProactiveTests(unittest.IsolatedAsyncioTestCase):
    message = proactive_fixtures.ProactiveTests.message
    configure = proactive_fixtures.ProactiveTests.configure

    async def asyncSetUp(self):
        await proactive_fixtures.ProactiveTests.asyncSetUp(self)
        self.configure(autonomy_enabled=True)
        self.runtime.autonomy.sync_mode()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_no_new_evidence_does_not_repeat_empty_model_consideration(self):
        self.reply = {'should_reply':False,'reason':'暂时没有想补充的','actions':[]}
        self.assertTrue(await self.service.run_once())
        self.service.touch(self.event['chat'],last_decision=0)
        self.assertFalse(await self.service.run_once())
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.gateway.sent, [])

    async def test_due_intent_can_supply_next_opportunity(self):
        self.runtime.autonomy.considered(self.event,self.event['key'])
        self.assertFalse(self.service.eligible(self.event['chat'],time.time()))
        self.runtime.autonomy.accept(self.event,[self.event], {'should_reply':False,'intent_ops':[
            {'op':'plan','topic':'问问游戏结果','source_key':self.event['key'],
             'after_minutes':1,'expires_minutes':60}]})
        self.assertTrue(self.service.eligible(self.event['chat'],time.time()+120))

    async def test_new_mode_still_honors_trigger_only_group(self):
        self.configure(trigger_only_chat_ids=[self.event['chat']])
        self.assertFalse(await self.service.run_once())
        self.assertEqual(self.calls,0)


if __name__ == '__main__':
    unittest.main()

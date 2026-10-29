"""Conversation-scoped quiet/wake/resume and the consolidated config API."""
import json
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_chat_habits as habits
from whalegirl_linux.app import create_app
from whalegirl_linux.chat_control import ChatControl,command

CHAT='group@chatroom'


class QuietTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue
    answer=habits.ChatHabitTests.answer

    async def poll(self,content,*,mid=1,chat=CHAT,mentioned=True,**extra):
        self.store.baseline('account',chat)
        raw={'serverId':mid,'localId':mid,'type':1,'content':content,'sender':'person',
             'senderName':'群友','isSelf':False,'isMentioned':mentioned,'timestamp':time.time(),**extra}
        async def messages(target,**kwargs):return [raw] if target==chat else []
        self.gateway.messages=messages
        await self.runtime.poll_once()
        return self.store.get(f'account/{chat}/{mid}')

    async def test_local_quiet_and_resume_need_no_model_and_are_idempotent(self):
        row=await self.poll('@机器人 安静半小时')
        state=self.runtime.chat_control.state('account',CHAT)
        self.assertTrue(state['active']);self.assertEqual(row['state'],'ignored')
        await self.runtime.poll_once()
        self.assertEqual(self.runtime.chat_control.state('account',CHAT)['until'],state['until'])
        self.assertIsNone(self.runtime.engine)
        await self.poll('@机器人 可以说话了',mid=2)
        self.assertFalse(self.runtime.chat_control.active('account',CHAT))
        self.assertIsNone(self.runtime.engine)
        self.assertEqual(self.runtime.proactive.chat_state(CHAT)['paused_until'],0)

    async def test_quiet_still_archives_but_only_explicit_wake_replies(self):
        await self.poll('先安静一会儿')
        row=await self.poll('大家继续讨论',mid=2,mentioned=False)
        self.assertEqual(row['state'],'ignored')
        self.assertIn('大家继续讨论',[r['content'] for r in self.store.conversation('account',CHAT,20)])
        row=await self.poll('鲸鱼娘 帮我看下这个',mid=3,mentioned=False)
        self.assertEqual(row['state'],'queued')
        engine=await self.answer([{'type':'text','text':'我看着呢'}])
        self.assertEqual(self.gateway.sent,[('text','我看着呢')]);self.assertEqual(engine.calls,1)
        self.assertTrue(self.runtime.chat_control.active('account',CHAT))
        row=await self.poll('顺便聊别的',mid=4,mentioned=False)
        self.assertEqual(row['state'],'ignored')

    async def test_private_messages_are_not_all_implicit_wakeups(self):
        self.config.save(self.config.settings.model_copy(update={'allowed_chat_ids':[CHAT,'friend']}))
        await self.poll('先安静一会儿',chat='friend',mentioned=False)
        row=await self.poll('普通随口一句',mid=2,chat='friend',mentioned=False)
        self.assertEqual(row['state'],'ignored')
        row=await self.poll('鲸鱼娘 过来一下',mid=3,chat='friend',mentioned=False)
        self.assertEqual(row['state'],'queued')
        self.assertFalse(self.runtime.chat_control.active('account',CHAT))

    async def test_quoted_and_other_peoples_commands_do_not_change_state(self):
        await self.poll('先安静一会儿',mentioned=False)
        self.assertFalse(self.runtime.chat_control.active('account',CHAT))
        await self.poll('先安静一会儿',mid=2,forwarded={'items':[{'content':'安静'}]})
        self.assertFalse(self.runtime.chat_control.active('account',CHAT))

    async def test_state_survives_recreation_expires_and_does_not_replay_old_messages(self):
        event=self.event();self.queue(event)
        state=self.runtime.chat_control.apply(CHAT,'quiet',1)
        control=ChatControl(self.runtime)
        self.assertTrue(control.active('account',CHAT))
        self.assertEqual(self.store.get(event['key'])['state'],'ignored')
        with patch('whalegirl_linux.chat_control.time.time',return_value=state['until']+1):
            self.assertFalse(control.active('account',CHAT))
            self.assertFalse(control.allowed(event))
            self.assertTrue(control.allowed({**event,'timestamp':state['until']+1,'trigger':'natural'}))

    async def test_quiet_during_generation_cancels_before_sending(self):
        row=self.event();self.queue(row);engine=await self.runtime.model()
        async def chat(*args,**kwargs):
            engine.calls+=1
            self.runtime.chat_control.apply(CHAT,'quiet',30)
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'这句不要发'}]})
        engine.chat=chat;await self.runtime.process_next()
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.store.get(row['key'])['state'],'skipped')

    async def test_quiet_stops_the_rest_of_an_outgoing_burst(self):
        row=self.event();self.queue(row);send=self.gateway.send
        async def quiet_after_first(*args,**kwargs):
            receipt=await send(*args,**kwargs)
            self.runtime.chat_control.apply(CHAT,'quiet',30)
            return receipt
        self.gateway.send=quiet_after_first
        await self.answer([{'type':'text','text':'已经开始的第一句'}, {'type':'text','text':'后面别发了'}])
        self.assertEqual(len(self.gateway.sent),1)
        self.assertEqual(self.store.get(row['key'])['state'],'partial')

    async def test_nuanced_quiet_uses_same_model_request_without_confirmation(self):
        row=self.event(content='这会儿先听我们把事情讲完');self.queue(row)
        engine=await self.answer([{'type':'text','text':'不应该确认'}],
            conversation_control={'op':'quiet','source_key':row['key'],'minutes':15})
        self.assertEqual(engine.calls,1);self.assertEqual(self.gateway.sent,[])
        self.assertTrue(self.runtime.chat_control.active('account',CHAT))

    async def test_prior_image_reply_and_proactive_sends_are_blocked(self):
        row=self.event();self.queue(row)
        self.config.save(self.config.settings.model_copy(update={'proactive_enabled':True,'proactive_chat_ids':[CHAT]}))
        self.runtime.chat_control.apply(CHAT,'quiet',30)
        self.assertFalse(self.runtime.proactive.permitted(CHAT))
        with self.assertRaisesRegex(ValueError,'静默'):
            self.runtime.images.check_proactive_permission({'destination':'chat','source_key':row['key']})
        self.runtime.chat_control.apply(CHAT,'resume')
        self.assertTrue(self.runtime.proactive.permitted(CHAT))


class QuietParserTests(unittest.TestCase):
    def test_clear_local_commands_and_non_commands(self):
        for text,expected in [('先安静一会儿',{'op':'quiet'}),('安静半小时',{'op':'quiet','minutes':30}),
                              ('大肥鱼安静十分钟',{'op':'quiet','minutes':10}),
                              ('先别说话直到我叫你',{'op':'quiet','minutes':0}),
                              ('@机器人 可以说话了',{'op':'resume'})]:
            self.assertEqual(command(text,['大肥鱼']),expected,text)
        for text in ['我让他安静半小时','这张图写着闭嘴','有人说可以说话了']:
            self.assertIsNone(command(text),text)


class QuietApiTests(unittest.TestCase):
    def test_one_save_roundtrips_expression_settings_and_quiet_is_scoped(self):
        from pathlib import Path
        import tempfile
        (habits.ROOT/'test-runs').mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='quiet-api-',dir=habits.ROOT/'test-runs'))
        app=create_app(folder,gateway=habits.Gateway(),run_background=False)
        config=app.state.config
        config.save(config.settings.model_copy(update={'account_id':'account','allowed_chat_ids':[CHAT]}))
        with TestClient(app) as client:
            h={'Authorization':'Bearer '+config.admin_token}
            self.assertEqual(client.post('/api/chat-control',json={'chat':CHAT,'op':'quiet'}).status_code,401)
            values={'stickers_enabled':True,'sticker_aggressiveness':'high','reply_max_parts':6,
                    'reply_part_interval':.5,'sticker_cooldown_seconds':120,'quiet_default_minutes':10}
            response=client.post('/api/proactive/config',headers=h,json={'values':values})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual({k:response.json()[k] for k in values},values)
            for op,active in [('quiet',True),('resume',False)]:
                response=client.post('/api/chat-control',headers=h,json={'chat':CHAT,'op':op})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['quiet'][CHAT]['active'],active)
            self.assertEqual(client.post('/api/chat-control',headers=h,json={'chat':'unauthorized','op':'quiet'}).status_code,400)


if __name__=='__main__':unittest.main()

"""Regressions for missing preceding messages and slow/uncertain sticker sends."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from whalegirl_linux.context import build_context
from whalegirl_linux.delivery import reconcile_outbound
from whalegirl_linux.gateway import AgentGateway, Receipt, UIJobError
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Settings
import test_chat_habits as habits
import test_conversation_upgrade as upgrade


class CompleteContextTests(unittest.IsolatedAsyncioTestCase):
    setUp=upgrade.UpgradeTests.setUp
    tearDown=upgrade.UpgradeTests.tearDown
    message=upgrade.UpgradeTests.message
    image=upgrade.UpgradeTests.image

    async def test_short_pronoun_question_keeps_all_recent_people_and_topics(self):
        for i in range(1,45):
            self.message(i,person='alice' if i%2 else 'bob',content=f'背景{i:02} 在聊不同事情')
        current=self.message(45,content='你说是不是');current['trigger']='direct'
        messages,_,counts=await build_context(self.store,None,Settings(vision_enabled=False),current,[current],{})
        prompt=messages[0]['content']
        self.assertEqual(counts['current_messages'],40)
        self.assertEqual(counts['selected_history_messages'],39)
        self.assertEqual(counts['context_mode'],'complete_recent')
        for i in range(6,45):self.assertIn(f'背景{i:02}',prompt)
        self.assertNotIn('背景05',prompt)
        self.assertIn('账号ID=alice',prompt);self.assertIn('账号ID=bob',prompt)

    async def test_aliases_do_not_consume_limit_but_real_repeats_are_kept(self):
        for i in range(1,51):
            event=self.message(i,content='复读' if i>45 else f'有效{i}')
            alias={**event,'key':event['key'].rsplit('/',1)[0]+f'/local:{i}'}
            self.store.observe([alias])
        recent=self.store.conversation('account','a@chatroom',40)
        self.assertEqual(len(recent),40)
        self.assertEqual([r['local_id'] for r in recent],list(range(11,51)))
        self.assertTrue(all('/local:' not in r['key'] for r in recent))
        self.assertEqual(sum(r['content']=='复读' for r in recent),5)

    async def test_long_record_cannot_evict_short_messages(self):
        self.message(1,kind=49,content='长转发'*6000)
        for i in range(2,20):self.message(i,content=f'短消息{i:02}')
        current=self.message(20,content='这几个人刚刚聊什么')
        messages,_,counts=await build_context(self.store,None,Settings(vision_enabled=False,context_char_budget=4000),current,[current],{})
        self.assertEqual(counts['selected_history_messages'],19)
        self.assertGreater(counts['context_truncated_messages'],0)
        for i in range(2,20):self.assertIn(f'短消息{i:02}',messages[0]['content'])

    async def test_separate_image_caption_and_short_question_keep_same_person_image(self):
        photo=self.message(70,kind=3);self.image(photo)
        self.message(71,content='感觉还是她更可爱')
        current=self.message(72,content='你说是不是');current['trigger']='direct'
        _,keys,_=await build_context(self.store,None,Settings(),current,[current],{})
        self.assertEqual(keys,[photo['key']])


class ReceiptLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue
    answer=habits.ChatHabitTests.answer

    def pending(self,event,index=0,**extra):
        self.store.update(event['key'],'uncertain',reply='[原生表情] 开心')
        identity=self.store.action_start(event['key'],event,index,{'type':'sticker','display':'[原生表情] 开心'})
        details={'before_ids':['local:5'],'md5':'a'*32,'local_id':7,'submitted_at':time.time(),**extra}
        self.store.action_finish(identity,'uncertain',error='回执待核实',details=details)
        self.store.metrics(event['key'],{'actions':[{'id':identity,'confirmed':False,'initial_result':'uncertain'}]})
        return identity

    async def test_unknown_result_does_not_block_later_independent_chat(self):
        e=self.event();self.queue(e);self.gateway.uncertain_at=1
        await self.answer([{'type':'sticker','id':'a'*32}])
        self.assertEqual(self.store.get(e['key'])['state'],'uncertain')
        self.assertFalse(self.runtime.paused_reason);self.assertFalse(self.runtime.safety.held('account'))
        self.config.settings.allowed_chat_ids.append('other@chatroom')
        other={**self.event(2),'key':'account/other@chatroom/2','chat':'other@chatroom'}
        self.queue(other);self.runtime.last_send=0
        await self.answer([{'type':'text','text':'接着聊'}])
        self.assertEqual(self.store.get(other['key'])['state'],'sent')
        self.assertEqual(len(self.gateway.sent),2)

    async def test_late_exact_ack_updates_action_turn_metrics_once(self):
        e=self.event();self.queue(e);identity=self.pending(e)
        rows=[{'localId':7,'serverId':77,'isSelf':True,'type':47,'nativeStatus':2,
               'sticker':{'md5':'a'*32},'timestamp':time.time()}]
        result=reconcile_outbound(self.store,'account',e['chat'],rows)
        self.assertEqual(len(result),1);self.assertEqual(self.store.actions(e['key'])[0]['state'],'sent')
        self.assertEqual(self.store.get(e['key'])['state'],'sent')
        self.assertTrue(json.loads(self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(e['key'],)).fetchone()[0])['confirmed'])
        self.assertEqual(reconcile_outbound(self.store,'account',e['chat'],rows),[])
        self.assertEqual(self.gateway.sent,[])

    async def test_ambiguous_receipt_does_not_confirm_two_actions(self):
        e=self.event();self.queue(e);self.pending(e)
        other=self.event(2);self.queue(other);self.pending(other)
        rows=[{'localId':7,'serverId':77,'isSelf':True,'type':47,'nativeStatus':2,'sticker':{'md5':'a'*32}}]
        self.assertEqual(reconcile_outbound(self.store,'account',e['chat'],rows),[])
        self.assertEqual(self.store.get(e['key'])['state'],'uncertain')

    async def test_unknown_archive_does_not_pause_restart_or_clear_admin_hold(self):
        e=self.event();self.queue(e);self.pending(e)
        self.runtime.safety.hold('account','admin_hold','管理员暂停')
        rebuilt=Runtime(self.config,self.store,self.gateway,habits.ROOT/'styles',engine_factory=habits.Engine)
        self.assertFalse(rebuilt.paused_reason)
        self.assertEqual(rebuilt.safety.held('account'),'管理员暂停')


class NativeRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_attempts_is_a_hard_limit_for_pre_send_errors(self):
        gateway=AgentGateway('http://127.0.0.1:6175','test');gateway.ui_jobs_directory=Path('.')
        calls=[]
        async def messages(*args,**kwargs):return []
        async def job(*args,**kwargs):
            calls.append(args);raise UIJobError('not clickable','failed_before_send')
        gateway.messages=messages;gateway.ui_job=job
        try:
            receipt=await gateway.send_sticker('chat','a'*32)
            self.assertEqual((receipt.attempts,len(calls)),(2,2));self.assertTrue(receipt.definite_failure)
        finally:await gateway.close()

    async def test_native_red_failure_is_not_reclicked_as_another_bubble(self):
        gateway=AgentGateway('http://127.0.0.1:6175','test');gateway.ui_jobs_directory=Path('.')
        calls=[]
        async def job(*args,**kwargs):calls.append(args);return {'state':'submitted'}
        async def messages(*args,**kwargs):
            return [] if not calls else [{'localId':7,'serverId':0,'isSelf':True,'type':47,
                'nativeStatus':5,'sticker':{'md5':'a'*32}}]
        gateway.messages=messages;gateway.ui_job=job
        try:
            receipt=await gateway.send_sticker('chat','a'*32)
            self.assertTrue(receipt.definite_failure);self.assertFalse(receipt.retryable)
            self.assertEqual(len(calls),1)
        finally:await gateway.close()

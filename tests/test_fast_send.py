"""Real session protocol with a simulated native actor; no external messages."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest
import uuid

import test_chat_habits as habits
from whalegirl_linux.gateway import AgentGateway
from whalegirl_linux.fast_send import CURRENT

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_fast_turn_test',ROOT/'deploy/native_fast_turn_v1.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)


class LeaseGateway(habits.Gateway):
    send_turn=AgentGateway.send_turn
    send=AgentGateway.send
    send_sticker=AgentGateway.send_sticker

    def __init__(self,folder):
        super().__init__();self.ui_jobs_directory=folder;self.ui_lock=asyncio.Lock();self.rows=[];self.ui_calls=[]
        self.after_send=None;self.lost_response=False
        native.write(folder/'fast-turn-capability.json',{'version':1})

    async def messages(self,chat,**kwargs):return [dict(row) for row in self.rows]

    def native_send(self,part,path,root,name,is_group,*args):
        if part.get('fast_session_nonce'):native.check_guard(part,path)
        part['state']='sending';native.write(path,part)
        kind=part['operation'][5:];number=len(self.rows)+1
        self.sent.append((kind,part.get('text',part.get('md5'))))
        self.rows.append({'isSelf':True,'type':1 if kind=='text' else 47,'content':part.get('text',''),
            'localId':number,'serverId':str(1000+number),'nativeStatus':2,
            'timestamp':time.time(),'sticker':{'md5':part.get('md5')}})
        if self.after_send:self.after_send()
        part['state']='submitted';native.write(path,part)
        return root

    async def ui_job(self,operation,chat,_job_id=None,**values):
        self.ui_calls.append(operation)
        path=self.ui_jobs_directory/((_job_id or uuid.uuid4().hex)+'.json')
        job={'operation':operation,'chat':chat,'created':time.time(),**values}
        native.write(path,job);root={'chat':chat}
        if operation=='send_session':
            if self.lost_response:
                job.update(state='ready',parts={});native.write(path,job)
                command_path=path.with_name(path.stem+'.command.json')
                while not command_path.exists():await asyncio.sleep(.01)
                # A published action may have run, but its result was lost.
                self.sent.append(('text','unknown submission'))
                raise OSError('transport lost after command publication')
            await asyncio.to_thread(native.run_session,job,path,root,chat,chat,True,
                lambda:{'chat':chat},lambda r,n,g:r.get('chat')==n,
                self.native_send,self.native_send,{'snapshots':0,'snapshot_seconds':0},lambda:'window-1')
        else:self.native_send(job,path,root,chat,True)
        return json.loads(path.read_text(encoding='utf-8'))


class FastSendTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    def lease_gateway(self):
        gateway=LeaseGateway(self.folder);self.runtime.gateway=gateway;return gateway

    async def answer(self,actions):
        row=self.event();self.queue(row);engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':actions}]
        await self.runtime.process_next()
        return row,engine

    async def test_one_lease_keeps_independent_receipts_and_one_model_call(self):
        gateway=self.lease_gateway()
        row,engine=await self.answer([{'type':'text','text':'我听着呢'},{'type':'sticker','id':'a'*32}])
        self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertEqual(gateway.sent,[('text','我听着呢'),('sticker','a'*32)])
        self.assertEqual([a['state'] for a in self.store.actions(row['key'])],['sent','sent'])
        self.assertEqual(engine.calls,1);self.assertTrue(self.runtime.last_timing['fast_send']['used'])
        self.assertFalse(gateway.ui_lock.locked());self.assertIsNone(CURRENT.get())

    async def test_switch_restores_legacy_delivery(self):
        gateway=self.lease_gateway();self.config.save(self.config.settings.model_copy(update={'fast_reply_enabled':False}))
        row,_=await self.answer([{'type':'text','text':'第一句'},{'type':'text','text':'第二句'}])
        self.assertEqual(gateway.ui_calls,['send_text','send_text'])
        self.assertEqual(self.store.get(row['key'])['state'],'sent')

    async def test_sender_switch_does_not_change_model_input(self):
        row=self.event();self.queue(row);engine=await self.runtime.model();captured=[]
        async def chat(messages,**options):
            captured.append(copy.deepcopy((messages,options['system_prompt'])))
            return json.dumps({'should_reply':False,'actions':[]})
        engine.chat=chat
        await self.runtime.process_next()
        self.config.settings=self.config.settings.model_copy(update={'fast_reply_enabled':False})
        self.store.update(row['key'],'queued')
        await self.runtime.process_next()
        self.assertEqual(len(captured),2)
        self.assertEqual(captured[0],captured[1])

    async def test_new_config_after_first_action_stops_remaining_actions(self):
        gateway=self.lease_gateway();gateway.after_send=lambda:setattr(self.config,'generation',self.config.generation+1)
        row,_=await self.answer([{'type':'text','text':'第一句'},{'type':'text','text':'取消的第二句'}])
        self.assertEqual(gateway.sent,[('text','第一句')]);self.assertEqual(self.store.get(row['key'])['state'],'partial')

    async def test_lost_submission_result_is_not_resent_or_globally_paused(self):
        gateway=self.lease_gateway();gateway.lost_response=True
        row,_=await self.answer([{'type':'text','text':'只提交一次'}])
        self.assertEqual(len(gateway.sent),1);self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertEqual(self.store.get(row['key'])['state'],'uncertain')
        self.assertFalse(self.runtime.safety.held('account'))

    def test_native_cancel_guard_prevents_submission(self):
        nonce=uuid.uuid4().hex;path=self.folder/(nonce+'.json');job={'chat':'group@chatroom','parts':{}}
        native.write(path.with_name(nonce+'.guard.json'),{'chat':'group@chatroom','allowed':False,'updated':time.time()})
        called=[]
        native.execute_part(job,path,{'chat':'group@chatroom'},'group@chatroom','group@chatroom',True,
            {'index':0,'chat':'group@chatroom','kind':'text','created':time.time(),'values':{'text':'不可发送'}},
            lambda:{},lambda *args:True,lambda *args:called.append(True),lambda *args:called.append(True),
            {'snapshots':0,'snapshot_seconds':0})
        self.assertEqual(called,[]);self.assertTrue(job['parts']['0']['cancelled'])

    async def test_fast_send_session_is_usable_age_and_state(self):
        from whalegirl_linux.fast_send import FastSendSession
        gateway=self.lease_gateway()
        allowed_flag={'ok':True}
        session=FastSendSession(gateway,'chat',lambda:allowed_flag['ok'])
        self.assertFalse(session.is_usable())
        session.started=time.monotonic()
        self.assertTrue(session.is_usable(max_age=12.0))
        allowed_flag['ok']=False
        self.assertFalse(session.is_usable(max_age=12.0))
        allowed_flag['ok']=True
        session.started=time.monotonic()-12.5
        self.assertFalse(session.is_usable(max_age=12.0))
        session.started=time.monotonic()
        session.closed=True
        self.assertFalse(session.is_usable(max_age=12.0))

    async def test_send_turn_reuses_fresh_prewarmed_session_and_replaces_expired(self):
        from whalegirl_linux.fast_send import FastSendSession
        gateway=self.lease_gateway()
        prewarmed=FastSendSession(gateway,'chat',lambda:True)
        self.assertTrue(await prewarmed.start())
        async with gateway.send_turn('chat',lambda:True,prewarmed=prewarmed) as active:
            self.assertIs(active,prewarmed)
            self.assertTrue(active.stats.get('prewarmed'))
            res=await active.submit('text',{'text':'预热直发'})
            self.assertEqual(res['state'],'submitted')
        self.assertTrue(prewarmed.closed)
        self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertFalse(gateway.ui_lock.locked())

        stale=FastSendSession(gateway,'chat',lambda:True)
        self.assertTrue(await stale.start())
        stale.started=time.monotonic()-20.5
        async with gateway.send_turn('chat',lambda:True,prewarmed=stale) as replacement:
            self.assertIsNot(replacement,stale)
            self.assertTrue(stale.closed)
            self.assertTrue(replacement.stats.get('prewarmed_expired'))
        self.assertFalse(gateway.ui_lock.locked())

    async def test_group_members_cache_300s_and_stale_refresh(self):
        gw=AgentGateway('http://127.0.0.1:1','token')
        gw.ui_jobs_directory=self.folder
        calls=[]
        async def fake_native(path,**params):
            calls.append((path,params))
            return {'members':[{'username':'u1','nickname':f'n{len(calls)}'}]}
        gw.native_request=fake_native
        rows1=await gw.group_members('room@chatroom')
        self.assertEqual(len(calls),1)
        ts,cached_rows=gw._members_cache['room@chatroom']
        gw._members_cache['room@chatroom']=(ts-250,cached_rows)
        rows2=await gw.group_members('room@chatroom')
        self.assertEqual(rows2,rows1)
        self.assertEqual(len(calls),1)
        gw._members_cache['room@chatroom']=(ts-350,cached_rows)
        rows3=await gw.group_members('room@chatroom')
        self.assertEqual(rows3,rows1)
        await asyncio.sleep(0.02)
        self.assertEqual(len(calls),2)

    async def test_recent_messages_uses_limit_25_and_falls_back_on_legacy_mock(self):
        from whalegirl_linux.delivery import _recent_messages
        seen=[]
        class ModernGateway:
            async def messages(self,chat,limit=100):
                seen.append((chat,limit))
                return [{'localId':1}]
        class LegacyGateway:
            async def messages(self,chat):
                seen.append((chat,None))
                return [{'localId':2}]
        self.assertEqual(await _recent_messages(ModernGateway(),'c1'),[{'localId':1}])
        self.assertEqual(await _recent_messages(LegacyGateway(),'c2'),[{'localId':2}])
        self.assertEqual(seen,[('c1',25),('c2',None)])

    async def test_multi_bubble_gap_anchors_to_submitted_at(self):
        from whalegirl_linux import runtime as rt_mod
        from whalegirl_linux.gateway import Receipt
        gateway=self.lease_gateway()
        self.config.save(self.config.settings.model_copy(update={'reply_part_interval':0.5,'chat_habits_enabled':True}))
        recorded_sleeps=[]
        orig_sleep=rt_mod.asyncio.sleep
        async def fake_sleep(delay,*args,**kwargs):
            recorded_sleeps.append(delay)
            return await orig_sleep(0)
        async def fake_send(chat,text,**kwargs):
            return Receipt(True,message_id='101',details={'submitted_at':time.time()-0.45})
        gateway.send=fake_send
        rt_mod.asyncio.sleep=fake_sleep
        try:
            row=self.event();self.queue(row)
            await self.runtime._send_plan(row,[row],row,[
                {'type':'text','text':'hi','display':'hi'},
                {'type':'text','text':'ok','display':'ok'},
            ],{},self.config.generation,{})
        finally:
            rt_mod.asyncio.sleep=orig_sleep
        # part_gap for len('ok')==2 with base_gap=0.5 is 0.5s; submitted_at was 0.45s ago so sleep <= ~0.1s, NOT ~0.5s!
        self.assertTrue(recorded_sleeps)
        self.assertLess(recorded_sleeps[0],0.2)

    async def test_delayed_prewarm_fires_during_generating_turn_and_reuses_session(self):
        gateway=self.lease_gateway()
        row=self.event();self.queue(row);engine=await self.runtime.model()
        async def slow_chat(messages,**options):
            deadline=time.monotonic()+5
            while not gateway.ui_calls and time.monotonic()<deadline:
                await asyncio.sleep(0.025)
            await asyncio.sleep(0.08)
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'预热全链路成功'}]})
        engine.chat=slow_chat
        pipeline_task=asyncio.create_task(self.runtime.pipeline.loop())
        try:
            await self.runtime.process_next(defer_delivery=True)
            await asyncio.wait_for(self.runtime.pipeline.queue.join(),timeout=5)
        finally:
            pipeline_task.cancel()
            await asyncio.gather(pipeline_task,return_exceptions=True)
        self.assertEqual(self.store.get(row['key'])['state'],'sent')
        self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertEqual(gateway.sent,[('text','预热全链路成功')])
        self.assertTrue(self.runtime.last_timing['fast_send'].get('prewarmed'))
        self.assertFalse(gateway.ui_lock.locked())

    async def test_delayed_prewarm_closes_cleanly_when_model_declines_reply(self):
        gateway=self.lease_gateway()
        row=self.event();self.queue(row);engine=await self.runtime.model()
        async def slow_silent_chat(messages,**options):
            deadline=time.monotonic()+5
            while not gateway.ui_calls and time.monotonic()<deadline:
                await asyncio.sleep(0.025)
            await asyncio.sleep(0.08)
            return json.dumps({'should_reply':False,'actions':[]})
        engine.chat=slow_silent_chat
        await self.runtime.process_next()
        self.assertEqual(self.store.get(row['key'])['state'],'ignored')
        self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertEqual(gateway.sent,[])
        self.assertFalse(gateway.ui_lock.locked())


if __name__=='__main__':unittest.main()

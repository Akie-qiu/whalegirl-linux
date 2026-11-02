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


if __name__=='__main__':unittest.main()

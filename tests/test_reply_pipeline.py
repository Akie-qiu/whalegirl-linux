"""Bounded overlap of generation and delivery, with real runtime guards."""
import asyncio
import os
from pathlib import Path
import unittest

import test_chat_habits as habits
from test_fast_send import native


class ReplyPipelineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    def other(self,mid,chat='other@chatroom'):
        row=self.event(mid)
        row.update(chat=chat,key='account/'+chat+'/'+str(mid))
        return row

    def start_sender(self):
        task=asyncio.create_task(self.runtime.pipeline.loop())
        self.runtime.tasks.append(task)
        return task

    async def prepare(self):
        self.config.save(self.config.settings.model_copy(update={
            'allowed_chat_ids':['group@chatroom','other@chatroom','third@chatroom'],
            'reply_part_interval':0,'chat_cooldown':0,'min_send_interval':0}))
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'这一轮回复'+str(i)}]} for i in range(5)]
        return engine

    async def generate(self):
        result=await self.runtime.process_next(defer_delivery=True)
        self.runtime.processing='';self.runtime.processing_key=''
        return result

    async def test_other_chat_generates_while_delivery_waits_same_chat_stays_ordered(self):
        engine=await self.prepare();entered=asyncio.Event();release=asyncio.Event()
        original=self.gateway.send
        async def send(*args,**kwargs):
            entered.set();await release.wait();return await original(*args,**kwargs)
        self.gateway.send=send
        first=self.event();self.queue(first)
        await self.generate();self.start_sender();await asyncio.wait_for(entered.wait(),1)
        second=self.event(2);other=self.other(3)
        self.queue(second);self.queue(other)
        await self.generate()
        self.assertEqual(engine.calls,2)
        self.assertEqual(self.store.get(second['key'])['state'],'queued')
        self.assertTrue(self.runtime.pipeline.full)
        self.assertFalse(await self.generate())
        self.assertEqual(engine.calls,2)
        release.set();await asyncio.wait_for(self.runtime.pipeline.queue.join(),2)
        self.assertEqual(self.store.get(first['key'])['state'],'sent')
        self.assertEqual(self.store.get(other['key'])['state'],'sent')
        self.assertFalse(self.runtime.processing)
        await self.generate();await asyncio.wait_for(self.runtime.pipeline.queue.join(),2)
        self.assertEqual(self.store.get(second['key'])['state'],'sent')
        self.assertEqual(engine.calls,3)

    async def test_queued_delivery_obeys_configuration_change_without_sending(self):
        await self.prepare();row=self.event();self.queue(row)
        await self.generate()
        self.config.save(self.config.settings.model_copy(update={'enabled':False}))
        self.start_sender();await asyncio.wait_for(self.runtime.pipeline.queue.join(),1)
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.store.get(row['key'])['state'],'skipped')
        self.assertFalse(self.runtime.processing)

    async def test_queued_delivery_obeys_new_quiet_state(self):
        await self.prepare();row=self.event();self.queue(row);await self.generate()
        self.runtime.chat_control.allowed=lambda _:False
        self.start_sender();await asyncio.wait_for(self.runtime.pipeline.queue.join(),1)
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.store.get(row['key'])['state'],'skipped')

    async def test_delivery_exception_keeps_unknown_ledger_and_serves_next_chat(self):
        engine=await self.prepare();row=self.event();other=self.other(2)
        self.queue(row);self.queue(other)
        calls=0;original=self.gateway.send
        async def send(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:raise OSError('lost submission result')
            return await original(*args,**kwargs)
        self.gateway.send=send
        await self.generate();await self.generate();self.start_sender()
        await asyncio.wait_for(self.runtime.pipeline.queue.join(),2)
        self.assertEqual(calls,2)
        self.assertEqual(self.store.get(row['key'])['state'],'uncertain')
        self.assertEqual(self.store.actions(row['key'])[0]['state'],'uncertain')
        self.assertEqual(self.store.get(other['key'])['state'],'sent')
        self.assertFalse(self.runtime.safety.held('account'))
        self.assertFalse(self.runtime.processing)

    @unittest.skipUnless(hasattr(os,'geteuid') and os.geteuid()==0,'Linux native writer runs as root')
    def test_root_native_writer_retains_private_directory_owner(self):
        folder=self.folder/'private-jobs';folder.mkdir(mode=0o700)
        os.chown(folder,65534,65534)
        path=folder/'result.json'
        native.write(path,{'state':'ready'})
        self.assertEqual((path.stat().st_uid,path.stat().st_gid),(65534,65534))
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        native.write(path,{'state':'done'})
        self.assertEqual(path.stat().st_uid,65534)

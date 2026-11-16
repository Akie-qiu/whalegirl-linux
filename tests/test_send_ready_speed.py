"""Reply-ready latency, bounded preparation, priority and durable receipts."""
import asyncio
import json
import os
import time
import unittest
from unittest.mock import patch

from tests import test_fast_send as fixture
from whalegirl_linux.ui_lock import PriorityUILock, send_access


class PriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_overtakes_waiting_background_without_preempting_owner(self):
        lock=PriorityUILock();await lock.acquire();order=[]
        async def background():
            async with lock:order.append('background')
        async def reply():
            async with send_access(lock):order.append('reply')
        bg=asyncio.create_task(background());await asyncio.sleep(0)
        send=asyncio.create_task(reply());await asyncio.sleep(0)
        self.assertEqual(order,[])
        lock.release();await asyncio.gather(bg,send)
        self.assertEqual(order,['reply','background']);self.assertFalse(lock.locked())

    async def test_cancelled_waiter_does_not_block_next_sender(self):
        lock=PriorityUILock();await lock.acquire()
        waiter=asyncio.create_task(lock.acquire(priority=0));await asyncio.sleep(0)
        waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        lock.release()
        async with send_access(lock):self.assertTrue(lock.locked())
        self.assertFalse(lock.locked())

    async def test_cancelled_handoff_releases_reserved_ownership(self):
        lock=PriorityUILock();await lock.acquire()
        waiter=asyncio.create_task(lock.acquire(priority=0));await asyncio.sleep(0)
        lock.release();waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        self.assertFalse(lock.locked())

    async def test_equal_priority_keeps_fifo_and_legacy_lock_works(self):
        lock=PriorityUILock();await lock.acquire();order=[]
        async def reply(n):
            async with send_access(lock):order.append(n)
        tasks=[]
        for n in range(3):
            tasks.append(asyncio.create_task(reply(n)));await asyncio.sleep(0)
        lock.release();await asyncio.gather(*tasks);self.assertEqual(order,[0,1,2])
        legacy=asyncio.Lock()
        async with send_access(legacy):self.assertTrue(legacy.locked())
        self.assertFalse(legacy.locked())

    async def test_continuous_replies_cannot_starve_background_forever(self):
        lock=PriorityUILock(background_wait_limit=.01);await lock.acquire();order=[]
        async def background():
            async with lock:order.append('background')
        async def reply():
            async with send_access(lock):order.append('reply')
        bg=asyncio.create_task(background());await asyncio.sleep(.02)
        send=asyncio.create_task(reply());await asyncio.sleep(0)
        lock.release();await asyncio.gather(bg,send)
        self.assertEqual(order,['background','reply'])


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.FastSendTests.asyncSetUp(self)
        if os.name=='nt':
            # The production actor runs on Linux. Windows can deny atomic
            # replacement while the simulated reader briefly has a file open.
            original=fixture.native.write
            def write(path,value):
                for attempt in range(25):
                    try:return original(path,value)
                    except PermissionError:
                        if attempt==24:raise
                        time.sleep(.004)
            writer=patch.object(fixture.native,'write',side_effect=write)
            writer.start();self.addCleanup(writer.stop)
    asyncTearDown=fixture.FastSendTests.asyncTearDown
    event=fixture.FastSendTests.event
    queue=fixture.FastSendTests.queue
    lease_gateway=fixture.FastSendTests.lease_gateway

    async def test_prepare_starts_with_model_and_has_no_2_second_delay(self):
        gateway=self.lease_gateway();row=self.event();self.queue(row);engine=await self.runtime.model()
        async def chat(messages,**options):
            until=time.monotonic()+.4
            while not gateway.ui_calls and time.monotonic()<until:await asyncio.sleep(.01)
            self.assertEqual(gateway.ui_calls,['send_session'])
            await asyncio.sleep(.05)
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'准备好了'}]})
        engine.chat=chat;await self.runtime.process_next()
        self.assertEqual(gateway.sent,[('text','准备好了')]);self.assertFalse(gateway.ui_lock.locked())
        self.assertEqual(gateway.ui_calls,['send_session'],json.dumps(self.runtime.last_timing.get('fast_send',{})))

    async def test_idle_preparation_releases_gui_during_slow_model(self):
        gateway=self.lease_gateway();row=self.event();self.queue(row);engine=await self.runtime.model()
        async def chat(messages,**options):
            until=time.monotonic()+1
            while not gateway.ui_calls and time.monotonic()<until:await asyncio.sleep(.01)
            background=asyncio.create_task(gateway.ui_lock.acquire())
            await asyncio.wait_for(background,1)
            gateway.ui_lock.release()
            self.assertFalse(gateway.ui_lock.locked())
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'慢一点也能接上'}]})
        engine.chat=chat
        with patch('whalegirl_linux.fast_send.PREWARM_POLL_SECONDS',.01):
            await self.runtime.process_next()
        self.assertEqual(gateway.sent,[('text','慢一点也能接上')])
        self.assertTrue(self.runtime.last_timing.get('prewarm_idle_expired'),
            json.dumps({k:v for k,v in self.runtime.last_timing.items() if k.startswith('prewarm') or k=='fast_send'}))
        self.assertEqual(gateway.ui_calls,['send_session','send_session'])
        self.assertFalse(gateway.ui_lock.locked())

    async def test_uncontended_preparation_is_kept_until_the_native_margin(self):
        gateway=self.lease_gateway();row=self.event();self.queue(row);engine=await self.runtime.model()
        async def chat(messages,**options):
            until=time.monotonic()+1
            while not gateway.ui_calls and time.monotonic()<until:await asyncio.sleep(.01)
            await asyncio.sleep(.25)
            self.assertTrue(gateway.ui_lock.locked())
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'这回不用重新找窗口'}]})
        engine.chat=chat;await self.runtime.process_next()
        self.assertEqual(gateway.ui_calls,['send_session'])
        self.assertTrue(self.runtime.last_timing['fast_send'].get('prewarmed'))

    async def test_native_lease_margin_still_closes_unused_preparation(self):
        gateway=self.lease_gateway();row=self.event();self.queue(row);engine=await self.runtime.model()
        async def chat(messages,**options):
            until=time.monotonic()+1
            while not gateway.ui_calls and time.monotonic()<until:await asyncio.sleep(.01)
            await asyncio.sleep(.25)
            self.assertFalse(gateway.ui_lock.locked())
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'过期后安全重建'}]})
        engine.chat=chat
        with patch('whalegirl_linux.fast_send.PREWARM_MAX_SECONDS',.1),patch('whalegirl_linux.fast_send.PREWARM_POLL_SECONDS',.01):
            await self.runtime.process_next()
        self.assertEqual(self.runtime.last_timing['prewarm_release_reason'],'lease_age')
        self.assertEqual(gateway.ui_calls,['send_session','send_session'])

    async def test_ready_to_submit_excludes_later_ack_wait(self):
        gateway=self.lease_gateway();row=self.event();self.queue(row);engine=await self.runtime.model()
        native_send=gateway.native_send;messages=gateway.messages
        def send(part,path,*args):
            result=native_send(part,path,*args)
            part['submitted_at']=time.time();fixture.native.write(path,part)
            return result
        async def read(chat,**kw):
            if gateway.rows:await asyncio.sleep(.22)
            return await messages(chat,**kw)
        gateway.native_send=send;gateway.messages=read
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'先发再核对'}]}]
        await self.runtime.process_next();metrics=self.runtime.last_timing
        self.assertGreaterEqual(metrics['first_ack_at']-metrics['first_submit_at'],.20)
        self.assertLess(metrics['reply_ready_to_submit_seconds'],metrics['actions'][0]['seconds'])
        self.assertEqual(self.store.get(row['key'])['state'],'sent')

    async def test_cancelled_session_close_releases_gui_guard(self):
        from whalegirl_linux.fast_send import FastSendSession
        gateway=self.lease_gateway();session=FastSendSession(gateway,'chat',lambda:True)
        await session.start();closing=asyncio.create_task(session.close());await asyncio.sleep(0)
        closing.cancel();await asyncio.gather(closing,return_exceptions=True)
        self.assertFalse(gateway.ui_lock.locked())

    def test_sticker_after_narrow_text_proof_refreshes_full_controls(self):
        import uuid
        nonce=uuid.uuid4().hex;path=self.folder/(nonce+'.json')
        fixture.native.write(path.with_name(nonce+'.guard.json'),{'chat':'chat','allowed':True,'updated':time.time()})
        full={'chat':'chat','sticker_button':True};reads=[];clicked=[]
        def tree():reads.append(True);return full
        def sticker(part,part_path,root,*args):
            self.assertTrue(root['sticker_button']);clicked.append(True);part['state']='submitted'
        job={'chat':'chat'}
        fixture.native.execute_part(job,path,{'send_controls_only':True},'chat','chat',True,
            {'index':0,'kind':'sticker','chat':'chat','created':time.time(),'values':{'md5':'a'*32}},
            tree,lambda *args:True,lambda *args:None,sticker,{'snapshots':0,'snapshot_seconds':0})
        self.assertEqual(reads,[True]);self.assertEqual(clicked,[True])
        self.assertEqual(job['parts']['0']['state'],'submitted')

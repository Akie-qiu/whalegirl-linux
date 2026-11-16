"""Short native UI leases; the existing per-action worker remains in charge."""
import asyncio
from contextvars import ContextVar
import json
from pathlib import Path
import time
import uuid

from .settings import private_write
from .ui_lock import acquire_for_send

CURRENT=ContextVar('whalegirl_fast_send',default=None)
PREWARM_MAX_SECONDS=20.0
PREWARM_POLL_SECONDS=.1


def current(gateway,chat):
    session=CURRENT.get()
    return session if session and session.gateway is gateway and session.chat==chat and session.available else None


class FastSendSession:
    def __init__(self,gateway,chat,allowed):
        self.gateway,self.chat,self.allowed=gateway,chat,allowed
        self.nonce=uuid.uuid4().hex;self.index=0;self.rpc=None;self.monitor=None
        self.locked=False;self.closed=False;self.cancelled=False;self.started=0;self.ready_profile={}
        self.stats={'enabled':True,'used':False,'commands':0,'fallback':''}
        folder=gateway.ui_jobs_directory
        self.path=Path(folder)/(self.nonce+'.json') if folder else None
        self.available=False
        try:self.available=bool(folder and json.loads((Path(folder)/'fast-turn-capability.json').read_text(encoding='utf-8')).get('version')==1)
        except (OSError,ValueError):pass
        if not self.available:self.stats['fallback']='native_capability_unavailable'

    def is_usable(self,max_age=12.0):
        return not self.unusable_reason(max_age)

    def unusable_reason(self,max_age=12.0):
        if self.closed:return 'closed'
        if self.cancelled:return 'cancelled'
        if not self.available:return 'unavailable'
        if not self.started:return 'not_started'
        if self.rpc is not None and self.rpc.done():return 'native_session_finished'
        if max_age is not None and time.monotonic()-self.started>float(max_age):return 'age_limit'
        if callable(self.allowed) and not self.allowed():return 'permission_changed'
        return ''

    def read(self):
        try:return json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def guard(self):
        try:self.cancelled=self.cancelled or not self.allowed()
        except Exception:self.cancelled=True
        private_write(self.path.with_name(self.nonce+'.guard.json'),json.dumps(
            {'allowed':not self.cancelled,'chat':self.chat,'updated':time.time()}))

    async def heartbeat(self):
        while not self.closed:
            self.guard()
            await asyncio.sleep(.5)

    async def start(self):
        if self.rpc:return True
        self.started=time.monotonic()
        await acquire_for_send(self.gateway.ui_lock);self.locked=True
        try:self.guard()
        except OSError:
            self.stats['fallback']='guard_file_unavailable'
            await self.close();self.available=False;return False
        self.monitor=asyncio.create_task(self.heartbeat())
        self.rpc=asyncio.create_task(self.gateway.ui_job('send_session',self.chat,_job_id=self.nonce))
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            value=self.read()
            if value.get('state')=='ready':
                self.ready_profile=value.get('ready_profile') or {}
                self.stats.update(used=True,start_seconds=round(time.monotonic()-self.started,3),
                    ready_at=time.time(),preparation_timing=dict(self.ready_profile))
                return True
            if self.rpc.done():
                if not self.rpc.cancelled() and self.rpc.exception() is not None:
                    self.stats['native_error_type']=type(self.rpc.exception()).__name__
                break
            await asyncio.sleep(.025)
        # No command has been published, so there was no send to repeat.
        self.stats['fallback']='session_not_ready_before_any_command'
        await self.close();self.available=False
        return False

    async def submit(self,kind,values):
        if not self.available:return None
        if not await self.start():return None
        self.guard()
        if self.cancelled:return {'state':'failed_before_send','cancelled':True,'error':'本轮已取消，未发送'}
        index=self.index;self.index+=1
        private_write(self.path.with_name(self.nonce+'.command.json'),json.dumps(
            {'index':index,'kind':kind,'values':values,'chat':self.chat,'created':time.time()},ensure_ascii=False))
        self.stats['commands']+=1
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            job=self.read();part=(job.get('parts') or {}).get(str(index))
            if part:
                result=dict(part);result.update(fast_session=True,job_id=self.nonce,part_index=index)
                timing=result.setdefault('ui_timing',{})
                timing.update(compact_snapshot=True,fast_session=True)
                return result
            if self.rpc.done() or job.get('state')=='done':
                if job.get('state')=='done':
                    return {'state':'failed_before_send','cancelled':True,'error':'快速会话已结束，本动作未提交'}
                break
            await asyncio.sleep(.025)
        # Transport failure cannot establish whether a published command ran.
        return {'state':'uncertain','error':'快速发送回执待核实，不自动重发','fast_session':True}

    async def close(self):
        if self.closed:return
        self.closed=True
        try:
            if self.rpc:
                self.cancelled=True
                try:
                    self.guard()
                    private_write(self.path.with_name(self.nonce+'.command.json'),json.dumps({'close':True}))
                except OSError:pass
                try:await asyncio.wait_for(asyncio.shield(self.rpc),5)
                except (Exception,asyncio.CancelledError):
                    self.rpc.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        finally:
            try:
                if self.monitor:
                    self.monitor.cancel();await asyncio.gather(self.monitor,return_exceptions=True)
            finally:
                if self.locked:self.gateway.ui_lock.release();self.locked=False
                if self.started:self.stats['lease_seconds']=round(time.monotonic()-self.started,3)

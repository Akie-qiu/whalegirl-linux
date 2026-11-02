"""One generator and one sender, overlapped across conversations only."""
import asyncio
import time


class ReplyPipeline:
    def __init__(self,runtime):
        self.runtime=runtime;self.queue=asyncio.Queue(maxsize=2);self.chats=set();self.active=False

    @property
    def full(self):return len(self.chats)>=2

    def submit(self,first,timing,deliver):
        if self.full or first['chat'] in self.chats:raise RuntimeError('Reply pipeline admission conflict')
        self.chats.add(first['chat'])
        self.queue.put_nowait((first,timing,deliver,time.monotonic()))

    async def loop(self):
        while True:
            first,timing,deliver,queued=await self.queue.get();self.active=True
            try:
                timing['delivery_queue_seconds']=round(time.monotonic()-queued,3)
                await deliver()
            except asyncio.CancelledError:raise
            except Exception as exc:
                from .runtime import exception_location
                r=self.runtime;error=f'发送流程异常：{type(exc).__name__}（{exception_location(exc)}）'
                actions=r.store.actions(first['key'])
                pending=any(a['state'] in ('sending','uncertain') for a in actions)
                state='uncertain' if pending else 'partial' if any(a['state']=='sent' for a in actions) else 'failed'
                for action in actions:
                    if action['state']=='sending':r.store.action_finish(action['id'],'uncertain',error=error)
                saved=r.store.get(first['key']) or {}
                r.store.update(first['key'],state,reply=saved.get('reply',''),error=error)
                r.last_error=error
                if pending:r.safety.record(first['account'],'send_receipt_pending',error,chat=first['chat'],level='info')
            finally:
                self.chats.discard(first['chat']);self.active=False;self.queue.task_done()
                self.runtime.send_processing=''

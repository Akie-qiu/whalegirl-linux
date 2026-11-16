"""One GUI owner, with reply submissions ahead of queued maintenance work."""
import asyncio
from contextlib import asynccontextmanager
import heapq
import itertools
import time


class PriorityUILock:
    def __init__(self, background_wait_limit=10.0):
        self._locked = False
        self._waiters = []
        self._sequence = itertools.count()
        self._background_wait_limit = background_wait_limit

    def locked(self):
        return self._locked

    @property
    def waiting_count(self):
        return sum(not future.done() for _, _, future, _ in self._waiters)

    async def acquire(self, priority=10):
        if not self._locked:
            self._locked = True
            return True
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._sequence), future, time.monotonic()))
        try:
            await future
            return True
        except BaseException:
            if future.done() and not future.cancelled():
                self.release()  # Cancelled after ownership was handed over.
            else:
                future.cancel()
            raise

    def release(self):
        if not self._locked:
            raise RuntimeError('UI lock is not acquired')
        # Reply bursts must not permanently starve Moments or maintenance.
        aged = [(seq, i) for i, (priority, seq, future, queued) in enumerate(self._waiters)
                if priority > 0 and not future.done() and time.monotonic()-queued >= self._background_wait_limit]
        if aged:
            _, index = min(aged)
            _, _, future, _ = self._waiters.pop(index)
            heapq.heapify(self._waiters)
            future.set_result(True)
            return
        while self._waiters:
            _, _, future, _ = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(True)
                return
        self._locked = False

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()


async def acquire_for_send(lock):
    if isinstance(lock, PriorityUILock):
        await lock.acquire(priority=0)
    else:
        await lock.acquire()


def waiting_ui_tasks(lock):
    if isinstance(lock, PriorityUILock):
        return lock.waiting_count
    return sum(not future.done() for future in (getattr(lock, '_waiters', None) or ()))


@asynccontextmanager
async def send_access(lock):
    await acquire_for_send(lock)
    try:
        yield
    finally:
        lock.release()

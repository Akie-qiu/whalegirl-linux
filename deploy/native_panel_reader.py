"""Fresh sticker-panel reads over one reusable, locked AT-SPI connection."""
import importlib
from pathlib import Path
import sys
import time


NATIVE_DIRECTORY = Path(__file__).resolve().parent
if str(NATIVE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(NATIVE_DIRECTORY))

STATE_DIRECTORY = Path('/data/native-tools/a11y-state')
_BUS = None


def _discard_bus():
    global _BUS
    bus, _BUS = _BUS, None
    if bus is not None:
        try:
            bus.connection.close_sync(None)
        except Exception:
            pass


def read(sticker_panel_ref):
    """Read live objects and bounds; only the D-Bus connection is reused."""
    global _BUS
    started = time.monotonic()
    locked = started
    safe = None
    try:
        safe = importlib.import_module('a11y_safe')
        ref = safe.parse_sticker_panel_ref(sticker_panel_ref)
        with (STATE_DIRECTORY / 'read.lock').open('r+') as lock:
            acquired = False
            try:
                safe.acquire_read_lock(lock)
                acquired = True
                locked = time.monotonic()
                deadline = started + 6.5
                if _BUS is None:
                    _BUS = safe.A11yBus(deadline=deadline)
                else:
                    _BUS.deadline = deadline
                tree, stats = safe.dump_tree(_BUS, skip_messages=True,
                                            root_ref=ref, deadline=deadline)
                if not tree:
                    raise RuntimeError('Native sticker panel snapshot unavailable')
                stats.update(at=time.time(), elapsed_seconds=round(time.monotonic() - started, 4),
                             lock_wait_seconds=round(locked - started, 4), ok=True, panel_read=True)
                safe.publish_stats(STATE_DIRECTORY, stats)
                return tree
            finally:
                if acquired:
                    failing = sys.exc_info()[0] is not None
                    try:
                        safe.fcntl.flock(lock, safe.fcntl.LOCK_UN)
                    except Exception:
                        if not failing:
                            raise
    except BaseException as exc:
        _discard_bus()
        if safe is not None:
            try:
                safe.publish_stats(STATE_DIRECTORY, {
                    'at': time.time(), 'ok': False, 'error': type(exc).__name__, 'panel_read': True,
                    'elapsed_seconds': round(time.monotonic() - started, 4),
                    'lock_wait_seconds': round((locked if locked > started else time.monotonic()) - started, 4),
                })
            except Exception:
                pass
        raise

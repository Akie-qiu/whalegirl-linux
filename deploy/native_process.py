"""Retry only failed process creation; never repeat a submitted UI action."""
import errno
import subprocess
import time


def run(*args, **kwargs):
    for delay in (.08, .2, None):
        try:
            return subprocess.run(*args, **kwargs)
        except OSError as exc:
            if exc.errno != errno.EAGAIN or delay is None:
                raise
            time.sleep(delay)

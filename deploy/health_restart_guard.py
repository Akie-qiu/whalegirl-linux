#!/usr/bin/python3
"""Block only the gateway's UI-timeout SIGKILL of the pinned WeChat process.

Mounted in an otherwise empty PATH directory. Other callers, signals and
processes use the original kill executable. The gateway still starts a new
client when the real WeChat process exits; a missing accessibility tree alone
must not destroy an authenticated session.
"""
import json
import os
from pathlib import Path
import sys
import time


def guarded_target(arguments, *, parent=None, proc=Path('/proc')):
    if len(arguments) != 2 or arguments[0] != '-9' or not arguments[1].isdigit():
        return None
    pid = int(arguments[1])
    if pid <= 1:
        return None
    parent = os.getppid() if parent is None else parent
    try:
        if (proc / str(parent) / 'exe').resolve() != Path('/opt/agent-server/agent-server'):
            return None
        target = proc / str(pid)
        native = pid if ((target/'comm').read_text().strip() == 'wechat' and
                         (target/'exe').resolve() == Path('/opt/wechat/wechat')) else None
        if native is None:
            # Upstream find_wechat_pid uses pgrep -f /usr/bin/wechat. It can
            # select `su ... /usr/bin/wechat`, whose termination tears down the
            # client session too. Protect that exact launch ancestor only while
            # its real pinned WeChat descendant is still present.
            if (target/'exe').resolve() not in {Path('/usr/bin/su'),Path('/usr/sbin/runuser'),
                                               Path('/usr/bin/bash'),Path('/usr/bin/dash')}:
                return None
            if b'/usr/bin/wechat' not in (target/'cmdline').read_bytes():
                return None
            pending = (target/'task'/str(pid)/'children').read_text().split()
            visited = set()
            while pending and len(visited) < 32:
                child = pending.pop()
                if child in visited:
                    continue
                visited.add(child)
                path = proc/child
                if (path/'exe').resolve() == Path('/opt/wechat/wechat'):
                    native = int(child)
                    break
                pending.extend((path/'task'/child/'children').read_text().split())
            if native is None:
                return None
        ticks = (target / 'stat').read_text().rsplit(') ', 1)[1].split()[19]
        return {'pid': pid, 'wechat_pid': native, 'started_ticks': ticks}
    except (OSError, ValueError, IndexError):
        return None


def record_block(target, directory=Path('/data/native-tools/health-state')):
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / 'restart-guard.json'
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    now = time.time()
    result = {'at': now, 'first_at': previous.get('first_at', now),
              'blocked_count': previous.get('blocked_count', 0) + 1,
              'reason': 'accessibility_timeout_is_not_process_failure', **target}
    report = now - previous.get('reported_at', 0) >= 60
    result['reported_at'] = now if report else previous.get('reported_at', 0)
    pending = directory / ('restart-guard-' + str(os.getpid()) + '.pending')
    pending.write_text(json.dumps(result))
    pending.chmod(0o600)
    pending.replace(path)
    if report:
        print('WeChat restart guard: preserved running client after UI timeout', file=sys.stderr)


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    target = guarded_target(arguments)
    if target:
        # An unavailable diagnostic file must not enable the destructive action.
        try:
            record_block(target)
        except OSError:
            pass
        return 75
    os.execv('/usr/bin/kill', ['/usr/bin/kill', *arguments])


if __name__ == '__main__':
    raise SystemExit(main())

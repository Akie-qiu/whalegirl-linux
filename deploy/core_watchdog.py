"""Restart the Python reply core once when its HTTP health check stays down."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


STATE = Path('/var/lib/whalegirl-linux/stability/core-watchdog.json')
HEALTH_URL = 'http://127.0.0.1:8092/health'
SERVICE = 'whalegirl-linux.service'
FAILURE_THRESHOLD = 3
RESTART_COOLDOWN = 900
HEALTH_TIMEOUT = 2
RECOVERY_TIMEOUT = 20


def load_state():
    try:
        value = json.loads(STATE.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(value):
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = STATE.with_suffix('.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(pending, 0o600)
    os.replace(pending, STATE)


def healthy(timeout=HEALTH_TIMEOUT):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(HEALTH_URL, timeout=timeout) as response:
        value = json.load(response)
    return value.get('status') == 'ok' and value.get('wechat_version') == '4.1.1.8'


def resource_snapshot():
    """Read bounded service cgroup metrics for the private watchdog state."""
    result = {}
    try:
        raw = subprocess.check_output(
            ['systemctl', 'show', 'whalegirl-linux.service',
             '--property=MainPID', '--property=MemoryCurrent', '--property=MemoryHigh',
             '--property=MemoryMax', '--property=MemorySwapCurrent',
             '--property=TasksCurrent', '--property=TasksMax'],
            text=True, timeout=3)
        result.update(dict(line.split('=', 1) for line in raw.splitlines() if '=' in line))
        pid = int(result.get('MainPID', '0'))
        if pid:
            memberships = (Path('/proc') / str(pid) / 'cgroup').read_text().splitlines()
            relative = next(line.split(':', 2)[2] for line in memberships if line.startswith('0::'))
            cgroup = Path('/sys/fs/cgroup') / relative.lstrip('/')
            for name in ('memory.current', 'memory.high', 'memory.max',
                         'memory.swap.current', 'memory.events', 'memory.pressure'):
                path = cgroup / name
                if not path.is_file():
                    continue
                if name == 'memory.events':
                    result[name] = {
                        key: int(value) for key, value in
                        (line.split() for line in path.read_text().splitlines())
                    }
                elif name == 'memory.pressure':
                    result[name] = path.read_text().strip()
                else:
                    result[name] = path.read_text().strip()
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        result['resource_probe'] = 'unavailable'
    return result


def transition(state, now, is_healthy):
    state = dict(state)
    if is_healthy:
        recovered = state.get('consecutive_failures', 0) > 0
        state.update(consecutive_failures=0, last_healthy_at=now, last_result='healthy',
                     last_probe_at=now)
        return state, 'recovered' if recovered else 'healthy'
    previous_probe = float(state.get('last_probe_at', 0))
    if previous_probe and now - previous_probe > 180:
        state['consecutive_failures'] = 0
    failures = int(state.get('consecutive_failures', 0)) + 1
    state.update(consecutive_failures=failures, last_failure_at=now, last_probe_at=now,
                 last_result='unhealthy')
    if failures < FAILURE_THRESHOLD:
        return state, 'waiting'
    if now - float(state.get('last_restart_at', 0)) < RESTART_COOLDOWN:
        return state, 'cooldown'
    state.update(last_restart_at=now, restart_attempts=int(state.get('restart_attempts', 0)) + 1,
                 last_result='restart_requested')
    return state, 'restart'


def wait_for_recovery():
    deadline = time.monotonic() + RECOVERY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if healthy():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _probe():
    try:
        return healthy()
    except Exception:
        return False


def main():
    now = time.time()
    state, action = transition(load_state(), now, _probe())
    state['resources'] = resource_snapshot()
    if action in ('healthy', 'recovered'):
        save_state(state)
        print(json.dumps({'status': action, 'consecutive_failures': 0}))
        return
    if action != 'restart':
        save_state(state)
        print(json.dumps({'status': action,
                          'consecutive_failures': state['consecutive_failures']}))
        return

    # Persist the cooldown before touching systemd, so a failed restart cannot
    # turn a one-minute timer into an uncontrolled restart loop.
    save_state(state)
    try:
        subprocess.run(['systemctl', 'restart', SERVICE], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        state['last_result'] = 'restart_failed:' + type(exc).__name__
        save_state(state)
        print(json.dumps({'status': state['last_result'],
                          'consecutive_failures': state['consecutive_failures']}))
        return
    recovered = wait_for_recovery()
    if recovered:
        state, _ = transition(state, time.time(), True)
        state.update(last_restart_at=now, last_result='restarted_and_healthy')
    else:
        state['last_result'] = 'restarted_but_unhealthy'
    save_state(state)
    print(json.dumps({'status': state['last_result'],
                      'consecutive_failures': state['consecutive_failures']}))


if __name__ == '__main__':
    main()

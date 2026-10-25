"""Restart the Python reply core once when its HTTP health check stays down."""
import json
import os
import pwd
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
CONTAINER = 'whalegirl-wechat-candidate-4118'
NATIVE_PUBLIC = Path('/var/lib/whalegirl-linux/core/stability/native-resources.json')


def native_resources():
    """Measure the container's own budget, independent of free host RAM."""
    info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], text=True, timeout=4))[0]
    pid = info['State']['Pid']
    relative = next(line.split(':', 2)[2] for line in (Path('/proc')/str(pid)/'cgroup').read_text().splitlines()
                    if line.startswith('0::'))
    cg = Path('/sys/fs/cgroup')/relative.lstrip('/')
    stats = dict((k, int(v)) for k, v in (line.split() for line in (cg/'memory.stat').read_text().splitlines()))
    limit = int((cg/'memory.max').read_text())
    current = int((cg/'memory.current').read_text())
    # Clean file cache is reclaimable; anon + shared memory are the working-set risk.
    working = max(0, current-stats.get('inactive_file', 0))
    processes = []
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if info['Id'] not in (process/'cgroup').read_text():
                continue
            fields = dict(line.split(':', 1) for line in (process/'status').read_text().splitlines() if ':' in line)
            rss = int(fields.get('VmRSS', '0 kB').split()[0])*1024
            if rss >= 32*1024**2:
                processes.append({'pid': int(process.name), 'name': fields['Name'].strip(), 'rss': rss,
                                  'anon': int(fields.get('RssAnon', '0 kB').split()[0])*1024,
                                  'shmem': int(fields.get('RssShmem', '0 kB').split()[0])*1024})
        except (OSError, ValueError):
            continue
    return {'at': time.time(), 'container_id': info['Id'][:12], 'cgroup': str(cg), 'memory_current': current,
            'memory_limit': limit, 'working_bytes': working, 'ratio': round(working/limit, 3),
            'pressure': working >= limit*.82 or (_mem_available_mb() or 0) < 240,
            'anon': stats.get('anon', 0), 'shmem': stats.get('shmem', 0),
            'swap_current': int((cg/'memory.swap.current').read_text()),
            'events': dict((k, int(v)) for k, v in (line.split() for line in (cg/'memory.events').read_text().splitlines())),
            'processes': sorted(processes, key=lambda p:p['rss'], reverse=True)[:8]}


def publish_native_resources(value):
    NATIVE_PUBLIC.parent.mkdir(parents=True, exist_ok=True)
    temporary = NATIVE_PUBLIC.with_suffix('.pending')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.chown(temporary, 0, pwd.getpwnam('whalegirl-ai').pw_gid)
    temporary.chmod(0o640)
    temporary.replace(NATIVE_PUBLIC)


def maintain_native_memory(state, now):
    sample = native_resources()
    cg = Path(sample['cgroup'])
    # Reclaim before the hard OOM boundary. Do not kill WeChat or wxocr.
    high = int(sample['memory_limit']*.90)//4096*4096
    try:
        if (cg/'memory.high').read_text().strip() != str(high):
            (cg/'memory.high').write_text(str(high))
        sample['high_bytes'] = high
        if sample['ratio'] >= .86 and now-state.get('last_native_reclaim_at', 0) >= 180:
            state['last_native_reclaim_at'] = now
            try:
                (cg/'memory.reclaim').write_text(str(64*1024**2))
                sample['reclaim'] = 'requested_64MiB'
            except OSError as exc:
                # EAGAIN means some but not all requested bytes could be reclaimed.
                sample['reclaim'] = 'partial' if exc.errno == 11 else type(exc).__name__
    except OSError as exc:
        sample['policy_error'] = type(exc).__name__
    publish_native_resources(sample)
    return sample


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


def _mem_available_mb():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def should_reap_process(elapsed_seconds, cmdline):
    """Return True if a helper/test/webview process is hung or non-essential."""
    if '/opt/tools/a11y-dump' in cmdline and elapsed_seconds > 20:
        return True
    if 'xclip -selection clipboard' in cmdline and elapsed_seconds > 90:
        return True
    if (' -m unittest' in cmdline or 'pytest' in cmdline) and elapsed_seconds > 300:
        return True
    if '/opt/wechat/RadiumWMPF/runtime/crashpad_handler' in cmdline:
        return True
    if '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx' in cmdline and '--type=' in cmdline:
        return True
    return False


def maintain_memory_hygiene(state, now):
    """Keep host and container free of WeChatAppEx bloat, hung helpers, and low-RAM stalls."""
    reaped = []
    stub_mounted = False
    dropped = False
    try:
        cpid_raw = subprocess.check_output(
            ['docker', 'inspect', '--format', '{{.State.Pid}}', 'whalegirl-wechat-candidate-4118'],
            text=True, timeout=4).strip()
        cpid = int(cpid_raw) if cpid_raw.isdigit() else 0
        if cpid > 0:
            mountinfo = Path(f'/proc/{cpid}/mountinfo').read_text()
            if '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx' not in mountinfo:
                subprocess.run(
                    ['nsenter', '-t', str(cpid), '-m', 'sh', '-c',
                     'printf "#!/bin/sh\\nexec sleep 2147483647\\n" > /tmp/wechat_appex_stub && '
                     'chmod 0755 /tmp/wechat_appex_stub && '
                     'mount -n --bind /tmp/wechat_appex_stub /opt/wechat/RadiumWMPF/runtime/WeChatAppEx'],
                    timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['pkill', '-9', '-f', '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx'],
                               timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            stub_mounted = '/opt/wechat/RadiumWMPF/runtime/WeChatAppEx' in Path(f'/proc/{cpid}/mountinfo').read_text()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    try:
        ps_out = subprocess.check_output(['ps', '-eo', 'pid=,etimes=,args='], text=True, timeout=4)
        for line in ps_out.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            pid, elapsed, cmd = int(parts[0]), int(parts[1]), parts[2]
            if should_reap_process(elapsed, cmd):
                try:
                    os.kill(pid, 9)
                    reaped.append(pid)
                except OSError:
                    pass
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        swappiness = Path('/proc/sys/vm/swappiness')
        if swappiness.is_file() and int(swappiness.read_text().strip()) > 20:
            swappiness.write_text('20\n')
    except (OSError, ValueError):
        pass

    avail_before = _mem_available_mb()
    if avail_before is not None and avail_before < 320 and now - float(state.get('last_cache_drop_at', 0)) >= 600:
        try:
            subprocess.run(['sync'], timeout=5)
            Path('/proc/sys/vm/drop_caches').write_text('1\n')
            state['last_cache_drop_at'] = now
            dropped = True
        except (OSError, subprocess.SubprocessError):
            pass
    avail_after = _mem_available_mb()
    return {
        'mem_available_mb': avail_after if avail_after is not None else avail_before,
        'reaped_pids': reaped[:20],
        'appex_stub_mounted': stub_mounted,
        'cache_dropped': dropped,
    }


def main():
    now = time.time()
    prior = load_state()
    hygiene = maintain_memory_hygiene(prior, now)
    state, action = transition(prior, now, _probe())
    state['hygiene'] = hygiene
    state['resources'] = resource_snapshot()
    try:
        native = maintain_native_memory(state, now)
        state['native'] = native
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        state['native_probe_error'] = type(exc).__name__
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

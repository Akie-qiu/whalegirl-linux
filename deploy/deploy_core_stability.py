"""Apply bounded core resource limits and install its guarded watchdog."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request


BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
CONTAINER = 'whalegirl-wechat-candidate-4118'
EXPECTED_VERSION = '4.1.1.8'
SERVICE = 'whalegirl-linux.service'
STAMP = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)

OLD_HEALTH = '''    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0", "wechat_version": config.settings.expected_wechat_version}
'''
NEW_HEALTH = '''    @app.get("/health")
    async def health():
        failed_tasks = sum(task.done() for task in runtime.tasks)
        return {"status": "degraded" if failed_tasks else "ok",
                "background_tasks": len(runtime.tasks),
                "failed_background_tasks": failed_tasks,
                "version": "0.1.0",
                "wechat_version": config.settings.expected_wechat_version}
'''
OLD_RUN = '''    uvicorn.run("whalegirl_linux.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("WHALEGIRL_PORT", "8092")), access_log=False)
'''
NEW_RUN = '''    uvicorn.run("whalegirl_linux.app:create_app", factory=True, host="127.0.0.1",
                port=int(os.environ.get("WHALEGIRL_PORT", "8092")), access_log=False,
                limit_concurrency=32, backlog=128, timeout_keep_alive=5,
                timeout_graceful_shutdown=30)
'''


def run(args, *, timeout=30, capture=True):
    return subprocess.run(args, check=True, timeout=timeout,
                          capture_output=capture, text=True).stdout.strip() if capture else \
        subprocess.run(args, check=True, timeout=timeout).returncode


def atomic_text(path, content, *, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.pending-' + STAMP)
    pending.write_text(content, encoding='utf-8')
    os.chmod(pending, mode)
    os.replace(pending, path)


def atomic_copy(source, target, *, mode=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(target.name + '.pending-' + STAMP)
    shutil.copy2(source, pending)
    if mode is not None:
        os.chmod(pending, mode)
    os.replace(pending, target)


def save_backup(backup, paths):
    for name, path in paths.items():
        if path.is_file():
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def require_no_interrupted_sends():
    db = sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True, timeout=3)
    try:
        for table in ('inbox', 'outbound_actions'):
            count = db.execute('SELECT count(*) FROM ' + table + ' WHERE state=?',
                               ('sending',)).fetchone()[0]
            if count:
                raise SystemExit('Deployment stopped: an outbound send is in progress; no service restart performed')
    finally:
        db.close()


def validate_container():
    info = json.loads(run(['docker', 'inspect', CONTAINER]))[0]
    if not info['State']['Running'] or info['State']['OOMKilled']:
        raise SystemExit('Pinned WeChat container is not running cleanly; no changes made')
    version = run(['docker', 'exec', CONTAINER, 'dpkg-query', '-W',
                   '-f=${Version}', 'wechat'])
    if version != EXPECTED_VERSION:
        raise SystemExit('Unexpected WeChat version; no changes made')
    return {'id': info['Id'][:12], 'restart_count': info['RestartCount'], 'version': version}


def replace_exact(path, old, new):
    content = path.read_text(encoding='utf-8')
    if new in content:
        return 'already_applied'
    if content.count(old) != 1:
        raise SystemExit('Live source did not match the reviewed patch; no replacement made: ' + path.name)
    atomic_text(path, content.replace(old, new, 1), mode=path.stat().st_mode & 0o777)
    return 'patched'


def api_json(url, token=None, timeout=4):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def await_health(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = api_json('http://127.0.0.1:8092/health', timeout=2)
            if value.get('status') == 'ok' and value.get('wechat_version') == EXPECTED_VERSION:
                return value
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit('Core did not become healthy; backup retained for recovery')


def main():
    stage = Path(sys.argv[1]).resolve()
    files = {
        'deploy/core_watchdog.py': stage / 'deploy/core_watchdog.py',
        'deploy/whalegirl-core-watchdog.service': stage / 'deploy/whalegirl-core-watchdog.service',
        'deploy/whalegirl-core-watchdog.timer': stage / 'deploy/whalegirl-core-watchdog.timer',
        'whalegirl_linux/app.py': stage / 'whalegirl_linux/app.py',
        'whalegirl_linux/__main__.py': stage / 'whalegirl_linux/__main__.py',
    }
    if any(not path.is_file() for path in files.values()):
        raise SystemExit('A required staged file is missing')

    container_before = validate_container()
    require_no_interrupted_sends()
    current = (BASE / 'current').resolve(strict=True)
    live_app = current / 'whalegirl_linux/app.py'
    live_main = current / 'whalegirl_linux/__main__.py'
    dropin = Path('/etc/systemd/system/whalegirl-linux.service.d/90-process-stability.conf')
    watchdog_script = BASE / 'core_watchdog.py'
    watchdog_service = Path('/etc/systemd/system/whalegirl-core-watchdog.service')
    watchdog_timer = Path('/etc/systemd/system/whalegirl-core-watchdog.timer')
    backup = ASSETS / ('core-process-stability-before-' + STAMP)
    if backup.exists():
        raise SystemExit('Backup destination already exists; no changes made')
    backup.mkdir(mode=0o700, parents=True)
    paths = {
        'live/app.py': live_app,
        'live/__main__.py': live_main,
        'systemd/whalegirl-linux.service': Path('/etc/systemd/system/whalegirl-linux.service'),
        'systemd/90-process-stability.conf': dropin,
        'systemd/core-watchdog.service': watchdog_service,
        'systemd/core-watchdog.timer': watchdog_timer,
        'core_watchdog.py': watchdog_script,
    }
    save_backup(backup, paths)
    before = run(['systemctl', 'show', SERVICE, '--property=MemoryHigh',
                  '--property=MemoryMax', '--property=MemorySwapMax'])
    (backup / 'previous-resources.txt').write_text(before + '\n', encoding='utf-8')
    (backup / 'deployment-plan.json').write_text(json.dumps({
        'container': container_before, 'restart_scope': SERVICE,
        'wechat_container_restart': False, 'outbound_sends_in_progress': 0,
        'memory_high': '384M', 'memory_max': '512M', 'memory_swap_max': '512M',
        'watchdog_interval_seconds': 60, 'failure_threshold': 3,
        'restart_cooldown_seconds': 900,
    }, indent=2), encoding='utf-8')

    health_patch = replace_exact(live_app, OLD_HEALTH, NEW_HEALTH)
    uvicorn_patch = replace_exact(live_main, OLD_RUN, NEW_RUN)
    atomic_copy(files['deploy/core_watchdog.py'], watchdog_script, mode=0o755)
    atomic_copy(files['deploy/whalegirl-core-watchdog.service'], watchdog_service)
    atomic_copy(files['deploy/whalegirl-core-watchdog.timer'], watchdog_timer)
    atomic_text(dropin, '''[Service]
MemoryHigh=384M
MemoryMax=512M
MemorySwapMax=512M
Restart=on-failure
RestartSec=10s
TimeoutStopSec=25s
''')

    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'set-property', SERVICE, 'MemoryHigh=384M',
         'MemoryMax=512M', 'MemorySwapMax=512M'])
    run(['systemctl', 'enable', '--now', 'whalegirl-core-watchdog.timer'])
    run(['systemctl', 'restart', SERVICE], timeout=70)
    health = await_health()
    run(['systemctl', 'start', 'whalegirl-core-watchdog.service'], timeout=15)
    public_health = api_json('http://127.0.0.1:18092/health', timeout=6)
    core_state = api_json('http://127.0.0.1:8092/api/state',
                          (DATA / 'admin.token').read_text().strip(), timeout=6)
    auth = api_json('http://127.0.0.1:8092/api/status/auth',
                    (DATA / 'gateway.token').read_text().strip(), timeout=6)
    container_after = validate_container()
    if container_after != container_before:
        raise SystemExit('WeChat container identity or restart count changed unexpectedly')
    if public_health.get('status') != 'ok' or health.get('failed_background_tasks') != 0:
        raise SystemExit('Core health verification was incomplete; backup retained')
    result = {
        'backup': str(backup),
        'health': health,
        'public_entry_health': public_health.get('status'),
        'container': container_after,
        'wechat_login_status': auth.get('status'),
        'account_present': bool(auth.get('loggedInUser')),
        'bot_enabled': bool(core_state.get('enabled')),
        'paused': bool(core_state.get('paused_reason')),
        'health_patch': health_patch,
        'uvicorn_patch': uvicorn_patch,
        'watchdog_timer': run(['systemctl', 'is-active', 'whalegirl-core-watchdog.timer']),
        'resources': json.loads((Path('/var/lib/whalegirl-linux/stability') /
                                 'core-watchdog.json').read_text()).get('resources', {}),
    }
    (backup / 'deployment-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                                   encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

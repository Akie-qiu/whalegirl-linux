"""Deploy the scoped reply-pause repair with an atomic release and rollback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER = 'whalegirl-wechat-candidate-4118'
FILES = {
    'whalegirl_linux/safety.py', 'whalegirl_linux/recovery.py',
    'whalegirl_linux/runtime.py', 'whalegirl_linux/moments.py',
    'deploy/native_recovery.py', 'deploy/chat_select_linux.py',
    'tests/test_ui_recovery.py',
}
MOUNTED = '/opt/tools/chat-select.py'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def command(*args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0] + '; ' + result.stderr[-1600:])
    return result.stdout.strip()


def api(route):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request(
        'http://127.0.0.1:8092/api/' + route,
        headers={'Authorization': 'Bearer ' + token},
    )
    with OPENER.open(request, timeout=12) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        total = 0
        for table in ('outbound_actions', 'moments_actions', 'picture_jobs'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                total += db.execute(
                    "SELECT count(*) FROM " + table + " WHERE state IN ('sending','generating')"
                ).fetchone()[0]
        return total


def install(path, data):
    owner = path.stat() if path.exists() else path.parent.stat()
    pending = path.with_name(path.name + '.reply-pause-' + str(time.time_ns()))
    pending.write_bytes(data)
    os.chown(pending, owner.st_uid, owner.st_gid)
    pending.chmod(owner.st_mode & 0o777)
    os.replace(pending, path)


def switch(target, stamp):
    current = BASE / 'current'
    pending = BASE / ('current.reply-pause-scope-' + stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, current)


def main():
    archive_path, manifest_path = map(Path, sys.argv[1:3])
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    current = BASE / 'current'
    previous = current.resolve()
    if str(previous) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Live release or manifest does not match the reviewed baseline')

    def unchanged():
        if current.resolve() != previous:
            raise RuntimeError('Active release changed during deployment')
        for name, expected in manifest['before'].items():
            if digest(previous / name) != expected:
                raise RuntimeError('Production source changed: ' + name)

    unchanged()
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment archive')
        payload = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in payload.items():
        if hashlib.sha256(data).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload checksum mismatch: ' + name)
        if name.endswith('.py'):
            compile(data, name, 'exec')

    state_before = api('state')
    if state_before.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; deployment postponed')
    if state_before.get('processing') or inflight():
        raise RuntimeError('Core has an active operation; deployment postponed')
    free = shutil.disk_usage('/').free
    current_bytes = sum(path.stat().st_size for path in previous.rglob('*') if path.is_file())
    if free < current_bytes * 2 + 256 * 1024 * 1024:
        raise RuntimeError('Insufficient disk space for release and rollback snapshot')

    protected_names = ('settings.json', 'api-key.enc', 'image-api-key.enc', 'admin.token')
    protected = {name: digest(DATA / name) for name in protected_names if (DATA / name).is_file()}
    wechat_pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    container_id = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / ('reply-pause-scope-' + stamp)
    backup = ASSETS / ('reply-pause-scope-before-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(previous, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (previous, *previous.rglob('*')):
        target = release / source.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, data in payload.items():
        original = previous / name
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, saved)
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        owner = original.stat()
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
    native_backup = backup / 'native-tools'
    native_backup.mkdir()
    for name in ('chat_select_linux.py', 'native_recovery.py'):
        source = TOOLS / name
        shutil.copy2(source, native_backup / name)
    mounted_before = subprocess.check_output(
        ['docker', 'exec', CONTAINER, 'cat', MOUNTED], timeout=10
    )
    (backup / 'mounted-chat-select.py').write_bytes(mounted_before)
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
        with sqlite3.connect(backup / 'state-before.sqlite3') as saved:
            source.backup(saved)
    (backup / 'state-before.sqlite3').chmod(0o600)

    test = subprocess.run(
        [str(BASE / 'venv/bin/python'), '-m', 'unittest',
         'test_ui_recovery', 'test_moments', '-q'],
        cwd=release,
        env={**os.environ, 'PYTHONPATH': str(release) + ':' + str(release / 'tests'),
             'LOGURU_LEVEL': 'WARNING'},
        capture_output=True, text=True, timeout=120,
    )
    (backup / 'focused-tests.log').write_text(test.stdout + test.stderr)
    if test.returncode:
        print((test.stdout + test.stderr)[-5000:], flush=True)
        raise RuntimeError('Focused recovery tests failed; live service unchanged')

    stopped = switched = False
    try:
        unchanged()
        if any(digest(DATA / name) != value for name, value in protected.items()):
            raise RuntimeError('Configuration changed during staging')
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True, timeout=30)
        stopped = True
        if inflight():
            raise RuntimeError('An outbound operation crossed the shutdown boundary')
        for name in ('chat_select_linux.py', 'native_recovery.py'):
            install(TOOLS / name, payload['deploy/' + name])
        subprocess.run([
            'docker', 'exec', '-i', CONTAINER, 'python3', '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
            MOUNTED,
        ], input=payload['deploy/chat_select_linux.py'], text=False,
           capture_output=True, check=True, timeout=10)
        mounted_after = command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED).split()[0]
        if mounted_after != manifest['after']['deploy/chat_select_linux.py']:
            raise RuntimeError('Mounted chat selector checksum mismatch')
        switch(release, stamp)
        switched = True
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True, timeout=30)
        health = {}
        state_after = {}
        for _ in range(80):
            try:
                with OPENER.open('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state_after = api('state')
                recovery_status = state_after.get('recovery', {}).get('status')
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and state_after.get('gateway', {}).get('status') == 'logged_in'
                        and recovery_status not in ('pending', 'recovering')):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('New core did not become healthy with the existing login')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == wechat_pid,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container_id,
            'configuration_and_keys': all(digest(DATA / name) == value for name, value in protected.items()),
            'active_release': current.resolve() == release,
        }
        if not all(preserved.values()):
            raise RuntimeError('Protected process or configuration changed: ' + str(preserved))
        report = {
            'release': str(release), 'previous': str(previous), 'backup': str(backup),
            'health': health, 'wechat_status': state_after.get('gateway', {}).get('status'),
            'paused_reason': state_after.get('paused_reason'),
            'safety': state_after.get('safety'), 'preserved': preserved,
            'focused_tests_output': test.stdout + test.stderr, 'real_test_messages_sent': 0,
            'native_tools': str(TOOLS), 'at': time.time(),
        }
        (ASSETS / 'reply-pause-scope-deployment.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=30)
            switch(previous, stamp + '-rollback')
        for name in ('chat_select_linux.py', 'native_recovery.py'):
            install(TOOLS / name, (native_backup / name).read_bytes())
        subprocess.run([
            'docker', 'exec', '-i', CONTAINER, 'python3', '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
            MOUNTED,
        ], input=mounted_before, text=False, capture_output=True, check=True, timeout=10)
        if stopped:
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

"""Deploy reply-format and native sticker-location repairs without restarting WeChat."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
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
MOUNTED_SELECTOR = '/opt/tools/chat-select.py'
FILES = {
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/conversation.py',
    'whalegirl_linux/stickers.py',
    'deploy/chat_select_linux.py',
    'tests/test_chat_habits.py',
    'tests/test_native_delivery.py',
    'tests/test_sticker_studio.py',
}
NATIVE = 'chat_select_linux.py'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args, timeout=30):
    return subprocess.check_output(args, text=True, timeout=timeout).strip()


def api(route):
    token = (DATA/'admin.token').read_text().strip()
    request = urllib.request.Request(
        'http://127.0.0.1:8092/api/'+route,
        headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'"))


def atomic_write(path, content, mode=None):
    owner = path.stat()
    pending = path.with_name(path.name+'.log-repair-'+str(time.time_ns()))
    pending.write_bytes(content)
    os.chown(pending, owner.st_uid, owner.st_gid)
    pending.chmod(mode if mode is not None else owner.st_mode & 0o777)
    os.replace(pending, path)


def main():
    lock = open(ASSETS/'log-repair-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    old = (BASE/'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected release manifest')
    selector = TOOLS/NATIVE
    mounted_hash = command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED_SELECTOR).split()[0]
    native_before = digest(selector)
    if native_before != manifest['native_before'] or mounted_hash != native_before:
        raise RuntimeError('Native selector changed since inspection')
    for name, expected in manifest['before'].items():
        if digest(old/name) != expected:
            raise RuntimeError('Live source changed: '+name)

    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment payload')
        contents = {member.name: archive.extractfile(member).read() for member in members}
    for name, content in contents.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload checksum mismatch: '+name)
        if name.endswith('.py'):
            compile(content, name, 'exec')

    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/('log-repair-'+stamp)
    backup = ASSETS/('log-repair-before-'+stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(old, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for name, content in contents.items():
        target = release/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    group = pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (release, *release.rglob('*')):
        if not path.is_symlink():
            os.chown(path, 0, group)
            path.chmod(0o750 if path.is_dir() else 0o640)

    checks = []
    for pattern in ('test_chat_habits.py', 'test_native_delivery.py', 'test_sticker_studio.py'):
        result = subprocess.run(
            [str(BASE/'venv/bin/python'), '-m', 'unittest', 'discover',
             '-s', 'tests', '-p', pattern, '-q'],
            cwd=release, capture_output=True, text=True, timeout=45)
        (backup/(pattern+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError('Focused regression failed: '+pattern)
        checks.append(pattern)

    started = time.monotonic()
    while True:
        state = api('state')
        if not state.get('processing') and not inflight():
            break
        if time.monotonic()-started > 90:
            raise RuntimeError('Active send or worker did not become idle; deployment deferred')
        time.sleep(.5)
    if state.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; deployment deferred')
    if state.get('paused_reason') or state.get('counts', {}).get('uncertain'):
        raise RuntimeError('Resolve the paused/uncertain send before deployment')
    original_settings = digest(DATA/'settings.json')
    native_pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader_pid = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container_id = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    backup.mkdir(exist_ok=True)
    for name in FILES:
        target = backup/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old/name, target)
    shutil.copy2(selector, backup/NATIVE)
    native_bytes = selector.read_bytes()
    previous = old
    pending = BASE/('current.log-repair-'+stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    switched = stopped = False
    try:
        command('systemctl', 'stop', 'whalegirl-linux.service')
        stopped = True
        atomic_write(selector, contents['deploy/'+NATIVE])
        subprocess.run(
            ['docker', 'exec', '-i', CONTAINER, 'python3', '-c',
             'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
             MOUNTED_SELECTOR],
            input=contents['deploy/'+NATIVE], check=True, capture_output=True, timeout=10)
        if command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED_SELECTOR).split()[0] != manifest['after']['deploy/'+NATIVE]:
            raise RuntimeError('Mounted native selector checksum mismatch')
        pending.symlink_to(release, target_is_directory=True)
        os.replace(pending, BASE/'current')
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(60):
            try:
                health = urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2)
                health = json.load(health)
                state = api('state')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks') and state.get('gateway', {}).get('status') == 'logged_in':
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Updated core did not become healthy and logged in')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native_pid,
            'reader_pid': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader_pid,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container_id,
            'settings': digest(DATA/'settings.json') == original_settings,
            'native_selector': digest(selector) == manifest['after']['deploy/'+NATIVE],
            'mounted_selector': command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED_SELECTOR).split()[0] == manifest['after']['deploy/'+NATIVE],
        }
        if not all(preserved.values()):
            raise RuntimeError('A protected runtime boundary changed')
        report = {'release': str(release), 'previous': str(previous), 'backup': str(backup),
                  'health': health, 'wechat_status': state.get('gateway', {}).get('status'),
                  'preserved': preserved, 'checks': checks, 'real_messages_sent': 0,
                  'source_hashes': manifest['after']}
        (ASSETS/'log-repair-deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k:v for k,v in report.items() if k != 'source_hashes'}, ensure_ascii=False), flush=True)
    except BaseException:
        if stopped:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=30)
            atomic_write(selector, native_bytes)
            subprocess.run(
                ['docker', 'exec', '-i', CONTAINER, 'python3', '-c',
                 'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
                 MOUNTED_SELECTOR],
                input=native_bytes, check=False, capture_output=True, timeout=10)
            if switched:
                rollback = BASE/('current.log-repair-rollback-'+stamp)
                rollback.symlink_to(previous, target_is_directory=True)
                os.replace(rollback, BASE/'current')
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

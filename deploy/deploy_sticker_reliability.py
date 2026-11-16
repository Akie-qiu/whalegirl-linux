"""Guarded sticker picker deployment; backs up code only and never sends media."""
import hashlib
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
ASSETS = Path('/opt/whalegirl-linux-assets/sticker-reliability-20261112')
DATA = Path('/var/lib/whalegirl-linux/core')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER = 'whalegirl-wechat-candidate-4118'
MOUNTED = '/opt/tools/chat-select.py'
FILES = ('deploy/chat_select_linux.py', 'whalegirl_linux/delivery.py')
TEST_FILES = ('tests/test_native_delivery.py', 'tests/test_media_delivery.py',
              'tests/test_sticker_anchor_scroll.py')
ROOT = '/opt/whalegirl-linux/releases/pc-voice-20261111-183639'
BEFORE = {
    'deploy/chat_select_linux.py': '413721383e37e6b4a71b725868fcca37c4d842328adb5c3ca02a2efd0e1ca775',
    'whalegirl_linux/delivery.py': '5e70dc060952e3af5e40248bab550eb795b0d3f05eac92288e677b2dd320ed0c',
}
BEFORE.update({
    'tests/test_native_delivery.py': '3c5eb323ad36882cdde37b9e5c50485fc2f61066bbf517337130eb33cdf1f5a7',
    'tests/test_media_delivery.py': '72f09871faefbeb36e69f72fb3c975abbfd30b6688d6105fa0f6e6169682df4e',
    'tests/test_sticker_anchor_scroll.py': '04fd4fadb7f61031c2d46ae7ca53c4a00d6b426713166934af39c0cd72ec719b',
})


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def command(*args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0] + '; ' + result.stderr[-1200:])
    return result.stdout.strip()


def api(route):
    request = urllib.request.Request('http://127.0.0.1:8092/api/' + route,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def reader_pid():
    return command('systemctl', 'show', 'whalegirl-native-reader.service', '-p', 'MainPID', '--value')


def container_state():
    identity = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    return {'identity': identity, 'wechat_pid': pid}


def inflight():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        total = 0
        for table in ('outbound_actions', 'moments_actions', 'picture_jobs'):
            if table in tables:
                total += db.execute('SELECT count(*) FROM ' + table +
                    " WHERE state IN ('sending','generating')").fetchone()[0]
        return total


def protected_hashes():
    return {name: digest(DATA / name) for name in
            ('settings.json', 'persona-cards.json', 'admin.token', 'gateway.token')}


def source_snapshot(root):
    ignored = {'__pycache__', 'test-runs', '.git'}
    return {path.relative_to(root).as_posix(): digest(path) for path in root.rglob('*')
            if path.is_file() and not any(part in ignored for part in path.relative_to(root).parts)}


def replace(path, data):
    metadata = path.stat()
    temp = path.with_name(path.name + '.sticker-reliability-new')
    if temp.exists():
        raise RuntimeError('Unreviewed replacement file already exists: ' + str(temp))
    temp.write_bytes(data)
    temp.chmod(metadata.st_mode & 0o777)
    os.chown(temp, metadata.st_uid, metadata.st_gid)
    os.replace(temp, path)


def update_mounted_selector(data):
    """Update the mounted inode and immediately restore its read-only bind."""
    pid = command('docker', 'inspect', '--format', '{{.State.Pid}}', CONTAINER)
    if not pid.isdigit() or int(pid) <= 1:
        raise RuntimeError('Invalid running container identity')
    # A bind can still refer to an unlinked inode after an earlier atomic
    # replacement. Overmounting that dentry returns ENOENT. Keep its inode and
    # update it while the core is stopped, restoring read-only even on failure.
    namespace = ['nsenter', '-t', pid, '-m', '-p', '--root', '--wd', '--']
    try:
        command(*namespace, '/usr/bin/mount', '-n', '-o', 'remount,bind,rw', MOUNTED)
        result = subprocess.run(['docker', 'exec', '-u', '0', '-i', CONTAINER, 'python3', '-c',
            'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',
            MOUNTED], input=data, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError('Mounted selector update failed: ' + result.stderr.decode()[-400:])
    finally:
        command(*namespace, '/usr/bin/mount', '-n', '-o', 'remount,bind,ro', MOUNTED)
    options = command('nsenter', '-t', pid, '-m', '-p', '--root', '--wd', '--',
                      '/usr/bin/findmnt', '-n', '-T', MOUNTED, '-o', 'OPTIONS')
    if 'ro' not in options.split(','):
        raise RuntimeError('Native selector bind lost its read-only protection')


def switch(target, stamp):
    current = BASE / 'current'
    pending = BASE / ('current.sticker-reliability-' + stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, current)


def wait_idle(deadline):
    while time.monotonic() < deadline:
        state = api('state')
        if not state.get('processing') and not inflight():
            return state
        time.sleep(2)
    raise TimeoutError('Active work did not drain before deployment')


def install():
    if (ASSETS / 'manifest.json').exists():
        raise RuntimeError('Existing repair backup directory must be reviewed first')
    current = BASE / 'current'
    previous = current.resolve()
    if str(previous) != ROOT:
        raise RuntimeError('Active release changed; refusing to deploy')
    for name, expected in BEFORE.items():
        if digest(previous / name) != expected:
            raise RuntimeError('Live release changed: ' + name)
    mounted_before = subprocess.check_output(['docker', 'exec', CONTAINER, 'cat', MOUNTED], timeout=10)
    if hashlib.sha256(mounted_before).hexdigest() != BEFORE['deploy/chat_select_linux.py']:
        raise RuntimeError('Mounted native selector differs from reviewed live source')
    native_path = TOOLS / 'chat_select_linux.py'
    if digest(native_path) != BEFORE['deploy/chat_select_linux.py']:
        raise RuntimeError('Persistent native selector differs from reviewed live source')

    payload = {}
    for name in FILES + TEST_FILES:
        source = ASSETS / 'stage' / name
        if not source.is_file():
            raise RuntimeError('Staged file missing: ' + name)
        value = source.read_bytes()
        if name.endswith('.py'):
            compile(value, name, 'exec')
        payload[name] = value

    ASSETS.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ASSETS / 'before').mkdir(mode=0o700, exist_ok=True)
    (ASSETS / 'logs').mkdir(mode=0o700, exist_ok=True)
    before = {}
    for name in FILES + TEST_FILES:
        saved = ASSETS / 'before' / name
        saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source_hash = digest(previous / name)
        if saved.exists():
            if digest(saved) != source_hash:
                raise RuntimeError('Existing code backup differs; review it first: ' + name)
        else:
            shutil.copy2(previous / name, saved)
        before[name] = source_hash
    native_backup = ASSETS / 'before/native-tools-chat_select_linux.py'
    if native_backup.exists():
        if digest(native_backup) != digest(native_path):
            raise RuntimeError('Existing selector backup differs; review it first')
    else:
        shutil.copy2(native_path, native_backup)
    mounted_backup = ASSETS / 'before/mounted-chat-select.py'
    if mounted_backup.exists():
        if digest(mounted_backup) != hashlib.sha256(mounted_before).hexdigest():
            raise RuntimeError('Existing mounted selector backup differs; review it first')
    else:
        mounted_backup.write_bytes(mounted_before)
    manifest = {
        'previous': str(previous), 'before': before,
        'payload': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
        'native_before': hashlib.sha256(mounted_before).hexdigest(),
        'protected_before': protected_hashes(), 'reader_pid_before': reader_pid(),
        'container_before': container_state(), 'at': time.time(),
        'source_before': source_snapshot(previous),
    }

    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / ('sticker-reliability-' + stamp)
    shutil.copytree(previous, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (previous, *previous.rglob('*')):
        target = release / source.relative_to(previous)
        if target.exists() or target.is_symlink():
            metadata = source.lstat()
            os.chown(target, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
    for name, data in payload.items():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        original = previous / name
        target.write_bytes(data)
        metadata = original.stat()
        os.chown(target, metadata.st_uid, metadata.st_gid)
        target.chmod(metadata.st_mode & 0o777)

    service_user = command('systemctl', 'show', 'whalegirl-linux.service', '-p', 'User', '--value')
    if service_user and service_user != 'root':
        command('runuser', '-u', service_user, '--', '/usr/bin/test', '-x', str(release))
        command('runuser', '-u', service_user, '--', '/usr/bin/test', '-r', str(release / 'whalegirl_linux/delivery.py'))

    test = subprocess.run(
        [str(BASE / 'venv/bin/python'), '-m', 'unittest', 'test_native_delivery',
         'test_media_delivery', 'test_sticker_grid_safety', 'test_sticker_animated_grid',
         'test_sticker_subtree_delivery', 'test_sticker_anchor_scroll', '-q'],
        cwd=release, env={**os.environ, 'PYTHONPATH': str(release) + ':' + str(release / 'tests'),
                          'LOGURU_LEVEL': 'WARNING'},
        capture_output=True, text=True, timeout=180)
    test_log = test.stdout + test.stderr
    test_log_path = ASSETS / 'logs' / ('candidate-tests-' + release.name + '.log')
    test_log_path.write_text(test_log)
    if test.returncode:
        raise RuntimeError('Candidate tests failed; live release is unchanged: ' + test_log[-2500:])

    state = api('state')
    if state.get('gateway', {}).get('status') != 'logged_in' or state.get('paused_reason'):
        raise RuntimeError('WeChat is not in a deployable state')
    if shutil.disk_usage('/').free < 256 * 1024 * 1024:
        raise RuntimeError('Insufficient disk reserve for service activation')
    state = wait_idle(time.monotonic() + 120)
    if state.get('gateway', {}).get('status') != 'logged_in' or state.get('paused_reason'):
        raise RuntimeError('WeChat state changed before activation')
    if protected_hashes() != manifest['protected_before'] or reader_pid() != manifest['reader_pid_before']:
        raise RuntimeError('Protected settings or native reader changed during staging')
    if container_state() != manifest['container_before']:
        raise RuntimeError('WeChat process changed during staging')
    if source_snapshot(previous) != manifest['source_before']:
        raise RuntimeError('Concurrent source update detected; keep the newer live files')
    expected = {**manifest['source_before'], **manifest['payload']}
    if source_snapshot(release) != expected:
        raise RuntimeError('Candidate includes an unexpected source change')
    manifest.update(release=str(release), source_expected=expected)
    (ASSETS / ('prepared-' + release.name + '.json')).write_text(json.dumps(manifest, indent=2))

    active = False
    core_stopped = False
    try:
        command('systemctl', 'stop', 'whalegirl-linux.service', timeout=30)
        core_stopped = True
        if inflight():
            raise RuntimeError('A send began while the core was draining')
        native_data = payload['deploy/chat_select_linux.py']
        replace(native_path, native_data)
        update_mounted_selector(native_data)
        if digest(native_path) != manifest['payload']['deploy/chat_select_linux.py']:
            raise RuntimeError('Persistent native selector checksum mismatch')
        mounted_after = command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED).split()[0]
        if mounted_after != manifest['payload']['deploy/chat_select_linux.py']:
            raise RuntimeError('Mounted native selector checksum mismatch')
        switch(release, stamp)
        active = True
        command('systemctl', 'start', 'whalegirl-linux.service', timeout=30)
        core_stopped = False
        deadline = time.monotonic() + 45
        health = status = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                status = api('state')
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and status.get('gateway', {}).get('status') == 'logged_in'
                        and not status.get('paused_reason')):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Candidate release did not become healthy')
        if protected_hashes() != manifest['protected_before']:
            raise RuntimeError('Configuration changed during activation')
        if reader_pid() != manifest['reader_pid_before']:
            raise RuntimeError('Native reader changed during activation')
        if container_state() != manifest['container_before']:
            raise RuntimeError('WeChat process changed during activation')
        if digest(release / 'whalegirl_linux/delivery.py') != manifest['payload']['whalegirl_linux/delivery.py']:
            raise RuntimeError('Active release does not contain the reviewed delivery code')
        manifest.update(release=str(release), at_activated=time.time(), health=health,
            wechat=status.get('gateway', {}).get('status'),
            paused=bool(status.get('paused_reason')), reader_unchanged=True,
            wechat_unchanged=True, private_files_unchanged=True,
            native_selector_installed=True, database_schema_migrated=False,
            database_snapshot_created=False,
            test_log=str(test_log_path), real_test_messages_sent=0)
        (ASSETS / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        (ASSETS / 'manifest.json').chmod(0o600)
        print(json.dumps({'installed': True, 'release': str(release), 'health': health,
            'wechat': manifest['wechat'], 'reader_unchanged': True, 'wechat_unchanged': True,
            'private_files_unchanged': True, 'database_schema_migrated': False,
            'real_test_messages_sent': 0, 'tests': test_log[-500:]}, ensure_ascii=False))
    except BaseException as exc:
        try:
            if not core_stopped:
                subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=30)
            if active and current.resolve() == release:
                switch(previous, stamp + '-rollback')
            original_native = (ASSETS / 'before/native-tools-chat_select_linux.py').read_bytes()
            replace(native_path, original_native)
            update_mounted_selector(original_native)
        finally:
            if core_stopped or active:
                subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
            (ASSETS / ('failure-' + str(time.time_ns()) + '.json')).write_text(json.dumps({'at': time.time(),
                'error': str(exc)[:500], 'previous': str(previous), 'candidate': str(release)},
                ensure_ascii=False, indent=2))
        raise


def rollback():
    manifest = json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))
    current = BASE / 'current'
    release = Path(manifest['release'])
    if current.resolve() != release:
        raise RuntimeError('A newer release is active; refusing to roll it back')
    for name, expected in manifest['payload'].items():
        if digest(release / name) != expected:
            raise RuntimeError('Candidate changed; refusing rollback: ' + name)
    native_data = (ASSETS / 'before/native-tools-chat_select_linux.py').read_bytes()
    if hashlib.sha256(native_data).hexdigest() != manifest['native_before']:
        raise RuntimeError('Native selector backup checksum mismatch')
    wait_idle(time.monotonic() + 120)
    command('systemctl', 'stop', 'whalegirl-linux.service', timeout=30)
    try:
        replace(TOOLS / 'chat_select_linux.py', native_data)
        update_mounted_selector(native_data)
        switch(Path(manifest['previous']), 'manual-rollback-' + str(time.time_ns()))
    finally:
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
    result = {'at': time.time(), 'restored_release': manifest['previous'],
              'restored_native_selector': True, 'database_schema_migrated': False}
    (ASSETS / 'rollback.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in ('install', 'rollback'):
        raise SystemExit('usage: deploy_sticker_reliability.py install|rollback')
    globals()[sys.argv[1]]()

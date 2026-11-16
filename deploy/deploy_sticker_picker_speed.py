"""Deploy the verified sticker picker repair with a preserved code/data rollback."""
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
MOUNTED = '/opt/tools/chat-select.py'
CODE_FILES = {'whalegirl_linux/delivery.py', 'deploy/chat_select_linux.py'}
FILES = CODE_FILES | {'tests/test_native_favorite_mapping.py',
                      'tests/test_sticker_grid_safety.py', 'tests/test_sticker_misdelivery.py'}
TESTS = (
    'test_media_delivery', 'test_native_delivery', 'test_native_favorite_mapping',
    'test_sticker_grid_safety', 'test_sticker_misdelivery',
    'test_sticker_scroll_progress', 'test_sticker_subtree_delivery', 'test_ui_recovery',
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def release_snapshot(root):
    """Fingerprint every release file without following links or test artifacts."""
    result = {}
    excluded = {'__pycache__', '.git', 'test-runs', '.pytest_cache'}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in excluded)
        for name in [*names, *sorted(files)]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = 'symlink:' + os.readlink(path)
            elif path.is_file():
                with path.open('rb') as stream:
                    checksum = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(block)
                    result[relative] = checksum.hexdigest()
    return result


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
                    'SELECT count(*) FROM ' + table + " WHERE state IN ('sending','generating')"
                ).fetchone()[0]
        return total


def install(path, data, stamp):
    owner = path.stat() if path.exists() else path.parent.stat()
    pending = path.with_name(path.name + '.sticker-picker-' + stamp + '.pending')
    if pending.exists():
        raise RuntimeError('Pending file already exists')
    fd = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY, owner.st_mode & 0o777)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(pending, owner.st_uid, owner.st_gid)
    os.replace(pending, path)


def install_mounted(data, stamp):
    """Preserve bind-mount identity and restore bytes if an in-place write fails."""
    source = '''import os,pathlib,sys
path=pathlib.Path(sys.argv[1]); stamp=sys.argv[2]; data=sys.stdin.buffer.read()
compile(data,str(path),'exec')
owner=path.stat()
if os.path.ismount(path):
    before=path.read_bytes()
    try:
        with path.open('wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if path.read_bytes()!=data: raise RuntimeError('Mounted selector write verification failed')
    except BaseException:
        with path.open('wb') as stream:
            stream.write(before); stream.flush(); os.fsync(stream.fileno())
        raise
else:
    pending=path.with_name(path.name+'.sticker-picker-'+stamp+'.pending')
    with pending.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    pending.chmod(owner.st_mode & 0o777); os.chown(pending,owner.st_uid,owner.st_gid)
    os.replace(pending,path)
'''
    subprocess.run(['docker', 'exec', '-i', CONTAINER, 'python3', '-c', source, MOUNTED, stamp],
                   input=data, capture_output=True, check=True, timeout=10)


def switch(target, stamp):
    current = BASE / 'current'
    pending = BASE / ('current.sticker-picker-' + stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, current)


def main():
    archive_path, manifest_path = map(Path, sys.argv[1:3])
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if set(manifest.get('before', {})) != FILES or set(manifest.get('after', {})) != FILES:
        raise RuntimeError('Manifest file set is not the reviewed sticker-only set')
    current = BASE / 'current'
    previous = current.resolve()
    if str(previous) != manifest['root']:
        raise RuntimeError('The active production release changed after staging')
    baseline = manifest.get('source_snapshot')
    if not baseline or any(baseline.get(name) != sha for name, sha in manifest['before'].items()):
        raise RuntimeError('Missing complete production source baseline')
    expected_candidate = {**baseline, **manifest['after']}

    def unchanged():
        if current.resolve() != previous:
            raise RuntimeError('The active production release changed')
        if release_snapshot(previous) != baseline:
            raise RuntimeError('Production source changed; preserving concurrent updates')

    unchanged()
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if {member.name for member in members} != FILES or not all(member.isfile() for member in members):
            raise RuntimeError('Unexpected deployment archive contents')
        payload = {member.name: archive.extractfile(member).read() for member in members}
    for name, data in payload.items():
        if hashlib.sha256(data).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload checksum mismatch: ' + name)
        compile(data, name, 'exec')

    state_before = api('state')
    if state_before.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; deployment postponed')
    deadline = time.monotonic() + 120
    while state_before.get('processing') or inflight():
        if time.monotonic() >= deadline:
            raise RuntimeError('Active send/generation did not drain')
        time.sleep(2)
        state_before = api('state')

    current_bytes = sum((previous / name).stat().st_size for name, value in baseline.items()
                        if not value.startswith('symlink:'))
    database_bytes = (DATA / 'state.sqlite3').stat().st_size
    free = shutil.disk_usage('/').free
    minimum_free = current_bytes * 2 + database_bytes + 500 * 1024 * 1024
    if free < minimum_free:
        raise RuntimeError('Insufficient disk space for release and rollback snapshot')

    protected_names = (
        'settings.json', 'api-key.enc', 'image-api-key.enc', 'admin.token',
        'model-profiles.json', 'model-api-keys.enc', 'persona-cards.json',
    )
    protected = {name: digest(DATA / name) for name in protected_names if (DATA / name).is_file()}
    wechat_pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    container_identity = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / ('sticker-picker-speed-' + stamp)
    backup = ASSETS / ('sticker-picker-speed-before-' + stamp)
    report_path = ASSETS / ('sticker-picker-speed-' + stamp + '-deployment.json')
    backup.mkdir(mode=0o700)
    shutil.copytree(previous, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git', '.pytest_cache'))
    for source in (previous, *previous.rglob('*')):
        target = release / source.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, data in payload.items():
        source = previous / name
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, saved)
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        owner = source.stat()
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)

    native_backup = backup / 'native-tools'
    native_backup.mkdir()
    native_source = TOOLS / 'chat_select_linux.py'
    if digest(native_source) != manifest['before']['deploy/chat_select_linux.py']:
        raise RuntimeError('Native selector differs from reviewed production source')
    shutil.copy2(native_source, native_backup / native_source.name)
    mounted_before = subprocess.check_output(['docker', 'exec', CONTAINER, 'cat', MOUNTED], timeout=10)
    if hashlib.sha256(mounted_before).hexdigest() != manifest['before']['deploy/chat_select_linux.py']:
        raise RuntimeError('Mounted selector differs from reviewed production source')
    (backup / 'mounted-chat-select.py').write_bytes(mounted_before)
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
        with sqlite3.connect(backup / 'state-before.sqlite3') as saved:
            source.backup(saved)
    (backup / 'state-before.sqlite3').chmod(0o600)
    with sqlite3.connect('file:' + str(backup / 'state-before.sqlite3') + '?mode=ro', uri=True) as saved:
        if saved.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Database rollback snapshot failed integrity check')

    (release / 'test-runs').mkdir(mode=0o700, exist_ok=True)
    test = subprocess.run(
        [str(BASE / 'venv/bin/python'), '-m', 'unittest', *TESTS, '-q'],
        cwd=release,
        env={**os.environ, 'PYTHONPATH': str(release) + ':' + str(release / 'tests'),
             'LOGURU_LEVEL': 'WARNING', 'WHALEGIRL_DATA_DIR': str(release / 'test-runs'),
             'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=240,
    )
    (backup / 'focused-tests.log').write_text(test.stdout + test.stderr)
    if test.returncode:
        print((test.stdout + test.stderr)[-7000:], flush=True)
        raise RuntimeError('Focused candidate tests failed; live service unchanged')
    if release_snapshot(release) != expected_candidate:
        raise RuntimeError('Candidate contains changes outside the reviewed patch')

    stopped = switched = native_installed = mounted_installed = False
    try:
        unchanged()
        if any(digest(DATA / name) != value for name, value in protected.items()):
            raise RuntimeError('Protected configuration changed during staging')
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True, timeout=30)
        stopped = True
        if inflight():
            raise RuntimeError('An outbound operation crossed the shutdown boundary')
        unchanged()
        if (any(digest(DATA / name) != value for name, value in protected.items())
                or digest(native_source) != manifest['before']['deploy/chat_select_linux.py']
                or command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED).split()[0]
                != manifest['before']['deploy/chat_select_linux.py']):
            raise RuntimeError('Configuration or native selector changed; preserving concurrent updates')
        install(native_source, payload['deploy/chat_select_linux.py'], stamp)
        native_installed = True
        install_mounted(payload['deploy/chat_select_linux.py'], stamp)
        mounted_installed = True
        mounted_after = command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED).split()[0]
        if mounted_after != manifest['after']['deploy/chat_select_linux.py']:
            raise RuntimeError('Mounted selector checksum mismatch')
        unchanged()
        switch(release, stamp)
        switched = True
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True, timeout=30)
        health = state_after = {}
        for _ in range(80):
            try:
                with OPENER.open('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state_after = api('state')
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and state_after.get('gateway', {}).get('status') == 'logged_in'
                        and not state_after.get('paused_reason')):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('New core did not become healthy with the existing login')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == wechat_pid,
            'container_identity': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}',
                                          CONTAINER) == container_identity,
            'protected_configuration': all(digest(DATA / name) == value for name, value in protected.items()),
            'active_release': current.resolve() == release,
            'complete_source_snapshot': release_snapshot(release) == expected_candidate,
            'previous_release_unchanged': release_snapshot(previous) == baseline,
        }
        if not all(preserved.values()):
            raise RuntimeError('Protected runtime state changed: ' + str(preserved))
        report = {
            'release': str(release), 'previous': str(previous), 'backup': str(backup),
            'health': health, 'wechat_status': state_after.get('gateway', {}).get('status'),
            'paused_reason': state_after.get('paused_reason'), 'preserved': preserved,
            'schema_changes': False, 'data_restore_performed': False, 'real_test_messages_sent': 0,
            'focused_tests': TESTS, 'candidate_test_output': test.stdout + test.stderr,
            'minimum_free_bytes': minimum_free, 'disk_free_before_bytes': free,
            'disk_free_after_bytes': shutil.disk_usage('/').free,
            'preserved_source_files': len(baseline), 'changed_files': sorted(FILES),
            'at': time.time(),
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except BaseException:
        # Never roll back another deployer who has changed the active release.
        owns_active = current.resolve() == release
        if switched and owns_active and release_snapshot(release) == expected_candidate:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=30)
            switch(previous, stamp + '-rollback')
        restore_own_files = current.resolve() == previous
        if (native_installed and restore_own_files
                and digest(native_source) == manifest['after']['deploy/chat_select_linux.py']):
            install(native_source, (native_backup / 'chat_select_linux.py').read_bytes(), stamp + '-rollback')
        if mounted_installed and restore_own_files:
            live_mounted = command('docker', 'exec', CONTAINER, 'sha256sum', MOUNTED).split()[0]
            if live_mounted == manifest['after']['deploy/chat_select_linux.py']:
                install_mounted(mounted_before, stamp + '-rollback')
        if stopped:
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    import fcntl

    with (ASSETS / 'sticker-picker-speed-deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main()

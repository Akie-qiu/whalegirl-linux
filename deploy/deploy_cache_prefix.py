"""Publish prompt-prefix ordering improvements without restarting native WeChat."""
import fcntl
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
DATA = Path('/var/lib/whalegirl-linux/core')
ASSETS = Path('/opt/whalegirl-linux-assets')
CONTAINER = 'whalegirl-wechat-candidate-4118'
FILES = {
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/affection.py',
    'whalegirl_linux/image_tools.py',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def api(route):
    request = urllib.request.Request(
        'http://127.0.0.1:8092/api/' + route,
        headers={'Authorization': 'Bearer ' + (DATA/'admin.token').read_text().strip()},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:' + str(DATA/'state.sqlite3') + '?mode=ro', uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
        ))


def switch_release(target, stamp, suffix=''):
    pending = BASE/('current.cache-prefix-' + stamp + suffix)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link already exists')
    pending.symlink_to(target, target_is_directory=True)
    os.replace(pending, BASE/'current')


def main():
    lock = open(ASSETS/'cache-prefix-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    old = (BASE/'current').resolve()
    if str(old) != manifest.get('root') or set(manifest.get('before', {})) != FILES or set(manifest.get('after', {})) != FILES:
        raise RuntimeError('Unexpected active release or manifest')

    def unchanged():
        if (BASE/'current').resolve() != old:
            raise RuntimeError('Active release changed during deployment')
        for name, expected in manifest['before'].items():
            if digest(old/name) != expected:
                raise RuntimeError('Production source changed: ' + name)

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if (len(members) != len(FILES) or {member.name for member in members} != FILES
                or not all(member.isfile() for member in members)):
            raise RuntimeError('Unexpected deployment archive')
        contents = {member.name: archive.extractfile(member).read() for member in members}
    for name, source in contents.items():
        if hashlib.sha256(source).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Deployment checksum mismatch: ' + name)
        if name.endswith('.py'):
            compile(source, name, 'exec')

    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/('cache-prefix-' + stamp)
    shutil.copytree(old, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release/source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, source in contents.items():
        target = release/name
        owner = (old/name).stat()
        target.write_bytes(source)
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(target))

    resource_args = (
        'systemctl', 'show', 'whalegirl-linux.service',
        '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax',
    )
    resources = command(*resource_args)
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    protected = {
        name: digest(DATA/name) for name in ('settings.json', 'api-key.enc', 'image-api-key.enc')
        if (DATA/name).is_file()
    }
    before_state = api('state')
    if before_state.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; deployment stopped')
    if before_state.get('paused_reason') or before_state.get('counts', {}).get('uncertain', 0):
        raise RuntimeError('Chat has a pause or uncertain send; resolve it before deployment')

    deadline = time.monotonic() + 120
    while True:
        state, images = api('state'), api('images')
        if (not state.get('processing') and not images.get('busy') and not inflight()):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Core is busy; deployment deferred without stopping services')
        time.sleep(2)
    unchanged()

    backup = ASSETS/('cache-prefix-before-' + stamp)
    backup.mkdir(mode=0o700)
    for name in FILES:
        target = backup/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(old/name, target)
        target.chmod(0o600)
    shutil.copy2(DATA/'settings.json', backup/'settings.json')
    (backup/'settings.json').chmod(0o600)

    command('systemctl', 'stop', 'whalegirl-linux.service')
    switched = False
    try:
        if inflight():
            raise RuntimeError('An outbound task started during shutdown')
        unchanged()
        if any(digest(DATA/name) != expected for name, expected in protected.items()):
            raise RuntimeError('Production configuration changed during staging')
        switch_release(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(60):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state = api('state')
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and state.get('gateway', {}).get('status') == 'logged_in'):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Updated core did not become healthy and logged in')

        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native,
            'native_reader_pid': command(
                'systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value'
            ) == reader,
            'container_and_restart_count': command(
                'docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER
            ) == container,
            'resource_limits': command(*resource_args) == resources,
            'configuration_and_keys': all(digest(DATA/name) == expected for name, expected in protected.items()),
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment changed a protected runtime boundary: ' + str(preserved))
        report = {
            'release': str(release), 'previous': str(old), 'backup': str(backup),
            'at': time.time(), 'health': health, 'preserved': preserved,
            'wechat_status': state.get('gateway', {}).get('status'),
            'enabled': state.get('enabled'), 'processing': state.get('processing'),
            'real_messages_sent': 0, 'database_changed': False,
            'files': manifest['after'],
        }
        (ASSETS/'cache-prefix-deployment.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps({key: value for key, value in report.items() if key != 'files'},
                         ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'],
                           check=False, timeout=40)
            switch_release(old, stamp, '-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'],
                       check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

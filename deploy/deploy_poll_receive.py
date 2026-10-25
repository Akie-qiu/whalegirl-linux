"""Publish the receive-path patch; keep native WeChat and reader running."""
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
FILES = {'whalegirl_linux/runtime.py', 'whalegirl_linux/store.py'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True, timeout=25).strip()


def api(route):
    token = (DATA/'admin.token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
                                 headers={'Authorization': 'Bearer '+token})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)


def inflight():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
        return sum(db.execute(sql).fetchone()[0] for sql in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'"))


def link_release(release, stamp):
    pending = BASE/('current.poll-receive-'+stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending path exists; preserved for inspection')
    pending.symlink_to(release, target_is_directory=True)
    os.replace(pending, BASE/'current')


def main():
    import fcntl
    lock = open(ASSETS/'poll-receive-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    old = (BASE/'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if (BASE/'current').resolve() != old:
            raise RuntimeError('Current release changed')
        for name, expected in manifest['before'].items():
            if digest(old/name) != expected:
                raise RuntimeError('Production source changed: '+name)

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive members')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, source in contents.items():
        if hashlib.sha256(source).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload checksum mismatch')
        compile(source, name, 'exec')

    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/('poll-receive-'+stamp)
    shutil.copytree(old, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release/source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, source in contents.items():
        (release/name).write_bytes(source)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(release/name))
    resource_args = ('systemctl', 'show', 'whalegirl-linux.service',
                     '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax')
    resources = command(*resource_args)
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight():
        if time.monotonic()-started > 120:
            raise RuntimeError('Core busy; release staged, deployment deferred')
        time.sleep(2)
    unchanged()
    backup = ASSETS/('poll-receive-before-'+stamp)
    backup.mkdir(mode=0o700)
    command('systemctl', 'stop', 'whalegirl-linux.service')
    switched = False
    try:
        if inflight():
            raise RuntimeError('Send crossed shutdown; release switch cancelled')
        settings_hash = digest(DATA/'settings.json')
        shutil.copy2(DATA/'settings.json', backup/'settings.json')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as source:
            with sqlite3.connect(backup/'state-before.sqlite3') as target:
                source.backup(target)
        (backup/'state-before.sqlite3').chmod(0o600)
        link_release(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state = api('state')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks'):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('New core did not become healthy')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container,
            'native_reader': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader,
            'resource_limits': command(*resource_args) == resources,
            'settings': digest(DATA/'settings.json') == settings_hash,
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment boundary changed: '+str(preserved))
        report = dict(release=str(release), previous=str(old), backup=str(backup),
            health=health, preserved=preserved, wechat_status=state.get('gateway', {}).get('status'),
            hashes=manifest['after'])
        (ASSETS/'poll-receive-deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'hashes'}, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link_release(old, stamp+'-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

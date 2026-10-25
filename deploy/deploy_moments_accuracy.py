"""Publish comment context and targeting fixes without restarting native WeChat."""
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

from deploy_poll_receive import ASSETS, BASE, DATA, CONTAINER, api, command, digest, inflight

FILES = {'whalegirl_linux/moments.py', 'deploy/moments_ui.py'}
NATIVE = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/moments_ui.py')


def install(path, source):
    owner = path.stat()
    pending = path.with_name(path.name+'.accuracy-'+str(time.time_ns()))
    pending.write_bytes(source)
    os.chown(pending, owner.st_uid, owner.st_gid)
    pending.chmod(owner.st_mode & 0o777)
    os.replace(pending, path)


def link_release(release, stamp):
    pending = BASE/('current.moments-accuracy-'+stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending path exists; preserved')
    pending.symlink_to(release, target_is_directory=True)
    os.replace(pending, BASE/'current')


def generating_comment():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
        return bool(db.execute("SELECT 1 FROM moments_actions WHERE state='generating' LIMIT 1").fetchone())


def main():
    lock = open(ASSETS/'moments-accuracy-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    old = (BASE/'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected manifest')

    def unchanged():
        if (BASE/'current').resolve() != old or digest(NATIVE) != manifest['native_before']:
            raise RuntimeError('Deployment baseline changed')
        for name, expected in manifest['before'].items():
            if digest(old/name) != expected:
                raise RuntimeError('Source changed: '+name)

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected payload')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, source in contents.items():
        if hashlib.sha256(source).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload hash mismatch')
        compile(source, name, 'exec')

    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/('moments-accuracy-'+stamp)
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

    native_pid = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    resources = command('systemctl', 'show', 'whalegirl-linux.service',
                        '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax')
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight() or generating_comment():
        if time.monotonic()-started > 120:
            raise RuntimeError('Core busy; staged release preserved, deployment deferred')
        time.sleep(2)
    unchanged()
    backup = ASSETS/('moments-accuracy-before-'+stamp)
    backup.mkdir(mode=0o700)
    # No schema changes: preserve only changed source files, not another full DB copy.
    for name in FILES:
        saved = backup/name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old/name, saved)
    shutil.copy2(NATIVE, backup/'native-moments_ui.py')
    original_native = NATIVE.read_bytes()
    settings_hash = digest(DATA/'settings.json')
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True, timeout=40)
    switched = False
    native_changed = False
    try:
        if inflight() or generating_comment():
            raise RuntimeError('Work crossed shutdown; release switch cancelled')
        unchanged()
        install(NATIVE, contents['deploy/moments_ui.py'])
        native_changed = True
        link_release(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state = api('state')
                if (health.get('status') == 'ok' and not health.get('failed_background_tasks')
                        and state.get('gateway', {}).get('status') != 'disconnected'):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('Core did not become ready')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native_pid,
            'native_reader': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container,
            'settings': digest(DATA/'settings.json') == settings_hash,
            'resource_limits': command('systemctl', 'show', 'whalegirl-linux.service',
                '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax') == resources,
        }
        if not all(preserved.values()) or digest(NATIVE) != manifest['after']['deploy/moments_ui.py']:
            raise RuntimeError('Deployment boundary changed')
        report = dict(release=str(release), previous=str(old), backup=str(backup),
            health=health, wechat_status=state.get('gateway', {}).get('status'),
            preserved=preserved, hashes=manifest['after'],
            backup_bytes=sum(p.stat().st_size for p in backup.rglob('*') if p.is_file()))
        (ASSETS/'moments-accuracy-deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'hashes'}, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link_release(old, stamp+'-rollback')
        if native_changed:
            install(NATIVE, original_native)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

"""Deploy the protocol adapter; preserve configuration and native WeChat."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

from deploy_poll_receive import ASSETS, BASE, DATA, CONTAINER, api, command, digest, inflight

FILES = {'whalegirl_linux/'+name for name in (
    'upstream/ai_engine.py', 'settings.py', 'runtime.py', 'images.py', 'moments.py',
    'tieba.py', 'proactive.py', 'app.py', 'console.js', 'web.html')}
KIND = 'responses'
REQUIRED_SETTINGS = ('api_style',)


def main():
    lock = open(ASSETS/(KIND+'-deploy.lock'), 'a')
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

    def link_release(target, suffix):
        pending = BASE/('current.'+KIND+'-'+suffix)
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('Pending link exists')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, BASE/'current')

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or len(members) != len(FILES) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected payload')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, content in contents.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload hash mismatch')
        if name.endswith('.py'):
            compile(content, name, 'exec')
    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/(KIND+'-'+stamp)
    shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release/source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, content in contents.items():
        target = release/name
        owner = (old/name).stat()
        target.write_bytes(content)
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(target))
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    resources_args = ('systemctl', 'show', 'whalegirl-linux.service', '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax')
    resources = command(*resources_args)
    protected = {name: digest(DATA/name) for name in ('settings.json', 'api-key.enc', 'image-api-key.enc') if (DATA/name).exists()}
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight():
        if time.monotonic()-started > 120:
            raise RuntimeError('Core busy; staged release preserved')
        time.sleep(2)
    unchanged()
    backup = ASSETS/(KIND+'-before-'+stamp)
    backup.mkdir(mode=0o700)
    for name in FILES:
        target = backup/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old/name, target)
    command('systemctl', 'stop', 'whalegirl-linux.service')
    switched = False
    try:
        if inflight():
            raise RuntimeError('Work crossed shutdown; deployment deferred')
        unchanged()
        if any(digest(DATA/name) != expected for name, expected in protected.items()):
            raise RuntimeError('Configuration changed during deployment')
        link_release(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state, settings = api('state'), api('config')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks') and all(k in settings for k in REQUIRED_SETTINGS):
                    break
            except Exception:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('New core did not become ready')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native,
            'reader_pid': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container,
            'configuration_and_keys': all(digest(DATA/name) == expected for name, expected in protected.items()),
            'limits': command(*resources_args) == resources,
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment boundary changed')
        report = dict(release=str(release), previous=str(old), backup=str(backup),
            health=health, preserved=preserved, wechat_status=state.get('gateway', {}).get('status'),
            api_style=settings['api_style'], source_files=len(FILES),
            backup_bytes=sum(p.stat().st_size for p in backup.rglob('*') if p.is_file()))
        (ASSETS/(KIND+'-deployment.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link_release(old, stamp+'-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

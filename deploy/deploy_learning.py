"""Publish conversational learning while keeping native WeChat and its reader online."""
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

from deploy_poll_receive import ASSETS, BASE, CONTAINER, DATA, api, command, digest, inflight

FILES = {'whalegirl_linux/'+name for name in (
    'settings.py', 'runtime.py', 'learning.py', 'tieba_tools.py',
    'app.py', 'console.js', 'learning.js', 'web.html')}


def main():
    lock = open(ASSETS/'learning-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    old = (BASE/'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if (BASE/'current').resolve() != old:
            raise RuntimeError('Current release changed')
        for name, expected in manifest['before'].items():
            if (digest(old/name) if (old/name).is_file() else None) != expected:
                raise RuntimeError('Production source changed: '+name)

    def link(target, suffix):
        pending = BASE/('current.learning-'+suffix)
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('Pending link exists')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, BASE/'current')

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or len(members) != len(FILES) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive members')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, content in contents.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload hash mismatch')
        if name.endswith('.py'):
            compile(content, name, 'exec')
    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns() % 1_000_000_000)
    release = BASE/'releases'/('learning-'+stamp)
    shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release/source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    reference = (old/'whalegirl_linux/runtime.py').stat()
    for name, content in contents.items():
        target = release/name
        owner = (old/name).stat() if (old/name).exists() else reference
        target.write_bytes(content)
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(target))
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    protected = {name: digest(DATA/name) for name in ('settings.json', 'api-key.enc', 'image-api-key.enc') if (DATA/name).exists()}
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight():
        if time.monotonic()-started > 110:
            raise RuntimeError('Core busy; staged release preserved')
        time.sleep(2)
    unchanged()
    backup = ASSETS/('learning-before-'+stamp)
    backup.mkdir(mode=0o700)
    for name in FILES:
        if (old/name).is_file():
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
        # This deployment only adds new tables; all existing state remains in place.
        link(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state, settings, learning = api('state'), api('config'), api('learning')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks') and 'queued' in learning:
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
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment boundary changed')
        report = dict(release=str(release), previous=str(old), backup=str(backup), health=health,
                      preserved=preserved, wechat_status=state.get('gateway', {}).get('status'),
                      settings={k: settings[k] for k in (
                          'learning_enabled', 'learning_eagerness', 'learning_auto_accept',
                          'learning_default_ttl_days', 'learning_recall_limit')},
                      queue=learning['queued'], source_files=len(FILES), hashes=manifest['after'])
        (ASSETS/'learning-deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'hashes'}, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link(old, stamp+'-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

"""Persist the native notification fix without restarting the core or WeChat."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import urllib.request

from deploy_poll_receive import ASSETS, BASE, DATA, CONTAINER, api, command, digest, inflight

NAME = 'deploy/moments_ui.py'
NATIVE = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/moments_ui.py')


def main():
    lock = open(ASSETS/'moments-navigation-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    old = (BASE/'current').resolve()
    source = Path(sys.argv[1]).read_bytes()
    if digest(Path(sys.argv[1])) != manifest['after']:
        raise RuntimeError('Unexpected helper payload')
    compile(source, NAME, 'exec')
    def unchanged():
        if str(old) != manifest['root'] or (BASE/'current').resolve() != old:
            raise RuntimeError('Current release changed')
        if digest(old/NAME) != manifest['before'] or digest(NATIVE) != manifest['native_before']:
            raise RuntimeError('Native helper baseline changed')
    unchanged()
    started = time.monotonic()
    while api('state').get('processing') or inflight():
        if time.monotonic()-started > 90:
            raise RuntimeError('Send in progress; deployment deferred')
        time.sleep(1)
    stamp = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000)
    release = BASE/'releases'/('moments-navigation-'+stamp)
    shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__','test-runs','.git'))
    for path in (old,*old.rglob('*')):
        copied = release/path.relative_to(old)
        if copied.exists() or copied.is_symlink():
            owner = path.lstat()
            os.chown(copied, owner.st_uid, owner.st_gid, follow_symlinks=False)
    (release/NAME).write_bytes(source)
    command('runuser','-u','whalegirl-ai','--','test','-r',str(release/NAME))
    backup = ASSETS/('moments-navigation-publish-before-'+stamp)
    backup.mkdir(mode=0o700)
    shutil.copy2(old/NAME,backup/'release-moments_ui.py')
    shutil.copy2(NATIVE,backup/'native-moments_ui.py')
    settings_hash = digest(DATA/'settings.json')
    core = command('systemctl','show','whalegirl-linux','-p','MainPID','--value')
    reader = command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    native = command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    container = command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)
    unchanged()
    if NATIVE.read_bytes() != source:
        owner = NATIVE.stat()
        pending = NATIVE.with_name(NATIVE.name+'.navigation-'+stamp)
        pending.write_bytes(source)
        os.chown(pending, owner.st_uid, owner.st_gid)
        pending.chmod(owner.st_mode & 0o777)
        os.replace(pending,NATIVE)
    # Only a child-process helper changed. All imported core modules stay identical.
    pending = BASE/('current.moments-navigation-'+stamp)
    if pending.exists() or pending.is_symlink():
        raise RuntimeError('Pending release link exists')
    pending.symlink_to(release,target_is_directory=True)
    os.replace(pending,BASE/'current')
    with urllib.request.urlopen('http://127.0.0.1:8092/health',timeout=3) as response:
        health = json.load(response)
    state = api('state')
    preserved = {
        'core_pid': command('systemctl','show','whalegirl-linux','-p','MainPID','--value') == core,
        'reader_pid': command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value') == reader,
        'wechat_pid': command('docker','exec',CONTAINER,'pgrep','-x','wechat') == native,
        'container': command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER) == container,
        'settings': digest(DATA/'settings.json') == settings_hash,
    }
    if not all(preserved.values()) or digest(NATIVE) != manifest['after'] or health.get('status') != 'ok':
        raise RuntimeError('Deployment validation failed; published files preserved for inspection')
    report = dict(release=str(release),previous=str(old),backup=str(backup),health=health,
        wechat_status=state.get('gateway',{}).get('status'),preserved=preserved)
    (ASSETS/'moments-navigation-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()

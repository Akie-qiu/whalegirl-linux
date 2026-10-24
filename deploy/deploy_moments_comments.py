"""Deploy comment polling fixes without restarting native WeChat or its reader."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
FILES = {'whalegirl_linux/moments.py', 'whalegirl_linux/moments.js',
         'tests/test_moments.py', 'MOMENTS.md', 'deploy/deploy_moments_comments.py',
         'deploy/moments_ui.py', 'deploy/moments_layout.py'}
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
token = (DATA/'admin.token').read_text().strip()


def api(route):
    request = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
                                    headers={'Authorization': 'Bearer '+token})
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=35).stdout.strip()


def deploy():
    stamp = time.strftime('%Y%m%d-%H%M%S')
    previous = (BASE/'current').resolve()
    stage = BASE/'releases'/('moments-comments-'+stamp)
    backup = ASSETS/('moments-comments-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'moments-comments-20261022.tar') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected comment patch payload')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    shutil.copytree(previous, stage, symlinks=True)
    group = pwd.getpwnam('whalegirl-ai').pw_gid
    for name, data in contents.items():
        target = stage/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for path in (stage, *stage.rglob('*')):
        if not path.is_symlink():
            os.chown(path, 0, group)
            path.chmod(0o750 if path.is_dir() else 0o640)
    until = time.monotonic()+45
    while True:
        state, moments = api('state'), api('moments')
        if not state.get('processing') and not any(
                row['state'] in ('generating', 'sending') for row in moments['actions']):
            break
        if time.monotonic() >= until:
            raise RuntimeError('Chat or Moments busy; live release unchanged')
        time.sleep(.5)
    original = api('config')
    original.pop('api_key_configured', None)
    settings_hash = hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()
    native_pid = command('docker', 'exec', 'whalegirl-wechat-candidate-4118', 'pgrep', '-x', 'wechat')
    reader_pid = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    shutil.copy2(DATA/'settings.json', backup/'settings.json')
    originals = {name: (TOOLS/name).read_bytes() if (TOOLS/name).exists() else None
                 for name in ('moments_layout.py', 'moments_ui.py')}
    for name, data in originals.items():
        if data is not None:
            (backup/name).write_bytes(data)
    stopped = False
    try:
        if (BASE/'current').resolve() != previous:
            raise RuntimeError('Another deployment changed current release')
        command('systemctl', 'stop', 'whalegirl-linux')
        stopped = True
        for name in ('moments_layout.py', 'moments_ui.py'):
            target = TOOLS/name
            owner = (target if target.exists() else TOOLS/'moments_ui.py').stat()
            pending = TOOLS/(name+'.comments-'+stamp)
            pending.write_bytes(contents['deploy/'+name])
            os.chown(pending, owner.st_uid, owner.st_gid)
            pending.chmod(owner.st_mode & 0o777)
            os.replace(pending, target)
        pending = BASE/('current.moments-comments-'+stamp)
        pending.symlink_to(stage, target_is_directory=True)
        os.replace(pending, BASE/'current')
        command('systemctl', 'start', 'whalegirl-linux')
        for _ in range(70):
            try:
                state = api('state')
                if state.get('gateway', {}).get('status') == 'logged_in':
                    break
            except (OSError, ValueError):
                pass
            time.sleep(.3)
        else:
            raise RuntimeError('Core not ready after comment patch')
        live = api('config')
        if any(live.get(k) != v for k, v in original.items()):
            raise RuntimeError('An unrelated setting changed')
        if hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest() != settings_hash:
            raise RuntimeError('Settings file changed during deployment')
        if command('docker', 'exec', 'whalegirl-wechat-candidate-4118', 'pgrep', '-x', 'wechat') != native_pid:
            raise RuntimeError('Native WeChat PID changed')
        if command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') != reader_pid:
            raise RuntimeError('Native reader PID changed')
        result = {'release': str(stage), 'previous': str(previous), 'backup': str(backup),
                  'at': time.time(), 'wechat': state['gateway']['status'],
                  'enabled': state['enabled'], 'settings_preserved': True,
                  'native_pid_preserved': True, 'reader_pid_preserved': True,
                  'comment_poll_seconds': api('moments')['comment_poll_seconds']}
        (ASSETS/'moments-comments-deployment.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    except BaseException:
        if not stopped:
            raise
        command('systemctl', 'stop', 'whalegirl-linux')
        for name, data in originals.items():
            if data is not None:
                (TOOLS/name).write_bytes(data)
        pending = BASE/('current.moments-comments-rollback-'+stamp)
        pending.symlink_to(previous, target_is_directory=True)
        os.replace(pending, BASE/'current')
        command('systemctl', 'start', 'whalegirl-linux')
        raise


if __name__ == '__main__':
    deploy()

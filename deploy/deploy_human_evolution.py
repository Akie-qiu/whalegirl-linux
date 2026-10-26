"""Publish human-like persona, associative memory, relationship profiles, and self-evolution while keeping native WeChat online."""
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

FILES = {'whalegirl_linux/' + name for name in (
    'settings.py', 'memory.py', 'affection.py', 'conversation.py',
    'learning.py', 'runtime.py', 'app.py',
    'console.css', 'console.js',
    'learning.js', 'affection.js', 'improvements.js', 'habits.js', 'web.html',
)}
DEPLOYMENT = 'human-evolution'


def main():
    lock = open(ASSETS / (DEPLOYMENT + '-deploy.lock'), 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    old = (BASE / 'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if (BASE / 'current').resolve() != old:
            raise RuntimeError('Current release changed')
        for name, expected in manifest['before'].items():
            if (digest(old / name) if (old / name).is_file() else None) != expected:
                raise RuntimeError('Production source changed: ' + name)

    def link(target, suffix):
        pending = BASE / ('current.' + DEPLOYMENT + '-' + suffix)
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('Pending link exists')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, BASE / 'current')

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or len(members) != len(FILES) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive members')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, content in contents.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload hash mismatch: ' + name)
        if name.endswith('.py'):
            compile(content, name, 'exec')
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / (DEPLOYMENT + '-' + stamp)
    shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release / source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    reference = (old / 'whalegirl_linux/runtime.py').stat()
    for name, content in contents.items():
        target = release / name
        owner = (old / name).stat() if (old / name).exists() else reference
        target.write_bytes(content)
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(target))
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    protected = {name: digest(DATA / name) for name in ('settings.json', 'api-key.enc', 'image-api-key.enc') if (DATA / name).exists()}
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight():
        if time.monotonic() - started > 110:
            raise RuntimeError('Core busy; staged release preserved')
        time.sleep(2)
    unchanged()
    backup = ASSETS / (DEPLOYMENT + '-before-' + stamp)
    backup.mkdir(mode=0o700)
    for name in FILES:
        if (old / name).is_file():
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old / name, target)
    command('systemctl', 'stop', 'whalegirl-linux.service')
    switched = False
    try:
        if inflight():
            raise RuntimeError('Work crossed shutdown; deployment deferred')
        unchanged()
        if any(digest(DATA / name) != expected for name, expected in protected.items()):
            raise RuntimeError('Configuration changed during deployment')
        link(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state = api('state')
                settings = api('config')
                learning = api('learning')
                persona_state = api('persona-state')
                if (health.get('status') == 'ok'
                        and not health.get('failed_background_tasks')
                        and 'queued' in learning
                        and 'mood' in persona_state):
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
            'configuration_and_keys': all(digest(DATA / name) == expected for name, expected in protected.items()),
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment boundary changed')
        report = dict(
            release=str(release), previous=str(old), backup=str(backup), health=health,
            preserved=preserved, wechat_status=state.get('gateway', {}).get('status'),
            persona_live=persona_state,
            settings={k: settings.get(k) for k in (
                'evolution_preset', 'evolution_async_digest', 'evolution_slang_auto_accept',
                'mood_engine_enabled', 'daily_life_theater_enabled',
                'anti_formula_guard_enabled', 'chime_in_ultra_short',
            )},
            queue=learning['queued'], source_files=len(FILES), hashes=manifest['after'],
        )
        (ASSETS / (DEPLOYMENT + '-deployment.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k != 'hashes'}, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link(old, stamp + '-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

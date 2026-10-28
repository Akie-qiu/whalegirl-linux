"""Publish topic relevance and emotional-sticker handling without restarting WeChat."""
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

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
CONTAINER = 'whalegirl-wechat-candidate-4118'

FILES = {
    'whalegirl_linux/context.py',
    'whalegirl_linux/conversation.py',
    'whalegirl_linux/learning.py',
    'whalegirl_linux/memory.py',
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/stickers.py',
    'whalegirl_linux/store.py',
    'whalegirl_linux/topic_relevance.py',
}
DEPLOYMENT = 'topic-sticker'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args):
    return subprocess.check_output(args, text=True, timeout=25).strip()


def inflight():
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
        ))


def api(route, body=None):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request(
        'http://127.0.0.1:8092/api/' + route,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    with opener.open(request, timeout=12) as response:
        return json.load(response)


def main():
    lock = open(ASSETS / (DEPLOYMENT + '-deploy.lock'), 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    current = BASE / 'current'
    old = current.resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if current.resolve() != old:
            raise RuntimeError('Current release changed')
        for name, expected in manifest['before'].items():
            actual = digest(old / name) if (old / name).is_file() else None
            if actual != expected:
                raise RuntimeError('Production source changed: ' + name)

    unchanged()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if (len(members) != len(FILES) or {m.name for m in members} != FILES
                or not all(m.isfile() for m in members)):
            raise RuntimeError('Unexpected archive members')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, content in contents.items():
        if hashlib.sha256(content).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Payload hash mismatch: ' + name)
        if name.endswith('.py'):
            compile(content, name, 'exec')

    state = api('state')
    if state.get('paused_reason') or state.get('counts', {}).get('uncertain'):
        raise RuntimeError('Resolve paused or uncertain actions before deployment')
    if state.get('gateway', {}).get('status') != 'logged_in':
        raise RuntimeError('WeChat is offline; current login session must be preserved')
    original_config = api('config')
    original_config.pop('api_key_configured', None)
    original_enabled = bool(original_config.get('enabled'))
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    protected = {
        name: digest(DATA / name)
        for name in ('api-key.enc', 'image-api-key.enc')
        if (DATA / name).exists()
    }
    stamp = time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    release = BASE / 'releases' / (DEPLOYMENT + '-' + stamp)
    backup = ASSETS / (DEPLOYMENT + '-before-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(old, release, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
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
        saved = backup / name
        if (old / name).is_file():
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old / name, saved)

    started = time.monotonic()
    paused = False
    stopped = False
    switched = False

    def link(target, suffix):
        pending = BASE / ('current.' + DEPLOYMENT + '-' + suffix)
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('Pending release link already exists')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, current)

    try:
        if original_enabled:
            api('config', {'settings': {'enabled': False}})
            paused = True
        while api('state').get('processing') or api('images').get('busy') or inflight():
            if time.monotonic() - started > 110:
                raise RuntimeError('Core is busy; deployment deferred without forcing a restart')
            time.sleep(1)
        unchanged()
        if any(digest(DATA / name) != expected for name, expected in protected.items()):
            raise RuntimeError('API credentials changed during staging')
        command('systemctl', 'stop', 'whalegirl-linux.service')
        stopped = True
        if inflight():
            raise RuntimeError('An outbound operation crossed the shutdown boundary')
        with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as source:
            with sqlite3.connect(backup / 'state-before.sqlite3') as saved:
                source.backup(saved)
        (backup / 'state-before.sqlite3').chmod(0o600)
        link(release, stamp)
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
            raise RuntimeError('Updated core did not become healthy or reconnect to the existing session')

        live_config = api('config')
        live_config.pop('api_key_configured', None)
        if live_config.get('enabled') != original_enabled:
            api('config', {'settings': {'enabled': original_enabled}})
        live_config = api('config')
        live_config.pop('api_key_configured', None)
        if live_config != original_config:
            raise RuntimeError('An existing configuration value changed')
        preserved = {
            'wechat_pid': command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') == native,
            'reader_pid': command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') == reader,
            'container': command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER) == container,
            'api_keys': all(digest(DATA / name) == expected for name, expected in protected.items()),
            'configuration': live_config == original_config,
        }
        if not all(preserved.values()):
            raise RuntimeError('Deployment boundary changed: ' + str(preserved))
        state = api('state')
        report = {
            'release': str(release),
            'previous': str(old),
            'backup': str(backup),
            'health': health,
            'preserved': preserved,
            'wechat_status': state.get('gateway', {}).get('status'),
            'enabled': state.get('enabled'),
            'paused_reason': state.get('paused_reason'),
            'real_messages_sent': 0,
            'source_files': len(FILES),
            'hashes': manifest['after'],
        }
        (ASSETS / (DEPLOYMENT + '-deployment.json')).write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps({key: value for key, value in report.items() if key != 'hashes'},
                         ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link(old, stamp + '-rollback')
        if stopped:
            subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        if paused:
            for _ in range(40):
                try:
                    api('config', {'settings': {'enabled': original_enabled}})
                    break
                except Exception:
                    time.sleep(.5)
        raise


if __name__ == '__main__':
    main()

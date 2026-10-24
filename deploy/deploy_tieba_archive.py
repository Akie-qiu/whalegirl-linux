"""Install the archive feature only; retain all settings, native login and historical data."""
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

DATA = Path('/var/lib/whalegirl-linux/core')
BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
CONTAINER = 'whalegirl-wechat-candidate-4118'
FILES = {'whalegirl_linux/'+name for name in ('tieba.py', 'tieba_archive.py', 'app.py', 'settings.py', 'tieba.js', 'console.css')}
FILES.add('TIEBA.md')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def api(route):
    request = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization': 'Bearer '+(DATA/'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def install(path, data):
    stat = path.stat() if path.exists() else path.parent.stat()
    pending = path.with_name(path.name+'.archive-pending')
    pending.write_bytes(data)
    pending.chmod(0o644)
    os.chown(pending, stat.st_uid, stat.st_gid)
    pending.replace(path)


def main():
    import fcntl
    lock = open(ASSETS/'tieba-archive-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = (BASE/'current').resolve()
    manifest = json.loads(Path(sys.argv[2]).read_text())
    if str(root) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected manifest')

    def check():
        if (BASE/'current').resolve() != root:
            raise RuntimeError('Active release changed')
        for name, value in manifest['before'].items():
            if digest(root/name) != value:
                raise RuntimeError('Server file changed: '+name)
    check()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Checksum mismatch: '+name)
        if name.endswith('.py'):
            compile(data, name, 'exec')
    started = time.monotonic()
    while True:
        state, images = api('state'), api('images')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
            active = sum(db.execute(q).fetchone()[0] for q in (
                "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
                "SELECT count(*) FROM outbound_actions WHERE state='sending'",
                "SELECT count(*) FROM moments_actions WHERE state='sending'",
                "SELECT count(*) FROM picture_assets WHERE state='tagging'"))
        if not state.get('processing') and not images.get('busy') and not active and not state.get('tieba', {}).get('busy'):
            break
        if time.monotonic()-started > 180:
            raise RuntimeError('Waiting for active tasks; no changes made')
        time.sleep(2)
    check()
    backup = ASSETS/('tieba-archive-before-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    previous = {name: (root/name).read_bytes() for name in FILES if (root/name).exists()}
    for name, data in previous.items():
        path = backup/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=True, timeout=30)
    settings_before = json.loads((DATA/'settings.json').read_text())
    shutil.copy2(DATA/'settings.json', backup/'settings.json')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as source:
        memories_before = source.execute('SELECT count(*) FROM memories').fetchone()[0]
        with sqlite3.connect(backup/'state-before.sqlite3') as target:
            source.backup(target)
    try:
        check()
        for name, data in contents.items():
            install(root/name, data)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=True, timeout=30)
        for _ in range(60):
            try:
                state, tieba = api('state'), api('tieba')
                if 'archive_stats' not in tieba:
                    raise RuntimeError('Archive API not ready')
                break
            except Exception:
                time.sleep(.5)
        else:
            raise RuntimeError('Core did not become ready')
        if command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat') != native:
            raise RuntimeError('Native WeChat restarted')
        if command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value') != reader:
            raise RuntimeError('Reader restarted')
        for name in FILES:
            if digest(root/name) != manifest['after'][name]:
                raise RuntimeError('Installed checksum mismatch')
        images, config = api('images'), api('config')
        settings_after = json.loads((DATA/'settings.json').read_text())
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
            memories_after = db.execute('SELECT count(*) FROM memories').fetchone()[0]
        result = {'at': time.time(), 'release': str(root), 'backup': str(backup),
            'wechat_pid_preserved': True, 'reader_pid_preserved': True,
            'wechat_status': state.get('gateway', {}).get('status'), 'enabled': state['enabled'],
            'archive_enabled': config['tieba_archive_enabled'], 'archive_limit_mb': config['tieba_archive_limit_mb'],
            'other_settings_changed': [k for k, v in settings_before.items() if settings_after.get(k) != v],
            'character_variant': config['image_character_variant'],
            'memory_count_before': memories_before, 'memory_count_after': memories_after,
            'image_workers': list(images['workers']), 'archive_stats': tieba['archive_stats']}
        (ASSETS/'tieba-archive-deployment.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux'], check=False, timeout=30)
        for name, data in previous.items():
            install(root/name, data)
        # Additive tables, new modules and original data are retained on rollback.
        subprocess.run(['systemctl', 'start', 'whalegirl-linux'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

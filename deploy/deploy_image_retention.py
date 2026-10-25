"""Add library retention without restarting WeChat or deleting existing media."""
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

FILES = {'whalegirl_linux/'+name for name in (
    'images.py', 'image_retention.py', 'settings.py', 'app.py', 'images.js',
    'images.css', 'tieba.py', 'web.html')}


def main():
    lock = open(ASSETS/'image-retention-deploy.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    old = (BASE/'current').resolve()
    if str(old) != manifest['root'] or set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected deployment manifest')

    def unchanged():
        if (BASE/'current').resolve() != old:
            raise RuntimeError('Current release changed')
        for name, expected in manifest['before'].items():
            actual = digest(old/name) if (old/name).exists() else None
            if actual != expected:
                raise RuntimeError('Production source changed: '+name)

    def link_release(target, suffix):
        pending = BASE/('current.image-retention-'+suffix)
        if pending.exists() or pending.is_symlink():
            raise RuntimeError('Pending link already exists')
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, BASE/'current')

    unchanged()
    settings = json.loads((DATA/'settings.json').read_text())
    if any(v for k, v in settings.items() if k.startswith('image_retention_')):
        raise RuntimeError('Retention already configured; do not activate deletion during deployment')
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
    release = BASE/'releases'/('image-retention-'+stamp)
    shutil.copytree(old, release, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', 'test-runs', '.git'))
    for source in (old, *old.rglob('*')):
        target = release/source.relative_to(old)
        if target.exists() or target.is_symlink():
            owner = source.lstat()
            os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)
    for name, content in contents.items():
        target = release/name
        owner = (old/name if (old/name).exists() else old/'whalegirl_linux/images.py').stat()
        target.write_bytes(content)
        os.chown(target, owner.st_uid, owner.st_gid)
        target.chmod(owner.st_mode & 0o777)
        command('runuser', '-u', 'whalegirl-ai', '--', 'test', '-r', str(target))
    native = command('docker', 'exec', CONTAINER, 'pgrep', '-x', 'wechat')
    reader = command('systemctl', 'show', 'whalegirl-native-reader', '-p', 'MainPID', '--value')
    container = command('docker', 'inspect', '--format', '{{.Id}} {{.RestartCount}}', CONTAINER)
    resources_args = ('systemctl', 'show', 'whalegirl-linux.service', '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax')
    resources = command(*resources_args)
    started = time.monotonic()
    while api('state').get('processing') or api('images').get('busy') or inflight():
        if time.monotonic()-started > 120:
            raise RuntimeError('Core busy; staged release preserved')
        time.sleep(2)
    unchanged()
    backup = ASSETS/('image-retention-before-'+stamp)
    backup.mkdir(mode=0o700)
    for name in FILES:
        if (old/name).is_file():
            target = backup/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old/name, target)
    settings_hash = digest(DATA/'settings.json')
    command('systemctl', 'stop', 'whalegirl-linux.service')
    switched = False
    try:
        if inflight():
            raise RuntimeError('Work crossed shutdown; deployment deferred')
        # Additive schema only: preserve this small table, not another full chat DB copy.
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
            schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='picture_assets'").fetchone()[0]
            rows = db.execute('SELECT * FROM picture_assets').fetchall()
            with sqlite3.connect(backup/'picture-assets-before.sqlite3') as saved:
                saved.execute(schema)
                if rows:
                    saved.executemany('INSERT INTO picture_assets VALUES('+','.join('?' for _ in rows[0])+')', rows)
            count_before = len(rows)
        (backup/'picture-assets-before.sqlite3').chmod(0o600)
        unchanged()
        link_release(release, stamp)
        switched = True
        command('systemctl', 'start', 'whalegirl-linux.service')
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                    health = json.load(response)
                state, pictures = api('state'), api('images')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks') and 'retention' in pictures:
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
            'settings': digest(DATA/'settings.json') == settings_hash,
            'limits': command(*resources_args) == resources,
        }
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
            deleted = db.execute('SELECT count(*) FROM picture_assets WHERE deleted_at>0').fetchone()[0]
            count_after = db.execute('SELECT count(*) FROM picture_assets').fetchone()[0]
        if not all(preserved.values()) or deleted or count_after < count_before:
            raise RuntimeError('Deployment boundary changed')
        report = dict(release=str(release), previous=str(old), backup=str(backup),
            health=health, preserved=preserved, wechat_status=state.get('gateway', {}).get('status'),
            existing_assets=count_before, current_assets=count_after, deleted=deleted,
            backup_bytes=sum(p.stat().st_size for p in backup.rglob('*') if p.is_file()))
        (ASSETS/'image-retention-deployment.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=False, timeout=40)
            link_release(old, stamp+'-rollback')
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=False, timeout=30)
        raise


if __name__ == '__main__':
    main()

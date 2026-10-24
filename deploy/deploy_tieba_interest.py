"""Update keyword screening and text-meme recall while preserving native sessions."""
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
FILES = {'whalegirl_linux/'+name for name in ('tieba.py', 'tieba.js', 'settings.py', 'runtime.py')} | {'TIEBA.md'}
FEATURE = 'tieba-interest'


def api(route):
    req = urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization': 'Bearer '+(DATA/'admin.token').read_text().strip()})
    return json.load(urllib.request.urlopen(req, timeout=15))


def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()


def install(path, data):
    stat = path.stat()
    pending = path.with_name(path.name+'.interest-pending')
    pending.write_bytes(data)
    pending.chmod(stat.st_mode & 0o777)
    os.chown(pending, stat.st_uid, stat.st_gid)
    pending.replace(path)


def main():
    import fcntl
    lock = open(ASSETS/(FEATURE+'-deploy.lock'), 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    root = (BASE/'current').resolve()
    def check():
        if str(root) != manifest['root'] or (BASE/'current').resolve() != root:
            raise RuntimeError('Active release changed')
        for name, value in manifest['before'].items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest() != value:
                raise RuntimeError('Server file changed: '+name)
    if set(manifest['before']) != FILES or set(manifest['after']) != FILES:
        raise RuntimeError('Unexpected manifest')
    check()
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if len(members) != len(FILES) or {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected release')
        contents = {m.name: archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest() != manifest['after'][name]:
            raise RuntimeError('Checksum mismatch: '+name)
        if name.endswith('.py'): compile(data, name, 'exec')
    started = time.monotonic()
    while True:
        state, images, tieba = api('state'), api('images'), api('tieba')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro', uri=True) as db:
            active = sum(db.execute(q).fetchone()[0] for q in (
                "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
                "SELECT count(*) FROM outbound_actions WHERE state='sending'",
                "SELECT count(*) FROM moments_actions WHERE state='sending'",
                "SELECT count(*) FROM picture_assets WHERE state='tagging'"))
        if not state.get('processing') and not images.get('busy') and not tieba.get('busy') and not active:
            break
        if time.monotonic()-started > 180:
            raise RuntimeError('Active work continues; no files changed')
        time.sleep(2)
    check()
    native = command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader = command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    backup = ASSETS/(FEATURE+'-before-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    previous = {name: (root/name).read_bytes() for name in FILES}
    for name, data in previous.items():
        path = backup/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(['systemctl','stop','whalegirl-linux'], check=True, timeout=30)
    settings_before = json.loads((DATA/'settings.json').read_text())
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
        with sqlite3.connect(backup/'state-before.sqlite3') as target: source.backup(target)
    try:
        check()
        for name, data in contents.items(): install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'], check=True, timeout=30)
        for _ in range(60):
            try:
                config, state, tieba = api('config'), api('state'), api('tieba')
                if 'tieba_interest_sensitivity' not in config: raise RuntimeError('New config not ready')
                break
            except Exception: time.sleep(.5)
        else: raise RuntimeError('Core not ready')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat') != native:
            raise RuntimeError('WeChat restarted')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value') != reader:
            raise RuntimeError('Reader restarted')
        settings_after = json.loads((DATA/'settings.json').read_text())
        changed = [k for k in settings_before.keys() | settings_after.keys()
                   if k != 'tieba_interest_sensitivity' and settings_before.get(k) != settings_after.get(k)]
        result = {'at':time.time(),'backup':str(backup),'root':str(root),
            'wechat_pid_preserved':True,'reader_pid_preserved':True,
            'wechat_status':state.get('gateway',{}).get('status'),
            'sensitivity':config['tieba_interest_sensitivity'],'other_settings_changed':changed,
            'meme_count':len(tieba['memes']),'archive_count':tieba['archive_stats']['count'],
            'image_workers':list(api('images')['workers']), 'forced_test_messages':0}
        (ASSETS/(FEATURE+'-deployment.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        for name,data in previous.items(): install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise


if __name__ == '__main__': main()

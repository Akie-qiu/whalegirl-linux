"""Wait for in-flight work, then replace only the image workers and runtime wiring."""
import hashlib
import json
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
FILES = {'whalegirl_linux/images.py', 'whalegirl_linux/runtime.py', 'IMAGE_TASKS.md'}


def api(path):
    token = (DATA/'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092/api/'+path, headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def command(*args):
    return subprocess.check_output(args, text=True, timeout=25).strip()


def install(path, data):
    import os
    owner = path.stat()
    pending = path.with_name(path.name+'.workers-pending')
    pending.write_bytes(data)
    pending.chmod(owner.st_mode & 0o777)
    os.chown(pending,owner.st_uid,owner.st_gid)
    pending.replace(path)


def main():
    import fcntl
    lock = open(ASSETS/'image-workers-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest = json.loads(Path(sys.argv[2]).read_text())
    root = (BASE/'current').resolve()
    if str(root)!=manifest['root']:
        raise RuntimeError('Active release changed')
    for name, expected in manifest['before'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Source changed: '+name)
    with tarfile.open(sys.argv[1]) as archive:
        members = archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected worker release contents')
        contents = {m.name:archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if hashlib.sha256(data).hexdigest()!=manifest['after'][name]:
            raise RuntimeError('Release checksum failed: '+name)
        if name.endswith('.py'):
            compile(data,name,'exec')

    # Let paid requests and real sends finish before restarting the core service.
    started = time.monotonic();last_notice = -30
    while True:
        state = api('state');images = api('images')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            active = db.execute("SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')").fetchone()[0]
            active += db.execute("SELECT count(*) FROM outbound_actions WHERE state='sending'").fetchone()[0]
            active += db.execute("SELECT count(*) FROM moments_actions WHERE state='sending'").fetchone()[0]
        if not state.get('processing') and not images.get('busy') and not active:
            break
        elapsed = time.monotonic()-started
        if elapsed-last_notice>=30:
            print(json.dumps({'waiting_for_inflight_work':True,'seconds':round(elapsed),'active_actions':active}),flush=True)
            last_notice = elapsed
        if elapsed>360:
            raise RuntimeError('Live work still active; deployment deferred without interrupting it')
        time.sleep(2)

    backup = ASSETS/('image-workers-before-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    previous = {name:(root/name).read_bytes() for name in FILES}
    for index,(name,data) in enumerate(previous.items()):
        (backup/(str(index)+'-'+Path(name).name)).write_bytes(data)
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    native_before = command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader_before = command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=30)
    try:
        if (BASE/'current').resolve()!=root:
            raise RuntimeError('Active release changed during update')
        for name,data in contents.items():
            install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=30)
        for _ in range(40):
            try:
                images = api('images');state = api('state')
                if set(images.get('workers',{}))!={'generation','dispatch'}:
                    raise RuntimeError('Separate image workers did not start')
                break
            except Exception:
                time.sleep(.5)
        else:
            raise RuntimeError('Core did not become ready')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native_before:
            raise RuntimeError('WeChat PID changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader_before:
            raise RuntimeError('Native reader PID changed')
        result = {'at':time.time(),'release':str(root),'backup':str(backup),'hashes':manifest['after'],
                  'wechat_pid_preserved':True,'reader_pid_preserved':True,'enabled':state['enabled'],
                  'workers':list(images['workers']),'wechat_status':state.get('gateway',{}).get('status'),
                  'inflight_work_was_allowed_to_finish':True}
        (ASSETS/'image-workers-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'}),flush=True)
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        for name,data in previous.items():
            install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise


if __name__=='__main__':
    main()

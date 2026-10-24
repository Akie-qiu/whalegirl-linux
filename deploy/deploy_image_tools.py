"""Hash-checked core-only deployment; preserve native WeChat and latest settings."""
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

DATA=Path('/var/lib/whalegirl-linux/core')
BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
CONTAINER='whalegirl-wechat-candidate-4118'
FILES={'whalegirl_linux/'+v for v in ['image_tools.py','images.py','image_references.py','runtime.py','safety.py','settings.py','app.py','moments.py','images.js','images.css','console.js']}
FILES.update({'REFERENCE_IMAGES.md','IMAGE_TASKS.md'})


def api(name):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+name,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with urllib.request.urlopen(request,timeout=15) as response:
        return json.load(response)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def command(*args):
    return subprocess.check_output(args,text=True,timeout=25).strip()


def install(path, data):
    owner=path.stat() if path.exists() else path.parent.stat()
    pending=path.with_name(path.name+'.image-tools-pending')
    pending.write_bytes(data);pending.chmod(0o644)
    os.chown(pending,owner.st_uid,owner.st_gid)
    pending.replace(path)


def main():
    import fcntl
    lock=open(ASSETS/'image-tools-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads(Path(sys.argv[2]).read_text())
    root=(BASE/'current').resolve()
    if str(root)!=manifest['root'] or set(manifest['before'])!=FILES:
        raise RuntimeError('Unexpected release or manifest')
    def check_sources():
        if (BASE/'current').resolve()!=root:
            raise RuntimeError('Active release changed')
        for name,expected in manifest['before'].items():
            if digest(root/name)!=expected:
                raise RuntimeError('Source changed: '+name)
    check_sources()
    with tarfile.open(sys.argv[1]) as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or len(members)!=len(FILES) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected release files')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if hashlib.sha256(data).hexdigest()!=manifest['after'][name]:
            raise RuntimeError('Release checksum failed: '+name)
        if name.endswith('.py'):compile(data,name,'exec')
    started=time.monotonic();last=-30
    while True:
        state=api('state');images=api('images')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            active=sum(db.execute(q).fetchone()[0] for q in [
                "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
                "SELECT count(*) FROM outbound_actions WHERE state='sending'",
                "SELECT count(*) FROM moments_actions WHERE state='sending'"])
        if not state.get('processing') and not images.get('busy') and not active:break
        elapsed=time.monotonic()-started
        if elapsed-last>=30:
            print(json.dumps({'waiting_for_inflight':True,'seconds':round(elapsed),'active':active}),flush=True);last=elapsed
        if elapsed>600:raise RuntimeError('Active work did not finish; update deferred')
        time.sleep(2)
    check_sources()
    backup=ASSETS/('image-tools-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    previous={n:(root/n).read_bytes() for n in FILES if (root/n).exists()}
    for name,data in previous.items():
        path=backup/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    native=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=30)
    try:
        check_sources()
        settings_hash=digest(DATA/'settings.json')
        shutil.copy2(DATA/'settings.json',backup/'settings.json')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
            with sqlite3.connect(backup/'state-before.sqlite3') as target:source.backup(target)
        for name,data in contents.items():install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=30)
        for _ in range(60):
            try:
                state=api('state');images=api('images')
                if set(images.get('workers',{}))!={'generation','dispatch'} or 'decisions' not in images:
                    raise RuntimeError('Image tools not ready')
                break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Core did not become ready')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('WeChat PID changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Reader PID changed')
        result={'at':time.time(),'release':str(root),'backup':str(backup),'hashes':manifest['after'],
                'wechat_pid_preserved':True,'reader_pid_preserved':True,'enabled':state['enabled'],
                'wechat_status':state.get('gateway',{}).get('status'),'settings_file_unchanged':digest(DATA/'settings.json')==settings_hash,
                'workers':list(images['workers']),'asset_scopes':{scope:sum(a['scope']==scope for a in images['assets']) for scope in ['admin','chat','global']}}
        (ASSETS/'image-tools-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'}),flush=True)
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        # Keep new unreferenced modules and additive SQLite columns during rollback.
        # No data, release, asset or backup is deleted.
        for name,data in previous.items():install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise


if __name__=='__main__':main()

"""Deploy bounded conversation changes, keeping native WeChat and current settings."""
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
FILES={'whalegirl_linux/'+n for n in ['conversation.py','memory.py','settings.py','app.py','habits.js','improvements.js','web.html']}
FILES.add('CHAT_PERSONALITY.md')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with urllib.request.urlopen(request,timeout=12) as response:return json.load(response)


def command(*args):return subprocess.check_output(args,text=True,timeout=25).strip()


def install(path,data):
    owner=path.stat() if path.exists() else path.parent.stat()
    pending=path.with_name(path.name+'.personality-pending')
    pending.write_bytes(data);pending.chmod(0o644);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.replace(path)


def main():
    import fcntl
    lock=open(ASSETS/'chat-personality-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads(Path(sys.argv[2]).read_text());root=(BASE/'current').resolve()
    if str(root)!=manifest['root'] or set(manifest['before'])!=FILES or set(manifest['after'])!=FILES:
        raise RuntimeError('Unexpected deployment manifest')
    def check_sources():
        if (BASE/'current').resolve()!=root:raise RuntimeError('Active release changed')
        for name,expected in manifest['before'].items():
            if digest(root/name)!=expected:raise RuntimeError('Source changed: '+name)
    check_sources()
    with tarfile.open(sys.argv[1]) as archive:
        members=archive.getmembers()
        if len(members)!=len(FILES) or {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected release files')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if hashlib.sha256(data).hexdigest()!=manifest['after'][name]:raise RuntimeError('Checksum failed: '+name)
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
        if elapsed-last>=30:print(json.dumps({'waiting_for_inflight':True,'seconds':round(elapsed)}),flush=True);last=elapsed
        if elapsed>300:raise RuntimeError('Busy; deployment deferred')
        time.sleep(2)
    check_sources()
    backup=ASSETS/('chat-personality-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    previous={n:(root/n).read_bytes() for n in FILES if (root/n).exists()}
    for name,data in previous.items():
        path=backup/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    native=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=30)
    try:
        check_sources();settings_hash=digest(DATA/'settings.json')
        shutil.copy2(DATA/'settings.json',backup/'settings.json')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
            memory_before=source.execute('SELECT count(*) FROM memories').fetchone()[0]
            with sqlite3.connect(backup/'state-before.sqlite3') as target:source.backup(target)
        for name,data in contents.items():install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=30)
        for _ in range(60):
            try:
                state=api('state');cfg=api('config');images=api('images')
                if not all(k in cfg for k in ('banter_level','play_along','callback_frequency','assertiveness')):
                    raise RuntimeError('New controls not ready')
                if set(images.get('workers',{}))!={'generation','dispatch'}:raise RuntimeError('Image workers not ready')
                break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Core did not become ready')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('WeChat PID changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Reader PID changed')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            memory_after=db.execute('SELECT count(*) FROM memories').fetchone()[0]
            if 'expires_at' not in {r[1] for r in db.execute('PRAGMA table_info(memories)')}:
                raise RuntimeError('Memory migration missing')
        if memory_after<memory_before:raise RuntimeError('Memory count decreased')
        result={'at':time.time(),'release':str(root),'backup':str(backup),'hashes':manifest['after'],
            'wechat_pid_preserved':True,'reader_pid_preserved':True,'enabled':state['enabled'],
            'wechat_status':state.get('gateway',{}).get('status'),'settings_file_unchanged':digest(DATA/'settings.json')==settings_hash,
            'controls':{k:cfg[k] for k in ('banter_level','play_along','callback_frequency','assertiveness')},
            'memory_count_before':memory_before,'memory_count_after':memory_after,
            'character_variant':cfg['image_character_variant'],'workers':list(images['workers'])}
        (ASSETS/'chat-personality-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'}),flush=True)
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        for name,data in previous.items():install(root/name,data)
        # Additive columns, docs, previous releases and backups remain available.
        subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise


if __name__=='__main__':main()

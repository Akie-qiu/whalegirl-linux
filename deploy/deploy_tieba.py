"""Install an exact manifest, retain native WeChat login and enable one approved target."""
import hashlib
import importlib.metadata
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
FILES={'whalegirl_linux/'+n for n in ['settings.py','runtime.py','app.py','tieba.py','tieba.js',
    'images.py','image_tools.py','images.js','web.html','console.js','token_usage.py','token-usage.js']}
FILES.update({'requirements.txt','TIEBA.md'})


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def api(route, body=None):
    headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()}
    data=None if body is None else json.dumps(body).encode()
    if data is not None:headers['Content-Type']='application/json'
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,data=data,headers=headers)
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)


def command(*args):
    return subprocess.check_output(args,text=True,timeout=30).strip()


def install(path,data):
    stat=path.stat() if path.exists() else path.parent.stat()
    pending=path.with_name(path.name+'.tieba-pending')
    pending.write_bytes(data);pending.chmod(0o644);os.chown(pending,stat.st_uid,stat.st_gid);pending.replace(path)


def main():
    import fcntl
    lock=open(ASSETS/'tieba-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads(Path(sys.argv[2]).read_text())
    root=(BASE/'current').resolve()
    if str(root)!=manifest['root'] or set(manifest['before'])!=FILES or set(manifest['after'])!=FILES:
        raise RuntimeError('Unexpected manifest')
    def check():
        if (BASE/'current').resolve()!=root:raise RuntimeError('Active release changed')
        for name,value in manifest['before'].items():
            if digest(root/name)!=value:raise RuntimeError('Server file changed: '+name)
    check()
    if importlib.metadata.version('aiotieba')!='4.8.1':raise RuntimeError('Reader dependency missing')
    with tarfile.open(sys.argv[1]) as archive:
        members=archive.getmembers()
        if len(members)!=len(FILES) or {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if hashlib.sha256(data).hexdigest()!=manifest['after'][name]:raise RuntimeError('Checksum mismatch')
        if name.endswith('.py'):compile(data,name,'exec')
    chats=api('gateway/chats')
    cfg=api('config')
    targets=[c['id'] for c in chats if (c.get('remark') or c.get('name'))=='临时测试'
             and c['id'] in cfg['allowed_chat_ids']]
    if len(targets)!=1:raise RuntimeError('Expected one approved 临时测试 chat')
    started=time.monotonic();last=-30
    while True:
        state=api('state');images=api('images')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            active=sum(db.execute(q).fetchone()[0] for q in [
                "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
                "SELECT count(*) FROM outbound_actions WHERE state='sending'",
                "SELECT count(*) FROM moments_actions WHERE state='sending'",
                "SELECT count(*) FROM picture_assets WHERE state='tagging'"])
        if not state.get('processing') and not images.get('busy') and not active:break
        elapsed=time.monotonic()-started
        if elapsed-last>=30:print(json.dumps({'waiting_for_inflight':True,'seconds':round(elapsed)}),flush=True);last=elapsed
        if elapsed>300:raise RuntimeError('Busy; leave deployment for later')
        time.sleep(2)
    check()
    backup=ASSETS/('tieba-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    previous={n:(root/n).read_bytes() for n in FILES if (root/n).exists()}
    for name,data in previous.items():
        p=backup/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    native=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=30)
    before_settings=json.loads((DATA/'settings.json').read_text())
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
        memories_before=source.execute('SELECT count(*) FROM memories').fetchone()[0]
        with sqlite3.connect(backup/'state-before.sqlite3') as target:source.backup(target)
    try:
        check()
        for name,data in contents.items():install(root/name,data)
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=30)
        for _ in range(60):
            try:
                state=api('state');t=api('tieba')
                if 'tieba' not in state:raise RuntimeError('Not ready')
                break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Core did not become ready')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('Native WeChat restarted')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Reader restarted')
        new=api('tieba/config',{'values':{'tieba_enabled':True,'tieba_forums':['绝区零内鬼'],
            'tieba_share_chat_ids':targets,'tieba_share_enabled':True,'tieba_learn_enabled':True,
            'tieba_images_enabled':True}})
        images=api('images')
        if set(images.get('workers',{}))!={'generation','dispatch'}:raise RuntimeError('Image workers missing')
        for name in FILES:
            if digest(root/name)!=manifest['after'][name]:raise RuntimeError('Post-deploy checksum mismatch')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            memories_after=db.execute('SELECT count(*) FROM memories').fetchone()[0]
        if memories_after<memories_before:raise RuntimeError('Memory count decreased')
        changed=[k for k,v in before_settings.items() if not k.startswith('tieba_') and new.get(k)!=v]
        result={'at':time.time(),'release':str(root),'backup':str(backup),'hashes':manifest['after'],
            'wechat_pid_preserved':True,'reader_pid_preserved':True,
            'wechat_status':state.get('gateway',{}).get('status'),'enabled':state['enabled'],
            'character_variant':new['image_character_variant'],'share_target':'临时测试',
            'forums':new['tieba_forums'],'other_settings_changed':changed,
            'memory_count_before':memories_before,'memory_count_after':memories_after,
            'interval_minutes':new['tieba_interval_minutes'],'daily_shares':new['tieba_share_per_day'],
            'workers':list(images['workers'])}
        (ASSETS/'tieba-deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'},ensure_ascii=False),flush=True)
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        for name,data in previous.items():install(root/name,data)
        # Keep current non-Tieba settings, additive schema, new files and all backups.
        current=json.loads((DATA/'settings.json').read_text())
        original={k:v for k,v in current.items() if not k.startswith('tieba_') or k in before_settings}
        pending=DATA/'settings.tieba-rollback-pending'
        pending.write_text(json.dumps(original,ensure_ascii=False,indent=2));pending.chmod(0o600)
        st=(DATA/'settings.json').stat();os.chown(pending,st.st_uid,st.st_gid);pending.replace(DATA/'settings.json')
        subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise


if __name__=='__main__':main()

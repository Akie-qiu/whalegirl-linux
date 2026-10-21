"""Deploy only the response-action repair; preserve native login and all data."""
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

ASSETS=Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
CORE=Path('/opt/whalegirl-linux/current')
DATA=Path('/var/lib/whalegirl-linux/core')
NATIVE=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
expected={
    'whalegirl_linux/runtime.py','whalegirl_linux/store.py','whalegirl_linux/gateway.py',
    'whalegirl_linux/context.py','whalegirl_linux/settings.py','whalegirl_linux/stickers.py',
    'whalegirl_linux/app.py','whalegirl_linux/web.html','styles/tsundere_whale_girl.yaml',
    'deploy/native_message_reader.py','deploy/chat_select_linux.py'}
backup=ASSETS/('reply-actions-before-'+str(time.time_ns()));backup.mkdir(mode=0o700)
user=pwd.getpwnam('whalegirl-ai')
with tarfile.open(ASSETS/'reply-natural-20261020.tar') as archive:
    members=archive.getmembers()
    if {m.name for m in members}!=expected or any(not m.isfile() for m in members):
        raise RuntimeError('Unexpected deployment archive')
    payload={m.name:archive.extractfile(m).read() for m in members}
for name,data in payload.items():
    if name.endswith('.py'):compile(data,name,'exec')
subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20)
with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
    if db.execute("SELECT 1 FROM inbox WHERE state='sending' LIMIT 1").fetchone():
        raise RuntimeError('Preserve interrupted send for review')
    with sqlite3.connect(backup/'state.sqlite3') as saved:db.backup(saved)
shutil.copy2(DATA/'settings.json',backup/'settings.json')
for name,data in payload.items():
    target=CORE/name
    if target.exists():
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
    target.write_bytes(data);os.chown(target,0,user.pw_gid);target.chmod(0o640)
for name in ['native_message_reader.py','chat_select_linux.py']:
    target=NATIVE/name;shutil.copy2(target,backup/('live-'+name))
    # Copy in place: the live helper is mounted by inode in the read-only container.
    shutil.copyfile(CORE/'deploy'/name,target)
settings=json.loads((DATA/'settings.json').read_text())
settings.update(enabled=True,natural_group_replies=True,stickers_enabled=True,poll_interval=.4)
(DATA/'settings.json').write_text(json.dumps(settings,ensure_ascii=False,indent=2))
os.chown(DATA/'settings.json',user.pw_uid,user.pw_gid);(DATA/'settings.json').chmod(0o600)
subprocess.run(['systemctl','restart','whalegirl-native-reader'],check=True,timeout=15)
subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=15)
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=(DATA/'admin.token').read_text().strip()
for attempt in range(25):
    try:
        with op.open(urllib.request.Request('http://127.0.0.1:8092/api/state',headers={'Authorization':'Bearer '+token}),timeout=3) as r:state=json.load(r)
        if state['gateway']['status']=='logged_in' and state.get('stickers',{}).get('available'):
            break
    except (OSError,ValueError):pass
    time.sleep(.3)
else:raise RuntimeError('New core is not ready; inspect service logs')
report={'backup':str(backup),'verified_at':time.time(),'state':state,
        'native_wechat_restarted':False,'natural_group_replies':True,'poll_interval':.4}
(ASSETS/'reply-actions-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))

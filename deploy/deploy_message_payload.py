"""Install quote/merged-record reading while preserving the native WeChat login."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
NATIVE=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
current=BASE/'current';previous=current.resolve();stamp=time.strftime('%Y%m%d-%H%M%S')
stage=BASE/'releases'/('message-payload-'+stamp);backup=ASSETS/('message-payload-before-'+stamp)
backup.mkdir(mode=0o700)
expected={'whalegirl_linux/'+n for n in ['message_payload.py','runtime.py','store.py','context.py']} | {
    'deploy/native_message_reader.py','tests/test_message_payload.py'}
with tarfile.open(ASSETS/'message-payload-20261021.tar') as archive:
    members=archive.getmembers()
    if {m.name for m in members}!=expected or any(not m.isfile() for m in members):raise RuntimeError('Unexpected deployment archive')
    payload={m.name:archive.extractfile(m).read() for m in members}
for name,content in payload.items():
    if name.endswith('.py'):compile(content,name,'exec')
shutil.copytree(previous,stage,symlinks=True)
for name,content in payload.items():
    path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
tests=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests','-p','test_message_payload.py','-v'],
                     cwd=stage,capture_output=True,text=True,timeout=30)
(backup/'focused-tests.log').write_text(tests.stdout+tests.stderr)
print((tests.stdout+tests.stderr)[-4000:],flush=True)
if tests.returncode:raise RuntimeError('Quote/context checks failed; live service unchanged')
spec=importlib.util.spec_from_file_location('new_message_payload',stage/'whalegirl_linux/message_payload.py')
parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
fixtures=json.loads((ASSETS/'quoted-forward-fixtures-20261021.json').read_text())
real={'quotes':0,'forwarded_records':0,'forwarded_items':0}
for sample in fixtures['samples']:
    value=parser.parse_app_message(sample['body'])
    if sample['app_type']=='57':
        if not value.get('reply') or not value['reply'].get('content'):raise RuntimeError('Actual quoted message did not parse')
        real['quotes']+=1
    elif sample['app_type']=='19':
        record=value.get('forwarded') or {}
        if not record.get('items'):raise RuntimeError('Actual forwarded record did not parse')
        real['forwarded_records']+=1;real['forwarded_items']+=len(record['items'])
    rendered=parser.render_app(value)
    if 'aeskey' in rendered or 'cdnthumbkey' in rendered:raise RuntimeError('Internal attachment metadata reached rendered text')
print(json.dumps({'actual_native_samples':real},ensure_ascii=False),flush=True)
user=pwd.getpwnam('whalegirl-ai')
for path in [stage,*stage.rglob('*')]:
    if not path.is_symlink():os.chown(path,0,user.pw_gid);path.chmod(0o750 if path.is_dir() else 0o640)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=(DATA/'admin.token').read_text().strip()
def api(route,body=None):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=10) as response:return json.load(response)
original=api('config');original.pop('api_key_configured',None)
state=api('state')
if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):raise RuntimeError('Live send needs review before upgrade')
native_before=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
environment=subprocess.check_output(['systemctl','show','whalegirl-linux','-p','Environment','--value'],text=True)
for entry in shlex.split(environment):
    name,sep,value=entry.partition('=')
    if sep and name.startswith('WHALEGIRL_'):os.environ[name]=value
(backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
print('Updating read-only message reader and Python context; native WeChat stays running',flush=True)
api('config',{'settings':{**original,'enabled':False}})
stopped=False;native_changed=False
try:
    for _ in range(200):
        if not api('state').get('processing'):break
        time.sleep(.3)
    else:raise RuntimeError('Live operation did not drain')
    if api('state').get('counts',{}).get('uncertain'):raise RuntimeError('Unconfirmed send; preserve for review')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20);stopped=True
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        with sqlite3.connect(backup/'state.sqlite3') as saved:db.backup(saved)
    for name in ['native_message_reader.py','message_payload.py']:
        target=NATIVE/name
        if target.exists():shutil.copy2(target,backup/('native-'+name))
    subprocess.run(['systemctl','stop','whalegirl-native-reader'],check=True,timeout=20)
    native_changed=True
    for name,data in [('native_message_reader.py',payload['deploy/native_message_reader.py']),('message_payload.py',payload['whalegirl_linux/message_payload.py'])]:
        target=NATIVE/name;target.write_bytes(data);os.chown(target,0,user.pw_gid);target.chmod(0o640)
    pending=BASE/('current.message-payload-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,current)
    subprocess.run(['systemctl','start','whalegirl-native-reader'],check=True,timeout=20)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    for _ in range(50):
        try:
            state=api('state')
            if state['gateway']['status']=='logged_in':break
        except (OSError,ValueError):pass
        time.sleep(.3)
    else:raise RuntimeError('New core did not become available')
    # Enrich only stored card contents; never queue or resend old messages.
    sys.path.insert(0,str(current))
    from whalegirl_linux.gateway import AgentGateway
    from whalegirl_linux.settings import Config
    from whalegirl_linux.store import Store
    from whalegirl_linux.runtime import Runtime
    config=Config(DATA)
    class ExistingStore(Store):
        def __init__(self):
            self.db=sqlite3.connect(DATA/'state.sqlite3',check_same_thread=False)
            self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA busy_timeout=5000');self.lock=threading.RLock()
    async def backfill():
        g=AgentGateway('http://127.0.0.1:6175',config.gateway_token,control_directory=DATA);s=ExistingStore();count=0
        try:
            for chat in original['allowed_chat_ids']:
                for offset in range(0,2000,200):
                    value=await g.native_request('/messages',chat=chat,limit=200,offset=offset)
                    rows=value['messages'];events=[Runtime.event(original['account_id'],chat,r) for r in rows if r.get('type')==49]
                    events=[e for e in events if e]
                    s.observe(events)
                    for e in events:
                        s.db.execute("UPDATE inbox SET content=?,payload_json=? WHERE key=? AND state NOT IN ('generating','sending')",(e['content'],e['payload_json'],e['key']))
                    s.db.commit();count+=len(events)
                    if len(rows)<200:break
            return count
        finally:await g.close();s.close()
    enriched=asyncio.run(backfill())
    live_config=api('config');live_config.pop('api_key_configured',None)
    api('config',{'settings':{**live_config,'enabled':original['enabled']}})
    state=api('state')
    native_after=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
    report={'release':str(stage),'backup':str(backup),'focused_checks':'6 passed','actual_samples':real,
            'enriched_existing_cards':enriched,'wechat_status':state['gateway']['status'],'enabled':state['enabled'],
            'paused_reason':state.get('paused_reason'),'native_wechat_process_unchanged':native_before==native_after,
            'trigger_only_chat_ids_preserved':live_config['trigger_only_chat_ids']==original['trigger_only_chat_ids']}
    (ASSETS/'message-payload-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
except BaseException:
    if stopped:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20)
        rollback=BASE/('current.message-payload-rollback-'+stamp);rollback.symlink_to(previous,target_is_directory=True);os.replace(rollback,current)
    if native_changed:
        subprocess.run(['systemctl','stop','whalegirl-native-reader'],check=True,timeout=20)
        for name in ['native_message_reader.py','message_payload.py']:
            saved=backup/('native-'+name)
            if saved.exists():shutil.copyfile(saved,NATIVE/name)
        subprocess.run(['systemctl','start','whalegirl-native-reader'],check=True,timeout=20)
    settings=DATA/'settings.json';settings.write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chown(settings,user.pw_uid,user.pw_gid);settings.chmod(0o600)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    raise

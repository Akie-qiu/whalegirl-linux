"""Stage the chat-habits upgrade, run bounded checks, then replace only the Python core."""
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

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')
stamp=time.strftime('%Y%m%d-%H%M%S')
current=BASE/'current'
previous=current.resolve()
stage=BASE/'releases'/('chat-habits-'+stamp)
backup=ASSETS/('chat-habits-before-'+stamp)
backup.mkdir(mode=0o700)
expected={'whalegirl_linux/'+n for n in ['settings.py','conversation.py','memory.py','runtime.py','store.py','context.py',
    'safety.py','app.py','web.html','console.js','habits.js']} | {'tests/test_chat_habits.py'}
with tarfile.open(ASSETS/'chat-habits-20261021.tar') as archive:
    members=archive.getmembers()
    if set(m.name for m in members)!=expected or any(not m.isfile() for m in members):
        raise RuntimeError('Unexpected deployment archive')
    payload={m.name:archive.extractfile(m).read() for m in members}
for name,content in payload.items():
    if name.endswith('.py'):compile(content,name,'exec')
shutil.copytree(previous,stage,symlinks=True)
for name,content in payload.items():
    target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
python=str(BASE/'venv/bin/python')
print('Staged core; checking send plans, memory revisions and group wake-up policy',flush=True)
tests=subprocess.run([python,'-m','unittest','discover','-s','tests','-p','test_chat_habits.py','-v'],
                     cwd=stage,capture_output=True,text=True,timeout=60)
(backup/'focused-tests.log').write_text(tests.stdout+tests.stderr)
print((tests.stdout+tests.stderr)[-5500:],flush=True)
if tests.returncode:raise RuntimeError('Focused checks failed; active service was not changed')
user=pwd.getpwnam('whalegirl-ai')
for target in [stage,*stage.rglob('*')]:
    if target.is_symlink():continue
    os.chown(target,0,user.pw_gid);target.chmod(0o750 if target.is_dir() else 0o640)
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()
def api(route,body=None):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with op.open(req,timeout=10) as response:return json.load(response)
original=api('config');original.pop('api_key_configured',None)
(backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
before=api('state')
native_before=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
if before.get('paused_reason') or before.get('counts',{}).get('uncertain'):
    raise RuntimeError('Core has an unresolved pause; preserved for inspection')
print('Entering brief core maintenance; native WeChat remains running',flush=True)
api('config',{'settings':{**original,'enabled':False}})
stopped=False
try:
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        state=api('state')
        if not state.get('processing'):break
        time.sleep(.3)
    else:raise RuntimeError('Core still has an active operation; upgrade postponed')
    if api('state').get('counts',{}).get('uncertain'):raise RuntimeError('Unconfirmed send needs review')
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20);stopped=True
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        with sqlite3.connect(backup/'state.sqlite3') as saved:db.backup(saved)
    pending=BASE/('current.chat-habits-'+stamp)
    pending.symlink_to(stage,target_is_directory=True)
    os.replace(pending,current)
    # Preserve all existing settings and credentials; the new Settings class supplies feature defaults.
    restored={**original,'chat_habits_enabled':True,'editable_memory_enabled':True,
              'text_per_hour':original.get('safety_per_hour',160),'text_per_day':original.get('safety_per_day',500)}
    settings_path=DATA/'settings.json'
    settings_path.write_text(json.dumps(restored,ensure_ascii=False,indent=2))
    os.chown(settings_path,user.pw_uid,user.pw_gid);settings_path.chmod(0o600)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    for _ in range(50):
        try:
            state=api('state');settings=api('config')
            if state.get('gateway',{}).get('status')=='logged_in' and 'chat_habits_enabled' in settings:break
        except (OSError,ValueError):pass
        time.sleep(.3)
    else:raise RuntimeError('New core did not become ready')
    if settings['allowed_chat_ids']!=original['allowed_chat_ids'] or settings['trigger_only_chat_ids']!=original['trigger_only_chat_ids']:
        raise RuntimeError('Existing chat policy changed unexpectedly')
    if api('memory')['memories'] is None:raise RuntimeError('Memory API missing')
    native_after=subprocess.check_output(['docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat'],text=True).strip()
    report={'release':str(stage),'previous':str(previous),'backup':str(backup),'focused_tests':'8 passed',
            'wechat_status':state['gateway']['status'],'wechat_process_unchanged':native_before==native_after,
            'enabled':state['enabled'],'paused_reason':state.get('paused_reason'),
            'allowed_chats':len(settings['allowed_chat_ids']),'trigger_only_chat_ids':settings['trigger_only_chat_ids'],
            'settings':{k:settings[k] for k in ['chat_habits_enabled','debounce_single_seconds','debounce_burst_seconds',
                'debounce_max_seconds','reply_max_parts','reply_part_interval','participation','quiet_start_hour','quiet_end_hour',
                'text_per_hour','text_per_day','sticker_per_hour','sticker_per_day','editable_memory_enabled']}}
    (ASSETS/'chat-habits-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
except Exception:
    # Roll back by replacing a symlink; do not erase either release or any memory/chat records.
    if stopped:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=20)
        rollback=BASE/('current.rollback-'+stamp);rollback.symlink_to(previous,target_is_directory=True);os.replace(rollback,current)
    (DATA/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
    os.chown(DATA/'settings.json',user.pw_uid,user.pw_gid)
    subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=20)
    raise

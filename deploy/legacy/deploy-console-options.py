"""Deploy send-limit/sticker controls and the requested console password."""

import os
CONSOLE_PASSWORD = os.environ["WHALEGIRL_CONSOLE_PASSWORD"]
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

ROOT=Path('/opt/whalegirl-linux/current')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
TOKEN=(DATA/'admin.token').read_text().strip()

def api(route,body=None,credential=None,anonymous=False):
    headers={'Content-Type':'application/json'}
    if not anonymous:headers['Authorization']='Bearer '+(TOKEN if credential is None else credential)
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=None if body is None else json.dumps(body).encode(),headers=headers)
    return json.load(urllib.request.urlopen(request,timeout=35))

def service(*args):subprocess.run(['systemctl',*args],check=True,timeout=40)

original=api('config');original.pop('api_key_configured',None)
old_token=TOKEN
owner=(DATA/'admin.token').stat()
names={'whalegirl_linux/settings.py','whalegirl_linux/runtime.py','whalegirl_linux/safety.py',
       'whalegirl_linux/app.py','whalegirl_linux/console.js','whalegirl_linux/web.html','README.md'}
with tarfile.open(ASSETS/'console-options-20261020.tar') as archive:
    members=archive.getmembers();assert {m.name for m in members}==names and all(m.isfile() for m in members)
    staged={m.name:archive.extractfile(m).read() for m in members}
for name,source in staged.items():
    if name.endswith('.py'):compile(source,name,'exec')
backup=ASSETS/('console-options-before-'+str(time.time_ns()));backup.mkdir(mode=0o700)
for name in ['settings.json','admin.token','console-password.json']:
    if (DATA/name).exists():shutil.copy2(DATA/name,backup/name)
for name in names:
    saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,saved)
api('config',{'settings':{**original,'enabled':False}})
for _ in range(120):
    state=api('state')
    if not state['processing'] and not any(state['counts'].get(k) for k in ('sending','generating')):break
    time.sleep(.5)
else:
    api('config',{'settings':original});raise RuntimeError('Sender still active; update deferred')
service('stop','whalegirl-linux.service')
try:
    for name,source in staged.items():(ROOT/name).write_bytes(source)
    sys.path.insert(0,str(ROOT))
    from whalegirl_linux.settings import Config
    config=Config(DATA)
    config.set_console_password(CONSOLE_PASSWORD)
    os.chown(DATA/'console-password.json',owner.st_uid,owner.st_gid)
    TOKEN=secrets.token_urlsafe(32)
    # Write in place so the service keeps ownership of its private maintenance key.
    (DATA/'admin.token').write_text(TOKEN)
    os.chmod(DATA/'admin.token',0o600)
    service('start','whalegirl-linux.service')
    for _ in range(40):
        try:api('config');break
        except OSError:time.sleep(.5)
    else:raise RuntimeError('Updated console did not start')
    current=api('config');current.pop('api_key_configured',None)
    # Verify configurable zero caps and sticker intensity while automatic replies are off.
    trial={**current,'enabled':False,'safety_per_minute':0,'safety_per_hour':0,
           'safety_chat_per_hour':0,'safety_per_day':0,'sticker_aggressiveness':'high'}
    api('config',{'settings':trial})
    checked=api('config');assert checked['sticker_aggressiveness']=='high'
    assert all(checked[name]==0 for name in ['safety_per_minute','safety_per_hour','safety_chat_per_hour','safety_per_day'])
    assert not api('safety')['rate_limited']
    restored={**original,'safety_send_limits_enabled':True,'sticker_aggressiveness':'normal'}
    api('config',{'settings':restored})
    # Validate the actual user password through the public login route, without an admin header.
    login=api('auth/login',{'password':CONSOLE_PASSWORD},anonymous=True)
    session=login['token'];assert session!=CONSOLE_PASSWORD
    final=api('config',credential=session);state=api('state',credential=session)
    assert final['allowed_chat_ids']==original['allowed_chat_ids']
    assert final['trigger_only_chat_ids']==original['trigger_only_chat_ids']
    assert final['system_prompt_override']==original['system_prompt_override']
    assert state['gateway']['status']=='logged_in'
    try:api('config',credential=old_token)
    except urllib.error.HTTPError as error:assert error.code==401
    else:raise RuntimeError('Previous maintenance credential should have been revoked')
    api('auth/logout',{},credential=session)
    try:api('config',credential=session)
    except urllib.error.HTTPError as error:assert error.code==401
    else:raise RuntimeError('Logged-out session should be rejected')
    report={'deployment':'ok','backup':str(backup),'password_login':'verified',
        'old_credential_revoked':True,'session_logout':'verified','zero_caps_save_readback':'verified',
        'sticker_aggressiveness':final['sticker_aggressiveness'],
        'safety_send_limits_enabled':final['safety_send_limits_enabled'],
        'limits':{k:final[k] for k in ['safety_per_minute','safety_per_hour','safety_chat_per_hour','safety_per_day']},
        'login':state['gateway']['status'],'paused_reason':state['paused_reason']}
    (ASSETS/'console-options-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
except Exception:
    service('stop','whalegirl-linux.service')
    for name in names:shutil.copy2(backup/name,ROOT/name)
    for name in ['settings.json','admin.token','console-password.json']:
        if (backup/name).exists():shutil.copy2(backup/name,DATA/name)
    service('start','whalegirl-linux.service')
    raise

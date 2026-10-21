"""Deploy local safeguards while preserving WeChat login and authorized chats."""
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT=Path('/opt/whalegirl-linux/current')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets/candidate-4.1.1.8')
TOKEN=(DATA/'admin.token').read_text().strip()

def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(request,timeout=35))

def service(*args):
    subprocess.run(['systemctl',*args],check=True,timeout=40)

original=api('config');original.pop('api_key_configured',None)
names={'whalegirl_linux/safety.py','whalegirl_linux/settings.py','whalegirl_linux/runtime.py',
       'whalegirl_linux/gateway.py','whalegirl_linux/app.py','whalegirl_linux/console.js',
       'whalegirl_linux/web.html','README.md'}
with tarfile.open(ASSETS/'safety-protection-20261020.tar') as archive:
    members=archive.getmembers()
    assert {m.name for m in members}==names and all(m.isfile() for m in members)
    staged={m.name:archive.extractfile(m).read() for m in members}
for name,source in staged.items():
    if name.endswith('.py'):compile(source,name,'exec')
backup=ASSETS/('safety-protection-before-'+str(time.time_ns()));backup.mkdir(mode=0o700)
shutil.copy2(DATA/'settings.json',backup/'settings.json')
for name in names:
    if (ROOT/name).exists():
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,saved)
api('config',{'settings':{**original,'enabled':False}})
for _ in range(120):
    current=api('state')
    if not current['processing'] and not any(current['counts'].get(k) for k in ('sending','generating')):break
    time.sleep(.5)
else:
    api('config',{'settings':original});raise RuntimeError('Sender is still active; deployment deferred')
service('stop','whalegirl-linux.service')
try:
    for name,source in staged.items():
        target=ROOT/name;target.write_bytes(source)
        if name.endswith('safety.py'):target.chmod(0o644)
    service('start','whalegirl-linux.service')
    for _ in range(40):
        try:api('config');break
        except OSError:time.sleep(.5)
    else:raise RuntimeError('Updated core did not start')
    api('config',{'settings':{**original,'enabled':False,'safety_enabled':True}})
    inspection=api('safety/check',{})
    assert not inspection['check_error'],inspection['check_error']
    api('config',{'settings':{**original,'safety_enabled':True}})
    config=api('config');state=api('state');risk=api('safety')
    assert config['allowed_chat_ids']==original['allowed_chat_ids']
    assert config['trigger_only_chat_ids']==original['trigger_only_chat_ids']
    assert config['quote_replies_enabled']==original['quote_replies_enabled']
    assert config['system_prompt_override']==original['system_prompt_override']
    assert risk['enabled'] and state['gateway']['status']=='logged_in'
    report={'deployment':'ok','backup':str(backup),'limits':risk['limits'],'counts':risk['counts'],
        'limited_chats':risk['limited_chats'],'client_warning':risk['client_warning'],
        'check_error':risk['check_error'],'hold_reason':risk['hold_reason'],
        'paused_reason':state['paused_reason'],'last_error':state['last_error'],
        'login':state['gateway']['status'],'authorized_chats':len(config['allowed_chat_ids'])}
    (ASSETS/'safety-protection-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)
except Exception:
    service('stop','whalegirl-linux.service')
    for name in names:
        if (backup/name).exists():shutil.copy2(backup/name,ROOT/name)
    shutil.copy2(backup/'settings.json',DATA/'settings.json')
    service('start','whalegirl-linux.service')
    raise

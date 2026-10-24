"""Publish image fallback and native delivery fixes; preserve the WeChat process."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux');ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
APP=('context.py','gateway.py','media_locator.py','runtime.py','store.py','visible_media.py','console.js','web.html')
NATIVE=('chat_select_linux.py','native_message_reader.py')
FILES={'whalegirl_linux/'+n for n in APP}|{'deploy/'+n for n in NATIVE}|{'tests/test_media_delivery.py','deploy/deploy_media_delivery.py'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=12) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=35).stdout.strip()


def idle():
    until=time.monotonic()+40
    while True:
        state=api('state')
        if not state.get('processing'):return state
        if time.monotonic()>=until:raise RuntimeError('Core busy; refusing forced restart')
        time.sleep(.25)


def publish(target,data,stamp):
    owner=target.stat();temporary=target.with_name(target.name+'.media-delivery-'+stamp)
    temporary.write_bytes(data);os.chown(temporary,owner.st_uid,owner.st_gid);temporary.chmod(owner.st_mode & 0o777)
    os.replace(temporary,target)


def deploy():
    stamp=time.strftime('%Y%m%d-%H%M%S');previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('media-delivery-'+stamp)
    backup=ASSETS/('media-delivery-before-'+stamp);backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'media-delivery-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):raise RuntimeError('Unexpected payload')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    # These tests touch temporary fixtures only and never send a real chat.
    checks=[]
    for pattern in ('test_media_delivery.py','test_conversation_upgrade.py','test_chat_habits.py'):
        result=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests','-p',pattern,'-v'],
            cwd=stage,capture_output=True,text=True,timeout=35)
        (backup/(pattern+'.log')).write_text(result.stdout+result.stderr)
        print((result.stdout+result.stderr)[-2200:],flush=True)
        if result.returncode:raise RuntimeError('Regression failed; deployment unchanged')
        checks.append(pattern)
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for target in (stage,*stage.rglob('*')):
        if not target.is_symlink():os.chown(target,0,group);target.chmod(0o750 if target.is_dir() else 0o640)
    state=idle()
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):raise RuntimeError('Pending uncertain send; deployment deferred')
    original=api('config');original.pop('api_key_configured',None)
    was_online=state['gateway']['status']=='logged_in'
    native_before=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    original_native={n:(TOOLS/n).read_bytes() for n in NATIVE}
    for n in NATIVE:shutil.copy2(TOOLS/n,backup/n)
    (backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2))
    (backup/'settings.json').chmod(0o600)
    stopped=False
    try:
        api('config',{'settings':{'enabled':False}});idle()
        command('systemctl','stop','whalegirl-linux');stopped=True
        for n in NATIVE:publish(TOOLS/n,contents['deploy/'+n],stamp)
        command('systemctl','restart','whalegirl-native-reader')
        pending=BASE/('current.media-delivery-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,BASE/'current')
        command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                state=api('state')
                if state['gateway']['status']=='logged_in' or (not was_online and state['gateway']['status']=='logged_out'):break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('Updated core not ready')
        live=api('config');live.pop('api_key_configured',None)
        if any(live.get(k)!=v for k,v in original.items() if k!='enabled'):raise RuntimeError('Unrelated settings changed')
        native_after=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
        if native_before!=native_after:raise RuntimeError('Native WeChat process changed')
        api('config',{'settings':{'enabled':original['enabled']}})
        state=api('state')
        report={'release':str(stage),'backup':str(backup),'checks':checks,'native_pid_unchanged':True,
            'native_pid':native_after,'wechat_status':state['gateway']['status'],'enabled':state['enabled'],
            'paused':state.get('paused_reason'),'last_error':state.get('last_error'),
            'hashes':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()}}
        (ASSETS/'media-delivery-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='hashes'},ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            for n,data in original_native.items():publish(TOOLS/n,data,stamp+'-rollback')
            command('systemctl','restart','whalegirl-native-reader')
            pending=BASE/('current.media-delivery-rollback-'+stamp);pending.symlink_to(previous,target_is_directory=True);os.replace(pending,BASE/'current')
            temporary=DATA/('settings.media-delivery-rollback-'+stamp);owner=(DATA/'settings.json').stat()
            temporary.write_text(json.dumps(original,ensure_ascii=False));os.chown(temporary,owner.st_uid,owner.st_gid);temporary.chmod(owner.st_mode & 0o777);os.replace(temporary,DATA/'settings.json')
            command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':{'enabled':original['enabled']}})
        raise


if __name__=='__main__':deploy()

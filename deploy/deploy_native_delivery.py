"""Deploy native delivery repairs without restarting the logged-in WeChat."""
import hashlib
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

BASE=Path('/opt/whalegirl-linux');ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER='whalegirl-wechat-candidate-4118'
NATIVE=('chat_select_linux.py','moments_ui.py','native_process.py','native_message_reader.py','a11y_safe.py','verify_native_delivery.py')
FILES={'whalegirl_linux/moments.py','whalegirl_linux/moments.js','whalegirl_linux/console.js','whalegirl_linux/web.html',
       *('deploy/'+n for n in NATIVE),'deploy/deploy_native_delivery.py','tests/test_native_delivery.py','NATIVE_DELIVERY_FIX.md'}
token=(DATA/'admin.token').read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,headers={'Authorization':'Bearer '+token})
    return json.load(opener.open(request,timeout=15))


def command(*args,timeout=40):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: '+args[0]+'; '+result.stderr[-1800:])
    return result.stdout.strip()


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def idle():
    for _ in range(80):
        s,m,lib=api('state'),api('moments'),api('sticker-library')
        if not s.get('processing') and not lib.get('worker_busy') and not any(
            r['state'] in ('sending','generating') for r in m['actions']) and not any(
            r['state']=='collecting' for r in lib['candidates']):return
        time.sleep(.5)
    raise RuntimeError('Core busy; defer deployment')


def install(path,data):
    owner=(path if path.exists() else path.parent).stat()
    pending=path.with_name(path.name+'.native-delivery-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod((owner.st_mode & 0o777) if path.exists() else 0o640)
    os.replace(pending,path)


def deploy():
    baseline=json.loads((ASSETS/'native-delivery-baseline-20261022.json').read_text())
    previous=(BASE/'current').resolve()
    if str(previous)!=baseline['release']:raise RuntimeError('Current release changed')
    for name,expected in baseline['files'].items():
        if digest(previous/name)!=expected:raise RuntimeError('Live source changed: '+name)
    for name,expected in baseline['tools'].items():
        if digest(TOOLS/name)!=expected:raise RuntimeError('Native source changed: '+name)
    with tarfile.open(ASSETS/'native-delivery-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):raise RuntimeError('Unexpected payload')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    stamp=time.strftime('%Y%m%d-%H%M%S');stage=BASE/'releases'/('native-delivery-'+stamp)
    backup=ASSETS/('native-delivery-before-'+stamp);backup.mkdir(mode=0o700)
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        p=stage/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for p in (stage,*stage.rglob('*')):
        if not p.is_symlink():os.chown(p,0,group);p.chmod(0o750 if p.is_dir() else 0o640)
    py='/opt/whalegirl-linux/venv/bin/python'
    for pattern in ('test_native_delivery.py','test_moments.py','test_moments_scroll.py'):
        subprocess.run([py,'-m','unittest','discover','-s','tests','-p',pattern,'-q'],cwd=stage,check=True,
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=25)
    idle();state=api('state');original=api('config');original.pop('api_key_configured',None)
    if state.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain') or api('moments').get('uncertain'):
        raise RuntimeError('Resolve existing uncertain sends first')
    native=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    originals={n:(TOOLS/n).read_bytes() if (TOOLS/n).exists() else None for n in NATIVE}
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    for name,data in originals.items():
        if data is not None:(backup/name).write_bytes(data)
    stopped=False;switched=False
    try:
        if (BASE/'current').resolve()!=previous:raise RuntimeError('Another release became active')
        command('systemctl','stop','whalegirl-linux');stopped=True
        command('systemctl','stop','whalegirl-native-reader')
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as live:
            with sqlite3.connect(backup/'state.sqlite3') as saved:live.backup(saved)
        for name in NATIVE:install(TOOLS/name,contents['deploy/'+name])
        command('systemctl','start','whalegirl-native-reader')
        for _ in range(40):
            try:
                raw=command('docker','exec',CONTAINER,'python3','-c',
                    "import sys;sys.path.insert(0,'/data/native-tools');from chat_select_linux import native_read;import json;v=native_read('/moments');print(json.dumps({'friends':len(v.get('friends',[]))}))",timeout=12)
                contact_count=json.loads(raw)['friends'];break
            except Exception:time.sleep(.25)
        else:raise RuntimeError('Native reader did not become ready')
        verify=json.loads(command('docker','exec','-u','root',CONTAINER,'python3','/data/native-tools/verify_native_delivery.py',timeout=75))
        (backup/'controls-verification.json').write_text(json.dumps(verify,indent=2))
        pending=BASE/('current.native-delivery-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current');switched=True
        command('systemctl','start','whalegirl-linux')
        for _ in range(50):
            try:
                state=api('state')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except Exception:pass
            time.sleep(.3)
        else:raise RuntimeError('Core did not become ready')
        after=api('config');after.pop('api_key_configured',None)
        if after!=original:raise RuntimeError('Existing configuration changed')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('WeChat PID changed')
        selector_hash=command('docker','exec',CONTAINER,'sha256sum','/opt/tools/chat-select.py').split()[0]
        if selector_hash!=hashlib.sha256(contents['deploy/chat_select_linux.py']).hexdigest():raise RuntimeError('Selector copy is stale')
        result={'release':str(stage),'previous':str(previous),'backup':str(backup),'native_pid':native,
            'wechat':state['gateway']['status'],'config_preserved':True,'friend_names':contact_count,
            'controls':verify,'files':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()},'at':time.time()}
        (ASSETS/'native-delivery-deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        if stopped:
            command('systemctl','stop','whalegirl-linux');command('systemctl','stop','whalegirl-native-reader')
            for name,data in originals.items():
                if data is not None:install(TOOLS/name,data)
            if switched:
                pending=BASE/('current.native-delivery-rollback-'+stamp);pending.symlink_to(previous,target_is_directory=True)
                os.replace(pending,BASE/'current')
            command('systemctl','start','whalegirl-native-reader');command('systemctl','start','whalegirl-linux')
        raise


if __name__=='__main__':deploy()

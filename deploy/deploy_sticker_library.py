"""Upgrade reversible sticker management and native collection; retain native WeChat."""
import argparse
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
DATA=Path('/var/lib/whalegirl-linux/core');REPORT=ASSETS/'sticker-library-stage.json'
CODE=('settings.py','stickers.py','sticker_library.py','runtime.py','gateway.py','app.py','console.js','web.html','sticker-library.js')
EXPECTED={'whalegirl_linux/'+name for name in CODE}|{'tests/test_sticker_library.py','deploy/deploy_sticker_library.py','deploy/chat_select_linux.py'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=10) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=25).stdout


def stage_release():
    stamp=time.strftime('%Y%m%d-%H%M%S');previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('sticker-library-'+stamp);backup=ASSETS/('sticker-library-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'sticker-library-20261021.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=EXPECTED or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive entries')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    for pattern in ('test_sticker_library.py','test_chat_habits.py'):
        result=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests','-p',pattern,'-v'],
            cwd=stage,capture_output=True,text=True,timeout=35)
        (backup/(pattern+'.log')).write_text(result.stdout+result.stderr)
        print((result.stdout+result.stderr)[-5500:],flush=True)
        if result.returncode:raise RuntimeError('Focused checks failed; live release unchanged')
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for target in [stage,*stage.rglob('*')]:
        if not target.is_symlink():os.chown(target,0,group);target.chmod(0o750 if target.is_dir() else 0o640)
    report={'stage':str(stage),'previous':str(previous),'backup':str(backup),'stamp':stamp,
        'checks':'passed','hashes':{n:hashlib.sha256(c).hexdigest() for n,c in contents.items()}}
    REPORT.write_text(json.dumps(report,indent=2));print(json.dumps({'stage':str(stage),'checks':'passed'}),flush=True)


def activate():
    report=json.loads(REPORT.read_text());stage=Path(report['stage']);previous=Path(report['previous'])
    backup=Path(report['backup']);current=BASE/'current';stamp=report['stamp']
    if current.resolve()!=previous:raise RuntimeError('Live release changed after staging')
    original=api('config');original.pop('api_key_configured',None)
    state=api('state')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):
        raise RuntimeError('Resolve uncertain send before upgrade')
    if state['gateway']['status']!='logged_in':raise RuntimeError('WeChat is offline')
    native_before=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat').strip()
    (backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chmod(backup/'settings.json',0o600)
    api('config',{'settings':{**original,'enabled':False}})
    stopped=False;native_changed=False
    native=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/chat_select_linux.py')
    try:
        for _ in range(150):
            state=api('state')
            if not state.get('processing'):break
            time.sleep(.2)
        else:raise RuntimeError('Active operation did not finish')
        if state.get('counts',{}).get('uncertain'):raise RuntimeError('Uncertain send appeared')
        command('systemctl','stop','whalegirl-linux');stopped=True
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            with sqlite3.connect(backup/'state.sqlite3') as saved:db.backup(saved)
        shutil.copy2(native,backup/'chat_select_linux.py')
        command('systemctl','stop','whalegirl-native-reader');native_changed=True
        native.write_bytes((stage/'deploy/chat_select_linux.py').read_bytes());native.chmod(0o640)
        command('systemctl','start','whalegirl-native-reader')
        pending=BASE/('current.sticker-library-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,current)
        command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                state=api('state')
                if state['gateway']['status']=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('New core unavailable or login changed')
        live=api('config');live.pop('api_key_configured',None)
        if any(live.get(k)!=v for k,v in original.items() if k!='enabled'):
            raise RuntimeError('Existing settings changed')
        live['enabled']=original['enabled'];api('config',{'settings':live})
        library=api('sticker-library');state=api('state')
        native_after=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat').strip()
        if native_before!=native_after:raise RuntimeError('Native WeChat PID changed unexpectedly')
        result={'release':str(stage),'backup':str(backup),'checks':'passed','native_pid_unchanged':True,
            'native_pid':native_after,'gateway_status':state['gateway']['status'],'enabled':state['enabled'],
            'self_manage_enabled':live['sticker_self_manage_enabled'],'active_stickers':library['active'],'candidates':len(library['candidates']),
            'authorized_chats':len(live['allowed_chat_ids']),'context_messages':live['context_messages'],
            'paused':state.get('paused_reason'),'last_error':state.get('last_error')}
        (ASSETS/'sticker-library-deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            rollback=BASE/('current.sticker-library-rollback-'+stamp);rollback.symlink_to(previous,target_is_directory=True);os.replace(rollback,current)
            (DATA/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chmod(DATA/'settings.json',0o600)
            if native_changed:
                command('systemctl','stop','whalegirl-native-reader')
                native.write_bytes((backup/'chat_select_linux.py').read_bytes())
                command('systemctl','start','whalegirl-native-reader')
            command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':original})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['stage','activate']);args=parser.parse_args()
    stage_release() if args.operation=='stage' else activate()

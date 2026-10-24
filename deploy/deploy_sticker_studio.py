"""Overlay the sticker studio on the live release; preserve native sessions."""
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

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
FILES={'whalegirl_linux/'+name for name in (
    'settings.py','stickers.py','sticker_assets.py','sticker_library.py','runtime.py','app.py',
    'console.js','web.html','sticker-library.js','sticker-library.css')}
FILES|={'tests/test_sticker_library.py','tests/test_sticker_studio.py','STICKER_STUDIO.md','deploy/deploy_sticker_studio.py'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route, body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=20) as response:return json.load(response)


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=35).stdout.strip()


def idle():
    for _ in range(80):
        state=api('state');library=api('sticker-library');moments=api('moments')
        if (not state.get('processing') and not library.get('worker_busy') and
            not any(r['state']=='collecting' for r in library['candidates']) and
            not any(r['state'] in ('generating','sending') for r in moments['actions'])):
            return state
        time.sleep(.5)
    raise RuntimeError('Core is busy; defer the upgrade')


def deploy():
    stamp=time.strftime('%Y%m%d-%H%M%S')
    previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('sticker-studio-'+stamp)
    backup=ASSETS/('sticker-studio-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'sticker-studio-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected sticker studio archive')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (stage,*stage.rglob('*')):
        if not path.is_symlink():
            os.chown(path,0,group);path.chmod(0o750 if path.is_dir() else 0o640)
    original=api('config');original.pop('api_key_configured',None)
    state=api('state')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain'):
        raise RuntimeError('Resolve uncertain sends before deployment')
    if state.get('gateway',{}).get('status')!='logged_in':
        raise RuntimeError('WeChat is offline; preserve session for diagnosis')
    native_pid=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    reader_pid=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    for name in ('sticker-profiles.json','sticker-catalog.json'):
        if (DATA/name).exists():shutil.copy2(DATA/name,backup/name)
    stopped=False
    try:
        if (BASE/'current').resolve()!=previous:raise RuntimeError('Another release became active')
        api('config',{'settings':{'enabled':False}})
        idle()
        command('systemctl','stop','whalegirl-linux');stopped=True
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as live:
            with sqlite3.connect(backup/'state.sqlite3') as saved:live.backup(saved)
        pending=BASE/('current.sticker-studio-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current')
        command('systemctl','start','whalegirl-linux')
        for _ in range(70):
            try:
                state=api('state')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.3)
        else:raise RuntimeError('New core did not become ready')
        api('config',{'settings':{'enabled':original['enabled']}})
        live=api('config')
        if any(live.get(k)!=v for k,v in original.items()):raise RuntimeError('An existing setting changed')
        if command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')!=native_pid:
            raise RuntimeError('Native WeChat PID changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader_pid:
            raise RuntimeError('Native reader PID changed')
        library=api('sticker-library');state=api('state')
        result={'release':str(stage),'previous':str(previous),'backup':str(backup),'at':time.time(),
            'wechat':state.get('gateway',{}).get('status'),'enabled':state['enabled'],
            'existing_settings_preserved':True,'native_pid_preserved':True,'reader_pid_preserved':True,
            'active_stickers':library['active'],'candidate_count':len(library['candidates']),
            'scoped_permissions':True,'owned_cache_cleanup':live['sticker_cache_cleanup_enabled'],
            'hashes':{n:hashlib.sha256(c).hexdigest() for n,c in contents.items()}}
        (ASSETS/'sticker-studio-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k!='hashes'}),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            pending=BASE/('current.sticker-studio-rollback-'+stamp);pending.symlink_to(previous,target_is_directory=True)
            os.replace(pending,BASE/'current')
            shutil.copy2(backup/'settings.json',DATA/'settings.json')
            command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':{'enabled':original['enabled']}})
        raise


if __name__=='__main__':deploy()

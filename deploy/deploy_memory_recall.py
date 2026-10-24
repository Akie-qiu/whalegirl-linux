"""Deploy the memory recall repair while preserving the native login session."""
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
FILES={'whalegirl_linux/memory.py','whalegirl_linux/runtime.py','whalegirl_linux/console.js',
       'tests/test_memory_recall.py','MEMORY_RECALL.md','deploy/deploy_memory_recall.py'}
EXPECTED={
    'whalegirl_linux/memory.py':'5e28ab1fdee7e315b6ced14c37a9bc0d87dbd670deaa76a53861b27ebf3b4a87',
    'whalegirl_linux/runtime.py':'95ec9fdede2146b5be6ea5a678bb70dc7ebe8d1ec39085a45a7b409b1412f1aa',
    'whalegirl_linux/console.js':'99971bfdb2d44d2b104e9f98e414835804be4fc59aa844be3b77ec5aa657fd60',
}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=20) as response:return json.load(response)


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=30).stdout.strip()


def idle():
    for _ in range(80):
        state=api('state');library=api('sticker-library');moments=api('moments')
        if (not state.get('processing') and not library.get('worker_busy') and
            not any(r['state']=='collecting' for r in library['candidates']) and
            not any(r['state'] in ('generating','sending') for r in moments['actions'])):
            return
        time.sleep(.5)
    raise RuntimeError('Core is busy; defer deployment')


def deploy():
    stamp=time.strftime('%Y%m%d-%H%M%S')
    previous=(BASE/'current').resolve()
    for name,digest in EXPECTED.items():
        if hashlib.sha256((previous/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Live source changed: '+name)
    with tarfile.open(ASSETS/'memory-recall-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected memory repair archive')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    original=api('config');original.pop('api_key_configured',None)
    state=api('state');moments=api('moments')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain') or moments.get('uncertain'):
        raise RuntimeError('Resolve uncertain sends before deploying')
    if state.get('gateway',{}).get('status')!='logged_in':
        raise RuntimeError('WeChat is offline; preserve session for diagnosis')
    native=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    stage=BASE/'releases'/('memory-recall-'+stamp)
    backup=ASSETS/('memory-recall-before-'+stamp);backup.mkdir(mode=0o700)
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (stage,*stage.rglob('*')):
        if not path.is_symlink():
            os.chown(path,0,group);path.chmod(0o750 if path.is_dir() else 0o640)
    stopped=False;switched=False;configured=False
    try:
        idle()
        if (BASE/'current').resolve()!=previous:raise RuntimeError('Another release became active')
        api('config',{'settings':{'enabled':False}});configured=True
        idle()
        command('systemctl','stop','whalegirl-linux');stopped=True
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as live:
            with sqlite3.connect(backup/'state.sqlite3') as saved:live.backup(saved)
        pending=BASE/('current.memory-recall-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current');switched=True
        command('systemctl','start','whalegirl-linux')
        for _ in range(60):
            try:
                state=api('state')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.3)
        else:raise RuntimeError('Updated core did not become ready')
        api('config',{'settings':{'enabled':original['enabled']}});configured=False
        live_config=api('config')
        if any(live_config.get(k)!=v for k,v in original.items()):
            raise RuntimeError('Existing configuration changed')
        if command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')!=native:
            raise RuntimeError('Native WeChat PID changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:
            raise RuntimeError('Native reader PID changed')
        state=api('state')
        result={'release':str(stage),'previous':str(previous),'backup':str(backup),'at':time.time(),
            'wechat':state['gateway']['status'],'enabled':state['enabled'],
            'native_pid':native,'reader_pid':reader,'config_preserved':True,
            'files':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()},
            'error':state.get('last_error',''),'pause':state.get('paused_reason','')}
        (ASSETS/'memory-recall-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except Exception:
        if switched:
            command('systemctl','stop','whalegirl-linux')
            rollback=BASE/('current.memory-recall-rollback-'+stamp)
            rollback.symlink_to(previous,target_is_directory=True)
            os.replace(rollback,BASE/'current')
        if stopped:command('systemctl','start','whalegirl-linux')
        if configured or switched:
            for _ in range(40):
                try:
                    api('config',{'settings':{'enabled':original['enabled']}});break
                except (OSError,ValueError):time.sleep(.3)
        raise


if __name__=='__main__':deploy()

"""Deploy persistent token accounting with the native WeChat session intact."""
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

BASE=Path('/opt/whalegirl-linux');ASSETS=Path('/opt/whalegirl-linux-assets');DATA=Path('/var/lib/whalegirl-linux/core')
FILES={'whalegirl_linux/token_usage.py','whalegirl_linux/token-usage.js','whalegirl_linux/upstream/ai_engine.py',
    'whalegirl_linux/runtime.py','whalegirl_linux/app.py','whalegirl_linux/search.py',
    'whalegirl_linux/console.js','whalegirl_linux/web.html','tests/test_token_usage.py','TOKEN_USAGE.md','deploy/deploy_token_usage.py'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=(DATA/'admin.token').read_text().strip()


def api(route):
    return json.load(opener.open(urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token}),timeout=15))


def command(*args,timeout=35):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Command failed: '+args[0]+'; '+result.stderr[-1800:])
    return result.stdout.strip()


def idle():
    for _ in range(80):
        s,m,lib=api('state'),api('moments'),api('sticker-library')
        if not s.get('processing') and not lib.get('worker_busy') and not any(r['state'] in ('generating','sending') for r in m['actions']) and not any(r['state']=='collecting' for r in lib['candidates']):return
        time.sleep(.5)
    raise RuntimeError('Core busy; defer deployment')


def deploy():
    baseline=json.loads((ASSETS/'token-usage-baseline-20261022.json').read_text());previous=(BASE/'current').resolve()
    if str(previous)!=baseline['release']:raise RuntimeError('Current release changed')
    for name,expected in baseline['files'].items():
        if hashlib.sha256((previous/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Live source changed: '+name)
    with tarfile.open(ASSETS/'token-usage-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):raise RuntimeError('Unexpected archive')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    stamp=time.strftime('%Y%m%d-%H%M%S');stage=BASE/'releases'/('token-usage-'+stamp);backup=ASSETS/('token-usage-before-'+stamp)
    backup.mkdir(mode=0o700);shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (stage,*stage.rglob('*')):
        if not path.is_symlink():os.chown(path,0,group);path.chmod(0o750 if path.is_dir() else 0o640)
    py='/opt/whalegirl-linux/venv/bin/python'
    for pattern in ('test_token_usage.py','test_search.py'):
        result=subprocess.run([py,'-m','unittest','discover','-s','tests','-p',pattern,'-q'],cwd=stage,capture_output=True,text=True,timeout=30)
        if result.returncode:raise RuntimeError('Focused test failed: '+pattern+'; '+result.stderr[-2500:])
    idle();state=api('state');original=api('config');original.pop('api_key_configured',None)
    if state.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain') or api('moments').get('uncertain'):
        raise RuntimeError('Resolve existing uncertain sends before deployment')
    native=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    shutil.copy2(DATA/'settings.json',backup/'settings.json');stopped=False;switched=False
    try:
        if (BASE/'current').resolve()!=previous:raise RuntimeError('Another release became active')
        command('systemctl','stop','whalegirl-linux');stopped=True
        with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as live:
            with sqlite3.connect(backup/'state.sqlite3') as saved:live.backup(saved)
        pending=BASE/('current.token-usage-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current');switched=True
        command('systemctl','start','whalegirl-linux')
        for _ in range(60):
            try:
                state=api('state');stats=api('token-usage')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except Exception:pass
            time.sleep(.3)
        else:raise RuntimeError('Updated core did not become ready')
        after=api('config');after.pop('api_key_configured',None)
        if after!=original:raise RuntimeError('Existing settings changed')
        if command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')!=native:raise RuntimeError('Native WeChat restarted')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Native reader restarted')
        for period in ('today','7d','30d','all'):
            value=api('token-usage?period='+period)
            if value.get('period')!=period or value['summary']['requests']<0:raise RuntimeError('Statistics API failed')
        result={'release':str(stage),'previous':str(previous),'backup':str(backup),'at':time.time(),
            'native_pid':native,'reader_pid':reader,'wechat':state['gateway']['status'],'enabled':state['enabled'],
            'config_preserved':True,'accounting_started_at':stats['started_at'],'requests':stats['summary']['requests'],
            'files':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()}}
        (ASSETS/'token-usage-deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        if switched:
            command('systemctl','stop','whalegirl-linux');pending=BASE/('current.token-usage-rollback-'+stamp)
            pending.symlink_to(previous,target_is_directory=True);os.replace(pending,BASE/'current')
        if stopped:command('systemctl','start','whalegirl-linux')
        raise


if __name__=='__main__':deploy()

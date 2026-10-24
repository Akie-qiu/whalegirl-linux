"""Publish role cards and their console UI; restart only the Python core."""
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
FILES={'whalegirl_linux/'+name for name in ('app.py','settings.py','personas.py','personas.js',
       'personas.css','web.html','console.js')}
FILES|={'tests/test_personas.py','PERSONAS.md','deploy/deploy_personas.py'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        data=None if body is None else json.dumps(body).encode())
    with opener.open(request,timeout=15) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=35).stdout.strip()


def idle():
    until=time.monotonic()+45
    while True:
        state=api('state')
        if not state.get('processing') and not any(state.get('counts',{}).get(k) for k in ('generating','sending')):
            return state
        if time.monotonic()>=until:raise RuntimeError('Core busy; deployment deferred without forced restart')
        time.sleep(.25)


def deploy():
    stamp=time.strftime('%Y%m%d-%H%M%S');previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('personas-'+stamp)
    backup=ASSETS/('personas-before-'+stamp);backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'personas-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected persona payload')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(exist_ok=True,parents=True);target.write_bytes(data)
    # One server smoke check validates routing and compatibility in its runtime.
    result=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest',
        'test_personas.PersonaApiTests.test_authenticated_save_preview_activate_and_legacy_prompt_compatibility','-v'],
        cwd=stage,env=dict(os.environ,PYTHONPATH=str(stage/'tests')+os.pathsep+str(stage)),
        capture_output=True,text=True,timeout=30)
    (backup/'api-smoke.log').write_text(result.stdout+result.stderr)
    print((result.stdout+result.stderr)[-2500:],flush=True)
    if result.returncode:raise RuntimeError('API smoke check failed; current release unchanged')
    gid=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in [stage,*stage.rglob('*')]:
        if not path.is_symlink():os.chown(path,0,gid);path.chmod(0o750 if path.is_dir() else 0o640)
    if (BASE/'current').resolve()!=previous:raise RuntimeError('Current release changed during staging')
    idle();original=api('config');original.pop('api_key_configured',None)
    prompt_before=api('prompt')['effective']
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    if (DATA/'persona-cards.json').exists():shutil.copy2(DATA/'persona-cards.json',backup/'persona-cards.json')
    native_pid=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    stopped=False
    try:
        api('config',{'settings':{'enabled':False}});idle()
        command('systemctl','stop','whalegirl-linux');stopped=True
        pending=BASE/('current.personas-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current');command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                if api('state').get('gateway',{}).get('status')=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('New core not ready')
        api('config',{'settings':{'enabled':original['enabled']}})
        live=api('config');roles=api('personas');state=api('state')
        if any(live.get(k)!=v for k,v in original.items()):raise RuntimeError('Existing setting changed unexpectedly')
        if roles['effective_prompt']!=prompt_before:raise RuntimeError('Current role prompt changed during migration')
        assets={}
        for path,needle in (('/','pc-heading-title'),('/personas.js','window.personas'),('/personas.css','.pc-workspace')):
            with opener.open('http://127.0.0.1:8092'+path,timeout=8) as response:
                if needle not in response.read().decode():raise RuntimeError('Expected role console asset missing')
                assets[path]=response.status
        if command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')!=native_pid:
            raise RuntimeError('Native WeChat PID changed')
        report={'release':str(stage),'previous':str(previous),'backup':str(backup),
            'at':time.time(),'wechat':state['gateway']['status'],'enabled':state['enabled'],
            'paused_reason':state.get('paused_reason'),'last_error':state.get('last_error'),
            'existing_settings_preserved':True,'current_prompt_preserved':True,
            'native_pid_preserved':True,'native_reader_restarted':False,
            'cards':len(roles['cards']),'custom_cards':sum(not r['builtin'] for r in roles['cards']),
            'console_assets':assets,'real_messages_sent':0,
            'prompt_hash':hashlib.sha256(prompt_before.encode()).hexdigest()}
        (ASSETS/'personas-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            shutil.copy2(backup/'settings.json',DATA/'settings.json')
            restore=BASE/('current.personas-rollback-'+stamp);restore.symlink_to(previous,target_is_directory=True)
            os.replace(restore,BASE/'current');command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':{'enabled':original['enabled']}})
        raise


if __name__=='__main__':deploy()

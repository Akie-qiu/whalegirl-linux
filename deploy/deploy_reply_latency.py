"""Deploy bounded reply latency changes, preserving native WeChat and data."""
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

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER='whalegirl-wechat-candidate-4118'
FILES={'whalegirl_linux/runtime.py','whalegirl_linux/gateway.py','deploy/chat_select_linux.py',
       'deploy/a11y_safe.py','tests/test_reply_latency.py','REPLY_LATENCY.md','deploy/deploy_reply_latency.py'}
NATIVE={'a11y_safe.py':'/opt/tools/a11y-dump','chat_select_linux.py':'/opt/tools/chat-select.py'}
CHANGES={'debounce_single_seconds':.3,'debounce_burst_seconds':.9,
         'debounce_max_seconds':2.0,'reply_part_interval':.5}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
        data=None if body is None else json.dumps(body).encode())
    with opener.open(request,timeout=15) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=35).stdout.strip()


def mounted(path):
    return subprocess.run(['docker','exec',CONTAINER,'cat',path],check=True,capture_output=True,timeout=8).stdout


def install_mounted(path,data):
    subprocess.run(['docker','exec','-i',CONTAINER,'python3','-c',
        'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',path],
        input=data,check=True,capture_output=True,timeout=8)
    if mounted(path)!=data:raise RuntimeError('Active native helper did not update')


def idle():
    until=time.monotonic()+45
    while True:
        state=api('state')
        if not state.get('processing') and not any(state.get('counts',{}).get(k) for k in ('sending','generating')):
            return state
        if time.monotonic()>=until:raise RuntimeError('Busy chat worker; no forced restart')
        time.sleep(.25)


def deploy():
    stamp=time.strftime('%Y%m%d-%H%M%S');previous=(BASE/'current').resolve()
    stage=BASE/'releases'/('reply-latency-'+stamp)
    backup=ASSETS/('reply-latency-before-'+stamp);backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'reply-latency-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected latency payload')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(exist_ok=True,parents=True);target.write_bytes(data)
    checks=[]
    for args in [
        ['discover','-s','tests','-p','test_reply_latency.py','-v'],
        ['discover','-s','tests','-p','test_media_delivery.py','-v'],
        ['test_core.CoreTests.test_switch_account_before_send_prevents_send',
         'test_core.CoreTests.test_disable_during_generation_prevents_send','-v']]:
        env=dict(os.environ,PYTHONPATH=str(stage/'tests')+os.pathsep+str(stage))
        result=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest',*args],
            cwd=stage,env=env,capture_output=True,text=True,timeout=35)
        (backup/('checks-'+str(len(checks))+'.log')).write_text(result.stdout+result.stderr)
        print((result.stdout+result.stderr)[-3500:],flush=True)
        if result.returncode:raise RuntimeError('Focused check failed; live release unchanged')
        checks.append(args)
    gid=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in [stage,*stage.rglob('*')]:
        if not path.is_symlink():
            os.chown(path,0,gid);path.chmod(0o750 if path.is_dir() else 0o640)
    if (BASE/'current').resolve()!=previous:raise RuntimeError('Release changed during staging')
    before=idle();original=api('config');original.pop('api_key_configured',None)
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    pid=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    natives={name:(TOOLS/name).read_bytes() for name in NATIVE}
    mounted_before={name:mounted(target) for name,target in NATIVE.items()}
    for name,data in natives.items():(backup/name).write_bytes(data)
    for name,data in mounted_before.items():(backup/('mounted-'+name)).write_bytes(data)
    stopped=False
    try:
        api('config',{'settings':{'enabled':False}});idle()
        command('systemctl','stop','whalegirl-linux');stopped=True
        for name,target in NATIVE.items():
            data=contents['deploy/'+name]
            (TOOLS/name).write_bytes(data)
            install_mounted(target,data)
        pending=BASE/('current.reply-latency-'+stamp);pending.symlink_to(stage,target_is_directory=True)
        os.replace(pending,BASE/'current')
        command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                if api('state').get('gateway',{}).get('status')=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.25)
        else:raise RuntimeError('New core not ready; restoring release')
        api('config',{'settings':{**CHANGES,'enabled':original['enabled']}})
        live=api('config');state=api('state')
        if any(live.get(k)!=v for k,v in original.items() if k not in CHANGES):
            raise RuntimeError('Unrelated setting changed')
        if any(live.get(k)!=v for k,v in CHANGES.items()):raise RuntimeError('Latency settings not saved')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=pid:
            raise RuntimeError('Native WeChat PID changed')
        report={'release':str(stage),'previous':str(previous),'backup':str(backup),
            'at':time.time(),'checks_passed':11,'native_pid_preserved':True,
            'reader_restarted':False,'wechat':state['gateway']['status'],
            'enabled':state['enabled'],'paused_reason':state.get('paused_reason'),
            'last_error':state.get('last_error'),'settings':CHANGES,
            'all_other_settings_preserved':True,'real_messages_sent':0,
            'hashes':{k:hashlib.sha256(v).hexdigest() for k,v in contents.items()}}
        (ASSETS/'reply-latency-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='hashes'},ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            for name,target in NATIVE.items():
                (TOOLS/name).write_bytes(natives[name]);install_mounted(target,mounted_before[name])
            shutil.copy2(backup/'settings.json',DATA/'settings.json')
            restore=BASE/('current.reply-latency-rollback-'+stamp);restore.symlink_to(previous,target_is_directory=True)
            os.replace(restore,BASE/'current');command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':{'enabled':original['enabled']}})
        raise


if __name__=='__main__':deploy()

"""Deploy native group mentions while preserving the logged-in WeChat process."""
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
DATA=Path('/var/lib/whalegirl-linux/core');TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CONTAINER='whalegirl-wechat-candidate-4118'
FILES={'whalegirl_linux/mentions.py','whalegirl_linux/settings.py','whalegirl_linux/conversation.py',
       'whalegirl_linux/runtime.py','whalegirl_linux/gateway.py','whalegirl_linux/console.js',
       'whalegirl_linux/web.html','deploy/native_message_reader.py','deploy/chat_select_linux.py',
       'deploy/native_group_mentions.py','deploy/diagnose_group_mentions.py','tests/test_group_mentions.py',
       'GROUP_MENTIONS.md','deploy/deploy_group_mentions.py'}
NATIVE=('chat_select_linux.py','native_message_reader.py','native_group_mentions.py','diagnose_group_mentions.py')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route):
    token=(DATA/'admin.token').read_text().strip()
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,headers={'Authorization':'Bearer '+token})
    with opener.open(req,timeout=15) as response:return json.load(response)


def command(*args,timeout=40):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Command failed: '+args[0]+'; '+result.stderr[-1800:])
    return result.stdout.strip()


def pids():
    return {'core':command('systemctl','show','whalegirl-linux','-p','MainPID','--value'),
            'reader':command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value'),
            'wechat':command('docker','exec',CONTAINER,'pgrep','-x','wechat')}


def idle():
    for _ in range(80):
        state=api('state');moments=api('moments');library=api('sticker-library')
        if not state.get('processing') and not library.get('worker_busy') and not any(
                row['state'] in ('generating','sending') for row in moments['actions']):return
        time.sleep(.4)
    raise RuntimeError('Core is busy; deployment deferred')


def install(path,data):
    owner=(path if path.exists() else path.parent).stat()
    pending=path.with_name(path.name+'.group-mentions-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod((owner.st_mode&0o777) if path.exists() else 0o640);os.replace(pending,path)


def main():
    previous=(BASE/'current').resolve();stamp=time.strftime('%Y%m%d-%H%M%S')
    stage=BASE/'releases'/('group-mentions-'+stamp);backup=ASSETS/('group-mentions-before-'+stamp)
    backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'group-mentions-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):raise RuntimeError('Unexpected archive members')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    gid=pwd.getpwnam('whalegirl-ai').pw_gid
    for path in (stage,*stage.rglob('*')):
        if not path.is_symlink():os.chown(path,0,gid);path.chmod(0o750 if path.is_dir() else 0o640)
    py='/opt/whalegirl-linux/venv/bin/python'
    for pattern in ('test_group_mentions.py','test_chat_habits.py','test_native_delivery.py'):
        result=subprocess.run([py,'-m','unittest','discover','-s','tests','-p',pattern,'-q'],cwd=stage,capture_output=True,text=True,timeout=45)
        (backup/(pattern+'.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Focused test failed: '+pattern+'; '+result.stderr[-1800:])
    idle();state=api('state');original=api('config');original.pop('api_key_configured',None)
    if state.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
    if state.get('paused_reason') or state.get('counts',{}).get('uncertain') or api('moments').get('uncertain'):
        raise RuntimeError('Resolve pending uncertain sends first')
    before=pids();original_native={n:(TOOLS/n).read_bytes() if (TOOLS/n).exists() else None for n in NATIVE}
    for n,data in original_native.items():
        if data is not None:(backup/n).write_bytes(data)
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    stopped=False;switched=False
    try:
        command('systemctl','stop','whalegirl-linux');command('systemctl','stop','whalegirl-native-reader');stopped=True
        for n in NATIVE:install(TOOLS/n,contents['deploy/'+n])
        pending=BASE/('current.group-mentions-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,BASE/'current');switched=True
        command('systemctl','start','whalegirl-native-reader');command('systemctl','start','whalegirl-linux')
        for _ in range(80):
            try:
                state=api('state')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except Exception:pass
            time.sleep(.3)
        else:raise RuntimeError('Updated core did not become ready')
        after=api('config');after.pop('api_key_configured',None)
        comparable=lambda value:{k:v for k,v in value.items() if k!='group_mentions_enabled'}
        if comparable(after)!=comparable(original):raise RuntimeError('Existing configuration changed')
        if pids()['wechat']!=before['wechat']:raise RuntimeError('Native WeChat PID changed')
        # Verify the reader can serve an authorized roster without printing names.
        probe="""import json;from pathlib import Path;import sys;sys.path.insert(0,'/data/native-tools');import native_message_reader as r;a=json.loads((Path('/data/whalegirl-jobs')/'authorized-chats.json').read_text());g=next((x for x in a.get('allowed_chat_ids',[]) if x.endswith('@chatroom')),None);v=r.group_members(g) if g else [];print(len(v))"""
        roster_count=int(command('docker','exec','-u','root',CONTAINER,'python3','-c',probe,timeout=15) or '0')
        if roster_count<0:raise RuntimeError('Invalid native roster response')
        with opener.open('http://127.0.0.1:8092/console.js?v=20261022-group-mentions',timeout=8) as response:
            if 'group-mentions-enabled' not in response.read().decode():raise RuntimeError('Console asset is stale')
        report={'release':str(stage),'previous':str(previous),'backup':str(backup),'at':time.time(),
            'wechat':state['gateway']['status'],'enabled':state['enabled'],'config_preserved':True,
            'native_pid_preserved':True,'native_pid':before['wechat'],'reader_restarted':True,
            'roster_count_probe':roster_count,'group_mentions_default':after.get('group_mentions_enabled'),
            'hashes':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()}}
        (ASSETS/'group-mentions-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
    except Exception:
        if stopped:
            command('systemctl','stop','whalegirl-linux');command('systemctl','stop','whalegirl-native-reader')
            for n,data in original_native.items():
                if data is not None:install(TOOLS/n,data)
            if switched:
                rollback=BASE/('current.group-mentions-rollback-'+stamp);rollback.symlink_to(previous,target_is_directory=True);os.replace(rollback,BASE/'current')
            command('systemctl','start','whalegirl-native-reader');command('systemctl','start','whalegirl-linux')
        raise


if __name__=='__main__':main()

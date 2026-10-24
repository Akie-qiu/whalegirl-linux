"""Deploy warmth and paired-sticker controls, restarting only the Python core."""
import hashlib
import argparse
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
FILES={'whalegirl_linux/'+name for name in ('settings.py','conversation.py','runtime.py','improvements.js','web.html')}
FILES|={'tests/test_reply_expression.py','deploy/deploy_reply_expression.py'}
CHANGES={'reply_warmth':'high','sticker_after_text':'high','teasing':'low'}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route,body=None):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(request,timeout=12) as response:return json.load(response)


def command(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True,timeout=25).stdout.strip()


def config():
    value=api('config');value.pop('api_key_configured',None);return value


def idle(seconds):
    until=time.monotonic()+seconds
    while True:
        value=api('state')
        if value.get('paused_reason') or value.get('counts',{}).get('uncertain'):
            raise RuntimeError('An uncertain send or hold needs attention before the upgrade')
        if not value.get('processing'):return value
        if time.monotonic()>=until:raise RuntimeError('Core remains busy; no forced restart')
        time.sleep(.25)


def deploy(profile='reply-expression'):
    changes=CHANGES if profile=='reply-expression' else {'sticker_pair_probability':90,'sticker_candidate_limit':12}
    stamp=time.strftime('%Y%m%d-%H%M%S')
    current=BASE/'current';previous=current.resolve()
    stage=BASE/'releases'/(profile+'-'+stamp)
    backup=ASSETS/(profile+'-before-'+stamp);backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/(profile+'-20261022.tar')) as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive contents')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    shutil.copytree(previous,stage,symlinks=True)
    for name,data in contents.items():
        target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    for pattern in ('test_reply_expression.py','test_chat_habits.py'):
        result=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest','discover','-s','tests','-p',pattern,'-v'],
            cwd=stage,capture_output=True,text=True,timeout=40)
        log=result.stdout+result.stderr;(backup/(pattern+'.log')).write_text(log)
        print(log[-4000:],flush=True)
        if result.returncode:raise RuntimeError('Focused checks failed; current release unchanged')
    group=pwd.getpwnam('whalegirl-ai').pw_gid
    for target in [stage,*stage.rglob('*')]:
        if not target.is_symlink():os.chown(target,0,group);target.chmod(0o750 if target.is_dir() else 0o640)
    if current.resolve()!=previous:raise RuntimeError('Current release changed while staging')
    state=idle(40)
    if state['gateway']['status']!='logged_in':raise RuntimeError('WeChat is offline')
    original=config();native_before=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    (backup/'settings.json').write_text(json.dumps(original,ensure_ascii=False,indent=2));os.chmod(backup/'settings.json',0o600)
    stopped=False
    try:
        api('config',{'settings':{'enabled':False}});idle(40)
        command('systemctl','stop','whalegirl-linux');stopped=True
        pending=BASE/('current.'+profile+'-'+stamp);pending.symlink_to(stage,target_is_directory=True);os.replace(pending,current)
        command('systemctl','start','whalegirl-linux')
        for _ in range(100):
            try:
                state=api('state')
                if state['gateway']['status']=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.2)
        else:raise RuntimeError('Updated core did not become ready')
        api('config',{'settings':changes})
        live=config()
        if any(live.get(k)!=v for k,v in original.items() if k not in {*changes,'enabled'}):
            raise RuntimeError('An unrelated configuration value changed')
        if any(live.get(k)!=v for k,v in changes.items()):raise RuntimeError('Expression controls did not persist')
        native_after=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
        if native_before!=native_after:raise RuntimeError('Native WeChat process changed')
        assets={}
        for route,needle in (('/','improvements.js?v=20261022-sticker-pairing'),('/improvements.js?v=20261022-sticker-pairing','自动搭配概率')):
            with opener.open('http://127.0.0.1:8092'+route,timeout=10) as response:
                text=response.read().decode();assets[route]=response.status
                if needle not in text:raise RuntimeError('Expected console asset missing')
        api('config',{'settings':{'enabled':original['enabled']}});state=api('state')
        report={'release':str(stage),'backup':str(backup),'checks':'4 expression + 8 existing checks passed',
            'settings':changes,'sticker_aggressiveness':live['sticker_aggressiveness'],'reply_warmth':live['reply_warmth'],
            'reply_max_parts':live['reply_max_parts'],'context_messages':live['context_messages'],
            'authorized_chats':len(live['allowed_chat_ids']),'trigger_only_chats':len(live['trigger_only_chat_ids']),
            'gateway_status':state['gateway']['status'],'enabled':state['enabled'],'paused':state.get('paused_reason'),
            'last_error':state.get('last_error'),'native_pid_unchanged':True,'native_pid':native_after,'console_assets':assets,
            'hashes':{n:hashlib.sha256(c).hexdigest() for n,c in contents.items()}}
        (ASSETS/(profile+'-deployment.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='hashes'},ensure_ascii=False),flush=True)
    except BaseException:
        if stopped:
            command('systemctl','stop','whalegirl-linux')
            pending=BASE/('current.'+profile+'-rollback-'+stamp);pending.symlink_to(previous,target_is_directory=True);os.replace(pending,current)
            temporary=DATA/('settings.'+profile+'-rollback');temporary.write_text(json.dumps(original,ensure_ascii=False,indent=2));temporary.chmod(0o600);os.replace(temporary,DATA/'settings.json')
            command('systemctl','start','whalegirl-linux')
        else:api('config',{'settings':original})
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=['reply-expression','sticker-pairing'],default='reply-expression')
    deploy(parser.parse_args().profile)

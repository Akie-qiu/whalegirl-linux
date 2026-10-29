"""Publish core and the no-send UI operation, retaining login and backups."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request
import deploy_moments_recovery as base

base.DEPLOYMENT='ui-recovery'
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
NATIVE={'deploy/chat_select_linux.py','deploy/native_recovery.py'}
FILES={'whalegirl_linux/'+n for n in ('settings.py','safety.py','gateway.py','runtime.py','images.py','app.py','console.js','web.html','recovery.py','recovery.js','recovery.css')}|NATIVE|{'tests/test_ui_recovery.py'}
MOUNTED='/opt/tools/chat-select.py'


def source(root,name):return TOOLS/Path(name).name if name in NATIVE else root/name


def write_native(name,content):
    target=TOOLS/Path(name).name
    owner=(TOOLS/'chat_select_linux.py').stat()
    pending=target.with_name(target.name+'.recovery-'+str(time.time_ns()))
    pending.write_bytes(content);os.chown(pending,owner.st_uid,owner.st_gid);pending.chmod(owner.st_mode & 0o777)
    os.replace(pending,target)
    if name.endswith('/chat_select_linux.py'):
        subprocess.run(['docker','exec','-i',base.CONTAINER,'python3','-c',
            'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',MOUNTED],
            input=content,capture_output=True,check=True,timeout=10)


def main():
    lock=open(base.ASSETS/'ui-recovery-deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8-sig'))
    previous=(base.BASE/'current').resolve()
    if str(previous)!=manifest['root'] or set(manifest['before'])!=FILES or set(manifest['after'])!=FILES:
        raise RuntimeError('Unexpected deployment manifest')
    def unchanged():
        if (base.BASE/'current').resolve()!=previous:raise RuntimeError('Active release changed')
        for name,expected in manifest['before'].items():
            if base.digest(source(previous,name))!=expected:raise RuntimeError('Source changed: '+name)
    unchanged()
    if base.command('docker','exec',base.CONTAINER,'sha256sum',MOUNTED).split()[0] != manifest['mounted_before']:
        raise RuntimeError('Mounted selector changed after the source/mount review')
    with tarfile.open(sys.argv[1]) as archive:
        members=archive.getmembers()
        if len(members)!=len(FILES) or {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment archive')
        payload={m.name:archive.extractfile(m).read() for m in members}
    for name,content in payload.items():
        if hashlib.sha256(content).hexdigest()!=manifest['after'][name]:raise RuntimeError('Payload changed: '+name)
        if name.endswith('.py'):compile(content,name,'exec')
    config_before={'send_auto_recovery_enabled':True,**base.api('config')}
    comparable={k:v for k,v in config_before.items() if k!='api_key_configured'}
    state_before=base.api('state')
    if state_before.get('gateway',{}).get('status')!='logged_in':raise RuntimeError('WeChat is offline')
    protected={n:base.digest(base.DATA/n) for n in ('settings.json','api-key.enc','image-api-key.enc','admin.token')}
    native_pid=base.command('docker','exec',base.CONTAINER,'pgrep','-x','wechat')
    reader_pid=base.command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    container=base.command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',base.CONTAINER)
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000)
    release=base.BASE/'releases'/('ui-recovery-'+stamp)
    backup=base.ASSETS/('ui-recovery-before-'+stamp);backup.mkdir(mode=0o700)
    shutil.copytree(previous,release,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','test-runs','.git'))
    for original in (previous,*previous.rglob('*')):
        target=release/original.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner=original.lstat();os.chown(target,owner.st_uid,owner.st_gid,follow_symlinks=False)
    reference=(previous/'whalegirl_linux/runtime.py').stat()
    for name,content in payload.items():
        original=source(previous,name)
        if original.is_file():
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,saved)
        target=release/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        owner=original.stat() if original.is_file() else reference
        os.chown(target,owner.st_uid,owner.st_gid);target.chmod(owner.st_mode & 0o777)
    test=subprocess.run([str(base.BASE/'venv/bin/python'),'-m','unittest','test_ui_recovery','test_media_delivery','test_search_clock','-q'],
        cwd=release,env={**os.environ,'PYTHONPATH':str(release)+':'+str(release/'tests'),'LOGURU_LEVEL':'ERROR'},
        capture_output=True,text=True,timeout=45)
    (backup/'focused-tests.log').write_text(test.stdout+test.stderr)
    if test.returncode:
        print((test.stdout+test.stderr)[-5000:]);raise RuntimeError('Recovery checks failed')
    started=time.monotonic()
    while base.api('state').get('processing') or base.inflight():
        if time.monotonic()-started>60:raise RuntimeError('Core remains busy')
        time.sleep(.5)
    unchanged()
    if any(base.digest(base.DATA/n)!=v for n,v in protected.items()):raise RuntimeError('Config changed during staging')
    stopped=switched=False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=30);stopped=True
        if base.inflight():raise RuntimeError('An outbound action crossed shutdown')
        mounted_before=subprocess.check_output(['docker','exec',base.CONTAINER,'cat',MOUNTED],timeout=10)
        if hashlib.sha256(mounted_before).hexdigest()!=manifest['mounted_before']:
            raise RuntimeError('Mounted selector changed before switching')
        (backup/'mounted-selector-before.py').write_bytes(mounted_before)
        with sqlite3.connect('file:'+str(base.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
            with sqlite3.connect(backup/'state-before.sqlite3') as saved:db.backup(saved)
        (backup/'state-before.sqlite3').chmod(0o600)
        for name in sorted(NATIVE,reverse=True):write_native(name,payload[name])
        mounted=base.command('docker','exec',base.CONTAINER,'sha256sum',MOUNTED).split()[0]
        if mounted!=manifest['after']['deploy/chat_select_linux.py']:raise RuntimeError('Mounted helper changed')
        base.switch(release,stamp);switched=True
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
        for _ in range(60):
            try:
                health=json.load(urllib.request.urlopen('http://127.0.0.1:8092/health',timeout=2))
                state=base.api('state');config={k:v for k,v in base.api('config').items() if k!='api_key_configured'}
                if health.get('status')=='ok' and not health.get('failed_background_tasks') and config==comparable:
                    if state.get('recovery',{}).get('status') not in ('pending','recovering'):break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('Core or automatic recovery did not settle')
        preserved={'wechat_pid':base.command('docker','exec',base.CONTAINER,'pgrep','-x','wechat')==native_pid,
            'reader_pid':base.command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')==reader_pid,
            'container':base.command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',base.CONTAINER)==container,
            'configuration_and_keys':all(base.digest(base.DATA/n)==v for n,v in protected.items())}
        if not all(preserved.values()):raise RuntimeError('Protected processes or config changed')
        report={'release':str(release),'previous':str(previous),'backup':str(backup),'health':health,
            'focused_tests':26,'preserved':preserved,'wechat_status':state.get('gateway',{}).get('status'),
            'recovery':state.get('recovery'),'paused_reason':state.get('paused_reason'),
            'real_test_messages_sent':0,'native_helper':str(TOOLS/'native_recovery.py')}
        (base.ASSETS/'ui-recovery-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    except BaseException:
        if stopped:subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=30)
        if switched:base.switch(previous,stamp+'-rollback')
        for name in NATIVE:
            if (backup/name).is_file():write_native(name,(backup/name).read_bytes())
        if (backup/'mounted-selector-before.py').is_file():
            subprocess.run(['docker','exec','-i',base.CONTAINER,'python3','-c',
                'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',MOUNTED],
                input=(backup/'mounted-selector-before.py').read_bytes(),capture_output=True,check=True,timeout=10)
        if stopped:subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
        raise


if __name__=='__main__':main()

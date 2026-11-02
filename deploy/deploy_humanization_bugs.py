"""Staged core-only release; preserve native WeChat, data and configuration."""
import ast
import fcntl
import hashlib
import json
import os
import pwd
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
CONTAINER='whalegirl-wechat-candidate-4118'
TAG='humanization-bugs-20261101'
PYTHON=str(BASE/'venv/bin/python')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
TESTS=('test_three_bug_regressions','test_picture_receipts','test_images','test_image_tools',
    'test_chat_pipeline_repair','test_tool_reply_guard','test_model_controls','test_responses',
    'test_experience_growth','test_interaction_continuity','test_autonomy','test_autonomous_growth',
    'test_learning','test_memory_hub','test_soul_upgrade_v5','test_soul_upgrade_regressions',
    'test_chat_control','test_proactive','test_proactive_tools','test_search_clock',
    'test_request_fingerprint','test_model_profiles','test_context_delivery','test_message_time_context',
    'test_tieba_tools','test_image_canvas','test_chat_world_images','test_token_usage')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def command(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with OPENER.open(request,timeout=10) as response:return json.load(response)


def db_read():
    return sqlite3.connect((DATA/'state.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=10)


def inflight():
    with db_read() as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'"))


def protected():
    return {p.name:digest(p) for p in DATA.iterdir() if p.is_file()
        and (p.suffix in ('.enc','.key','.token') or p.name in
             ('settings.json','model-profiles.json','persona-cards.json'))}


def native_state():
    return {'wechat_pid':command('docker','exec',CONTAINER,'pgrep','-x','wechat'),
        'reader_pid':command('systemctl','show','whalegirl-native-reader.service','-p','MainPID','--value'),
        'container':command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)}


def validate_manifest(manifest):
    previous=Path(manifest['root'])
    if (BASE/'current').resolve()!=previous:
        raise RuntimeError('Production release changed')
    if set(manifest['before'])!=set(manifest['after']):
        raise RuntimeError('Manifest file sets differ')
    for name,expected in manifest['before'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or not (name.startswith(('whalegirl_linux/','tests/')) or p.suffix=='.md'):
            raise RuntimeError('Unexpected release path')
        if digest(previous/name)!=expected:
            raise RuntimeError('Production source changed: '+name)
    return previous


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    path.chmod(0o600)


def prepare(folder):
    manifest=json.loads((folder/'manifest.json').read_text())
    previous=validate_manifest(manifest)
    if shutil.disk_usage(DATA).free < (DATA/'state.sqlite3').stat().st_size*2+300_000_000:
        raise RuntimeError('Insufficient free space for database backup and staged release')
    with tarfile.open(folder/'release.tar.gz') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=set(manifest['after']) or len(members)!=len(manifest['after']):
            raise RuntimeError('Unexpected archive entries')
        if any(not m.isfile() for m in members):raise RuntimeError('Non-file archive entry')
        payload={m.name:archive.extractfile(m).read() for m in members}
    for name,raw in payload.items():
        if hashlib.sha256(raw).hexdigest()!=manifest['after'][name]:raise RuntimeError('Payload checksum: '+name)
        if name.endswith('.py'):ast.parse(raw.decode('utf-8-sig'),filename=name)
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000)
    release=BASE/'releases'/(TAG+'-'+stamp)
    shutil.copytree(previous,release,symlinks=True,
        ignore=shutil.ignore_patterns('__pycache__','test-runs','.git'))
    for source in (previous,*previous.rglob('*')):
        if any(p in ('__pycache__','test-runs','.git') for p in source.relative_to(previous).parts):continue
        target=release/source.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner=source.lstat();os.chown(target,owner.st_uid,owner.st_gid,follow_symlinks=False)
    owner=(previous/'whalegirl_linux/console.js').stat()
    for name,raw in payload.items():
        target=release/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_symlink():raise RuntimeError('Refuse writing through staged symlink: '+name)
        target.write_bytes(raw)
        target.chmod(0o644);os.chown(target,owner.st_uid,owner.st_gid)
    # Test files create only synthetic state in this staged checkout.
    for dirname in ('tests','test-runs'):
        target=release/dirname;target.mkdir(exist_ok=True)
        service=pwd.getpwnam('whalegirl-ai')
        os.chown(target,service.pw_uid,service.pw_gid);target.chmod(0o755)
    env=os.environ.copy();env.update(PYTHONPATH=str(release/'tests'),PYTHONIOENCODING='utf-8',
        PYTHONDONTWRITEBYTECODE='1',LOGURU_LEVEL='ERROR',
        WHALEGIRL_DATA_DIR=str(release/'test-runs/isolated-default'))
    log=folder/'candidate-tests.log'
    with log.open('w') as handle:
        result=subprocess.run(['runuser','-u','whalegirl-ai','--',PYTHON,'-m','unittest',*TESTS,'-q'],
            cwd=release,env=env,stdout=handle,stderr=subprocess.STDOUT,timeout=240)
    text=log.read_text(errors='replace')
    if result.returncode:
        print(json.dumps({'stage':'candidate_tests_failed','release':str(release),
            'log':str(log),'tail':text[-6500:]},ensure_ascii=False),flush=True)
        raise RuntimeError('Staged regression failed; production has not switched')
    prepared={'release':str(release),'previous':str(previous),'manifest_sha256':digest(folder/'manifest.json'),
        'protected_hashes':protected(),'native_before':native_state(),
        'state_before':{'logged_in':api('api/state').get('gateway',{}).get('status')=='logged_in'},
        'test_summary':text[-1600:]}
    save(folder/'prepared.json',prepared)
    print(json.dumps({'stage':'prepared','release':str(release),'test_summary':text[-600:]},ensure_ascii=False),flush=True)


def switch_link(target,suffix):
    pending=BASE/('current.'+TAG+'-'+suffix)
    if pending.exists() or pending.is_symlink():raise RuntimeError('Pending symlink already exists')
    pending.symlink_to(target,target_is_directory=True)
    os.replace(pending,BASE/'current')


def deploy(folder):
    manifest=json.loads((folder/'manifest.json').read_text());previous=validate_manifest(manifest)
    prepared=json.loads((folder/'prepared.json').read_text());release=Path(prepared['release'])
    if digest(folder/'manifest.json')!=prepared['manifest_sha256']:raise RuntimeError('Manifest changed after tests')
    for name,expected in manifest['after'].items():
        if digest(release/name)!=expected:raise RuntimeError('Staged source changed: '+name)
    if protected()!=prepared['protected_hashes']:raise RuntimeError('Configuration changed during staging; recheck before switch')
    native_before=native_state()
    console_password_before=digest(DATA/'console-password.json')
    state_before=api('api/state');config_before=api('api/config')
    deadline=time.monotonic()+90
    while api('api/state').get('processing') or inflight() or api('api/images').get('busy'):
        if time.monotonic()>deadline:raise RuntimeError('Core still busy; production not switched')
        time.sleep(1)
    validate_manifest(manifest)
    backup=ASSETS/(TAG+'-before-'+release.name.removeprefix(TAG+'-'))
    backup.mkdir(mode=0o700)
    save(folder/'rollout-state.json',{'release':str(release),'previous':str(previous),'backup':str(backup),'stage':'stopping'})
    stopped=switched=False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=45);stopped=True
        if inflight():raise RuntimeError('Outbound task crossed shutdown boundary; restart previous core')
        if protected()!=prepared['protected_hashes']:raise RuntimeError('Configuration changed before backup')
        for name in (*prepared['protected_hashes'], 'console-password.json'):
            if not (DATA/name).is_file():continue
            shutil.copy2(DATA/name,backup/name);(backup/name).chmod(0o600)
        with db_read() as source,sqlite3.connect(backup/'state.sqlite3') as destination:
            source.backup(destination,pages=4096,sleep=.01)
            if destination.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Database backup check failed')
        (backup/'state.sqlite3').chmod(0o600)
        for name in manifest['before']:
            source=previous/name
            if source.is_file():
                target=backup/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        save(backup/'backup-manifest.json',{'previous':str(previous),'release':str(release),
            'database_bytes':(backup/'state.sqlite3').stat().st_size,'database_sha256':digest(backup/'state.sqlite3'),
            'protected_hashes':prepared['protected_hashes']})
        switch_link(release,str(time.time_ns()));switched=True
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=35)
        probe={}
        for _ in range(60):
            try:
                health=api('health');state=api('api/state');config=api('api/config')
                same_settings=all(config.get(k)==v for k,v in config_before.items())
                probe={'health':health.get('status'),'failed_tasks':health.get('failed_background_tasks'),
                    'wechat_status':state.get('gateway',{}).get('status'),'settings_preserved':same_settings}
                if health.get('status')=='ok' and health.get('background_tasks')==19 and not health.get('failed_background_tasks') and same_settings:break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('New core failed health/config check: '+json.dumps(probe))
        if state_before.get('gateway',{}).get('status')=='logged_in' and state.get('gateway',{}).get('status')!='logged_in':
            raise RuntimeError('Previously active WeChat login not preserved')
        native_after=native_state()
        preserved={k:native_before[k]==native_after[k] for k in native_before}
        preserved['configuration_files']=protected()==prepared['protected_hashes']
        preserved['console_password']=digest(DATA/'console-password.json')==console_password_before
        if not all(preserved.values()):raise RuntimeError('Protected component changed: '+json.dumps(preserved))
        with db_read() as db:
            schemas={name:[r[1] for r in db.execute('PRAGMA table_info('+name+')')]
                for name in ('picture_jobs','autonomy_intents','autonomy_events')}
        if 'diagnostics_json' not in schemas['picture_jobs'] or 'role_id' not in schemas['autonomy_intents']:
            raise RuntimeError('Expected additive migration missing')
        report={'stage':'deployed','release':str(release),'previous':str(previous),'backup':str(backup),
            'health':health,'wechat_status':probe['wechat_status'],'bot_enabled':state.get('enabled'),
            'paused':bool(state.get('paused_reason')),'preserved':preserved,
            'experience_enabled':config.get('experience_growth_enabled'),'autonomy_enabled':config.get('autonomy_enabled'),
            'hashes':manifest['after'],'real_test_messages_sent':0,'time':time.time()}
        save(folder/'deployment.json',report);save(ASSETS/(TAG+'-deployment.json'),report)
        save(folder/'rollout-state.json',{**report,'hashes':None})
        print(json.dumps({k:v for k,v in report.items() if k!='hashes'},ensure_ascii=False),flush=True)
    except BaseException:
        if switched:
            # Additive schemas remain. Previous code ignores added fields.
            # Preserve data and backup for review rather than overwriting it.
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=40)
            switch_link(previous,str(time.time_ns())+'-rollback')
        if stopped:subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=35)
        raise


if __name__=='__main__':
    folder=Path(sys.argv[1]).resolve()
    if folder.parent!=ASSETS or not folder.name.startswith(TAG):raise RuntimeError('Unexpected staging directory')
    with (ASSETS/(TAG+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if sys.argv[2]=='prepare':prepare(folder)
        elif sys.argv[2]=='deploy':deploy(folder)
        else:raise ValueError('Unknown deployment phase')

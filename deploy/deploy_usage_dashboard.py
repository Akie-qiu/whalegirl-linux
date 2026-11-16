"""Stage and atomically activate the usage dashboard over the live release.

Only this script's activation path stops the core. It never restarts WeChat,
changes configuration, restores a database, or removes a release/backup.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')
ASSETS=Path('/opt/whalegirl-linux-assets')
PYTHON=BASE/'venv/bin/python'
CONTAINER='whalegirl-wechat-candidate-4118'
IGNORE={'__pycache__','test-runs','artifacts','.git','.venv','venv','node_modules'}
EXTENSIONS={'.py','.js','.css','.html','.json','.md','.txt','.toml','.yml','.yaml'}
TESTS=('test_usage_dashboard','test_token_usage','test_proactive_tools','test_audio','test_content_filter',
       'test_studio_console','test_search','test_independent_search','test_life_chat_integration')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def sources(root):
    result={}
    for parent,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in IGNORE]
        for name in files:
            path=Path(parent)/name
            if path.suffix in EXTENSIONS and not path.is_symlink():
                result[path.relative_to(root).as_posix()]=digest(path)
    return result


def command(*args,timeout=30):
    return subprocess.check_output(args,text=True,timeout=timeout).strip()


def api(path):
    request=urllib.request.Request('http://127.0.0.1:8092'+path,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with OPENER.open(request,timeout=30) as response:return json.load(response)


def write_json(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    path.chmod(0o600)


def protected():
    return {p.name:digest(p) for p in DATA.iterdir() if p.is_file() and
            (p.suffix in ('.enc','.token') or p.name in ('settings.json','persona-cards.json'))}


def guard(manifest):
    previous=Path(manifest['root'])
    if (BASE/'current').resolve()!=previous:
        raise RuntimeError('Active release changed; preserving the newer release')
    actual=sources(previous)
    if actual!=manifest['source_guard']:
        changed=sorted(k for k in actual.keys()|manifest['source_guard'].keys()
                       if actual.get(k)!=manifest['source_guard'].get(k))
        raise RuntimeError('Source changed during staging: '+','.join(changed[:12]))
    for name,expected in manifest['before'].items():
        if digest(previous/name)!=expected:raise RuntimeError('Target changed: '+name)


def snapshot(path):
    start=time.monotonic()
    def progress(*_):
        if time.monotonic()-start>90:raise TimeoutError('Consistent snapshot exceeded 90s')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
        with sqlite3.connect(path) as target:
            source.backup(target,pages=512,progress=progress)
    path.chmod(0o600)
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Snapshot integrity failed')


def install(path,content,reference):
    owner=path.stat() if path.is_file() else reference
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_name(path.name+'.usage-new')
    if pending.exists():raise RuntimeError('Unexpected temporary source file')
    pending.write_bytes(content)
    os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod(owner.st_mode&0o777)
    os.replace(pending,path)  # Never write through a hard link to the old release.


def inflight():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        total=0
        for table in ('outbound_actions','picture_jobs','moments_actions','audio_jobs','growth_runs'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                total+=db.execute('SELECT count(*) FROM '+table+
                    " WHERE state IN ('sending','generating','planning','running','reading','reflecting')").fetchone()[0]
        return total


def stage(folder,manifest):
    guard(manifest)
    previous=Path(manifest['root']);release=Path(manifest['release'])
    reserve=sum(p.stat().st_size for p in DATA.glob('state.sqlite3*') if p.is_file())
    if shutil.disk_usage(DATA).free<reserve*2+512*1024**2:
        raise RuntimeError('Not enough space for validation, final backup and live WAL')
    archive=folder/'payload.tar.gz'
    with tarfile.open(archive) as tar:
        members=tar.getmembers()
        if (len(members)!=len(manifest['after']) or {m.name for m in members}!=set(manifest['after'])
                or not all(m.isfile() and not Path(m.name).is_absolute() and '..' not in Path(m.name).parts for m in members)):
            raise RuntimeError('Unexpected payload members')
        payload={m.name:tar.extractfile(m).read() for m in members}
    for name,content in payload.items():
        if hashlib.sha256(content).hexdigest()!=manifest['after'][name]:raise RuntimeError('Payload hash mismatch')
        if name.endswith('.py'):compile(content,name,'exec')
    print('Verified payload and live baseline',flush=True)
    shutil.copytree(previous,release,symlinks=True,copy_function=shutil.copy2,
                    ignore=lambda parent,names: [n for n in names if n in IGNORE])
    for parent,dirs,_ in os.walk(release,followlinks=False):
        relative=Path(parent).relative_to(release);source=previous/relative
        stat=source.stat();os.chown(parent,stat.st_uid,stat.st_gid)
    reference=(previous/'whalegirl_linux/runtime.py').stat()
    for name,content in payload.items():
        original=previous/name
        if original.is_file():
            saved=folder/'before'/name;saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(original,saved);saved.chmod(0o600)
        install(release/name,content,reference)
    for name,expected in manifest['source_guard'].items():
        if digest(release/name)!=manifest['after'].get(name,expected):raise RuntimeError('Unrelated source not preserved: '+name)
    private=folder/'private-config';private.mkdir(mode=0o700)
    for source in DATA.iterdir():
        if source.is_file() and source.suffix in ('.json','.enc','.token','.key') and source.stat().st_size<8*1024**2:
            target=private/source.name;shutil.copy2(source,target);target.chmod(0o600)
    with tarfile.open(folder/'source-before.tar.gz','w:gz') as tar:
        for name in manifest['source_guard']:tar.add(previous/name,arcname=name,recursive=False)
    (folder/'source-before.tar.gz').chmod(0o600)
    print('Staged candidate; previous source and private configuration preserved',flush=True)
    for name in manifest['after']:
        if name.endswith('.js'):command('node','--check',str(release/name))
    test=subprocess.run(['systemd-run','--scope','--quiet','--unit','whalegirl-usage-tests-'+str(time.time_ns()),
        '--property=CPUQuota=50%','--property=MemoryMax=256M','--property=MemorySwapMax=128M',
        str(PYTHON),'-m','unittest',*TESTS,'-q'],cwd=release,
        env={**os.environ,'PYTHONPATH':str(release)+':'+str(release/'tests'),'LOGURU_LEVEL':'ERROR'},
        capture_output=True,text=True,timeout=420,preexec_fn=lambda:os.nice(5))
    (folder/'linux-tests.log').write_text(test.stdout+test.stderr);(folder/'linux-tests.log').chmod(0o600)
    if test.returncode:raise RuntimeError('Linux regression failed; see protected linux-tests.log')
    match=re.search(r'Ran (\d+) tests',test.stdout+test.stderr)
    print('Linux regression passed: '+(match[1] if match else 'unknown'),flush=True)
    validation=folder/'schema-compat.sqlite3';snapshot(validation)
    env={**os.environ,'PYTHONPATH':str(release)}
    new_check="""from pathlib import Path
from types import SimpleNamespace
import json,sqlite3,sys
from whalegirl_linux.store import Store
from whalegirl_linux.token_usage import TokenLedger
from whalegirl_linux.usage_dashboard import UsageDashboard
p=Path(sys.argv[1]);s=Store(p);b=TokenLedger(s)
assert len(s.db.execute('PRAGMA table_info(model_token_usage)').fetchall())==15
assert s.db.execute(\"SELECT 1 FROM sqlite_master WHERE name='model_usage_context'\").fetchone()
account=json.loads((p.parent/'private-config/settings.json').read_text())['account_id']
r=SimpleNamespace(store=s,config=SimpleNamespace(settings=SimpleNamespace(account_id=account)),chats_cache=[],chat_labels={})
result=UsageDashboard(r).snapshot('all')
assert result['tokens']['summary']['requests']>0
assert 'sent' in result['speech']['summary']
s.close()
"""
    subprocess.run([str(PYTHON),'-c',new_check,str(validation)],env=env,cwd=release,check=True,timeout=90,capture_output=True)
    old_check="""from pathlib import Path
import sys,time
from whalegirl_linux.store import Store
from whalegirl_linux.token_usage import TokenLedger
s=Store(Path(sys.argv[1]));TokenLedger(s)
s.db.execute('INSERT INTO model_token_usage VALUES('+','.join('?' for _ in range(15))+')',
('deployment-schema-check','__validation__',time.time(),'offline','test','chat','success',1,1,1,2,None,None,None,None))
s.db.commit();s.close()
"""
    subprocess.run([str(PYTHON),'-c',old_check,str(validation)],env={**os.environ,'PYTHONPATH':str(previous)},
                   cwd=previous,check=True,timeout=60,capture_output=True)
    guard(manifest)
    report={'release':str(release),'previous':str(previous),'backup':str(folder),
            'linux_tests':int(match[1]) if match else None,'schema_new_and_previous_compatible':True,
            'unrelated_source_preserved':True,'production_database_modified':False}
    write_json(folder/'staged.json',report)
    print(json.dumps(report),flush=True)


def switch(target,label):
    pending=BASE/('current.usage-'+label)
    if pending.exists() or pending.is_symlink():raise RuntimeError('Unexpected pending release link')
    pending.symlink_to(target,target_is_directory=True)
    os.replace(pending,BASE/'current')


def seal(folder,manifest):
    """Separate any staging hard links so the rollback source stays independent."""
    report=json.loads((folder/'staged.json').read_text())
    guard(manifest)
    release=Path(manifest['release'])
    shared=[]
    for parent,dirs,files in os.walk(release,followlinks=False):
        dirs[:]=[n for n in dirs if n not in IGNORE]
        for name in files:
            path=Path(parent)/name
            if path.is_file() and not path.is_symlink() and path.stat().st_nlink>1:shared.append(path)
    total=sum(p.stat().st_size for p in shared)
    if shutil.disk_usage(DATA).free<total+(DATA/'state.sqlite3').stat().st_size+512*1024**2:
        raise RuntimeError('Insufficient space for independent rollback and final database backup')
    for path in shared:
        stat=path.stat();pending=path.with_name(path.name+'.usage-independent')
        if pending.exists():raise RuntimeError('Unexpected pending source copy')
        shutil.copy2(path,pending);os.chown(pending,stat.st_uid,stat.st_gid);os.replace(pending,path)
    guard(manifest)
    for name,value in manifest['source_guard'].items():
        if digest(release/name)!=manifest['after'].get(name,value):raise RuntimeError('Independent source copy mismatch')
    report.update(independent_source_files=True,detached_files=len(shared),detached_bytes=total)
    write_json(folder/'staged.json',report)
    print(json.dumps({'independent_source_files':True,'detached_files':len(shared),'detached_bytes':total}),flush=True)


def activate(folder,manifest):
    staged=json.loads((folder/'staged.json').read_text())
    if not staged.get('independent_source_files'):raise RuntimeError('Seal the independent candidate before activation')
    release=Path(manifest['release']);previous=Path(manifest['root'])
    guard(manifest)
    for name,value in manifest['after'].items():
        if digest(release/name)!=value:raise RuntimeError('Staged file changed: '+name)
    deadline=time.monotonic()+100
    while api('/api/state').get('processing') or inflight():
        if time.monotonic()>deadline:raise RuntimeError('Active work did not drain; production unchanged')
        time.sleep(1)
    guard(manifest)
    before=api('/api/state');settings=api('/api/config');secrets=protected()
    if before.get('gateway',{}).get('status')!='logged_in' or before.get('paused_reason'):
        raise RuntimeError('Resolve current WeChat state before deployment')
    native=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    container=command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)
    reserve=(DATA/'state.sqlite3').stat().st_size+512*1024**2
    if shutil.disk_usage(DATA).free<reserve:raise RuntimeError('Final backup would leave insufficient free space')
    stopped=switched=False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=40);stopped=True
        if inflight():raise RuntimeError('A send/generation crossed shutdown; restarting previous core')
        snapshot(folder/'state-before-activate.sqlite3')
        guard(manifest)
        if protected()!=secrets:raise RuntimeError('Private configuration changed while stopping')
        print('Consistent final database backup passed integrity check',flush=True)
        switch(release,str(time.time_ns()));switched=True
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=35)
        for _ in range(80):
            try:
                health=api('/health');state=api('/api/state')
                if (health.get('status')=='ok' and not health.get('failed_background_tasks')
                        and state.get('gateway',{}).get('status')=='logged_in'
                        and not state.get('paused_reason')):break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('New core did not become healthy')
        if api('/api/config')!=settings:raise RuntimeError('Configuration changed across deployment')
        if protected()!=secrets:raise RuntimeError('Credentials or role card changed across deployment')
        if command('docker','exec',CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('WeChat restarted')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Reader restarted')
        if command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)!=container:raise RuntimeError('Container changed')
        dashboard=api('/api/usage-dashboard?period=today&view=both')
        options=api('/api/usage-dashboard/options')
        if not all(k in dashboard for k in ('tokens','speech','timeline','pricing')):raise RuntimeError('Dashboard response incomplete')
        for path,name in [('/token-usage.js','whalegirl_linux/usage-dashboard.js'),
                          ('/usage-dashboard.css','whalegirl_linux/usage-dashboard.css'),
                          ('/studio.js','whalegirl_linux/studio.js'),('/studio.css','whalegirl_linux/studio.css')]:
            with OPENER.open('http://127.0.0.1:8092'+path,timeout=15) as response:
                if hashlib.sha256(response.read()).hexdigest()!=manifest['after'][name]:raise RuntimeError('Served asset mismatch')
        try:OPENER.open('http://127.0.0.1:8092/api/usage-dashboard',timeout=10)
        except urllib.error.HTTPError as error:
            if error.code!=401:raise
        else:raise RuntimeError('Dashboard authentication boundary missing')
        report={**staged,'activated':True,'health':health,'wechat':'logged_in',
                'production_database_modified':True,'production_data_reset':False,
                'credentials_and_persona_preserved':True,'wechat_reader_container_preserved':True,
                'statistics_api_verified':True,'served_assets_verified':True,'unauthenticated_status':401,
                'requests_today':dashboard['tokens']['summary']['requests'],
                'confirmed_messages_today':dashboard['speech']['summary']['sent'],
                'model_options':len(options['models']),'chat_options':len(options['chats']),
                'schema_additive_only':True,'free_bytes':shutil.disk_usage(DATA).free,
                'validation_messages_sent':0,'model_test_calls':0,'activated_at':time.time()}
        write_json(folder/'deployment.json',report)
        print(json.dumps(report),flush=True)
    except BaseException:
        if switched:
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=40)
            switch(previous,str(time.time_ns())+'-rollback')
        if stopped:subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=35)
        raise


def rollback(folder,manifest):
    if (BASE/'current').resolve()!=Path(manifest['release']):
        raise RuntimeError('A newer release is active; refusing to replace it')
    if sources(Path(manifest['root']))!=manifest['source_guard']:
        raise RuntimeError('Rollback baseline changed; preserve it and inspect source-before.tar.gz')
    subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=40)
    switch(Path(manifest['root']),str(time.time_ns())+'-manual-rollback')
    subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=35)
    print(json.dumps({'rolled_back':True,'database_restored':False,'current':manifest['root']}))


def main():
    mode=sys.argv[1];folder=Path(sys.argv[2]).resolve()
    if folder.parent!=ASSETS or not folder.name.startswith('usage-dashboard-'):raise RuntimeError('Unexpected deployment directory')
    lock=open(ASSETS/'usage-dashboard-deploy.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8-sig'))
    for name in ('root','release'):
        if Path(manifest[name]).resolve().parent!=(BASE/'releases').resolve():
            raise RuntimeError('Release path is outside the release directory')
    {'stage':stage,'seal':seal,'activate':activate,'rollback':rollback}[mode](folder,manifest)


if __name__=='__main__':main()

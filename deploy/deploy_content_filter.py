"""Deploy only provider-error handling, preserving every prompt and configuration."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
CONTAINER='whalegirl-wechat-candidate-4118'
FILES={'whalegirl_linux/upstream/ai_engine.py','whalegirl_linux/model_reply.py',
       'whalegirl_linux/runtime.py','tests/test_content_filter.py'}
TESTS=('test_content_filter','test_three_bug_regressions.LengthBudgetTests',
       'test_model_controls','test_request_fingerprint','test_model_format_guard',
       'test_chat_pipeline_repair.EmptyResponseTests')
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def snapshot(root):
    result={}
    for directory,names,files in os.walk(root,followlinks=False):
        names[:]=sorted(n for n in names if n not in {'__pycache__','.git','test-runs','.pytest_cache'})
        for name in [*names,*sorted(files)]:
            path=Path(directory)/name;relative=path.relative_to(root).as_posix()
            if path.is_symlink():result[relative]='symlink:'+os.readlink(path)
            elif path.is_file():
                with path.open('rb') as stream:
                    result[relative]=hashlib.file_digest(stream,'sha256').hexdigest()
    return result


def command(*args):
    return subprocess.check_output(args,text=True,timeout=25).strip()


def state():
    request=urllib.request.Request('http://127.0.0.1:8092/api/state',
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with OPENER.open(request,timeout=10) as response:return json.load(response)


def protected():
    names=('settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc',
           'api-key.enc','image-api-key.enc','admin.token','gateway.token','encryption.key',
           'bilibili-session.enc')
    return {name:hashlib.sha256((DATA/name).read_bytes()).hexdigest()
            for name in names if (DATA/name).is_file()}


def inflight():
    import sqlite3
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True,timeout=5) as db:
        return sum(db.execute('SELECT count(*) FROM '+table+" WHERE state IN ('sending','generating')").fetchone()[0]
                   for table in ('outbound_actions','picture_jobs','moments_actions'))


def switch(target,stamp):
    pending=BASE/('current.content-filter-'+stamp)
    if pending.exists() or pending.is_symlink():raise RuntimeError('Pending link exists')
    pending.symlink_to(target,target_is_directory=True)
    os.replace(pending,BASE/'current')


def main(directory):
    manifest=json.loads((directory/'manifest.json').read_text())
    previous=(BASE/'current').resolve()
    before=manifest['source_snapshot']
    if set(manifest['after'])!=FILES:raise RuntimeError('Unexpected patch files')
    if str(previous)!=manifest['root'] or snapshot(previous)!=before:
        raise RuntimeError('Production changed; preserving concurrent updates')
    expected={**before,**manifest['after']}
    for name,digest in manifest['after'].items():
        content=(directory/'payload'/name).read_bytes()
        if hashlib.sha256(content).hexdigest()!=digest:raise RuntimeError('Payload hash mismatch')
        compile(content,name,'exec')
    configuration=protected()
    free=shutil.disk_usage(DATA).free
    if free<650*1024**2:raise RuntimeError('Insufficient free storage; no live changes')
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000)
    release=BASE/'releases'/('content-filter-diagnostics-'+stamp)
    backup=ASSETS/('content-filter-before-'+stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(previous,release,symlinks=True,
        ignore=shutil.ignore_patterns('__pycache__','test-runs','.git','.pytest_cache'))
    for source in (previous,*previous.rglob('*')):
        target=release/source.relative_to(previous)
        if target.exists() or target.is_symlink():
            owner=source.lstat();os.chown(target,owner.st_uid,owner.st_gid,follow_symlinks=False)
    reference=(previous/'whalegirl_linux/runtime.py').stat()
    for name in FILES:
        source=previous/name
        if source.exists():
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,saved)
        target=release/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((directory/'payload'/name).read_bytes())
        owner=source.stat() if source.exists() else reference
        os.chown(target,owner.st_uid,owner.st_gid);target.chmod(owner.st_mode&0o777)
    (backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=True,indent=2))
    (release/'test-runs').mkdir(mode=0o700,exist_ok=True)
    tests=subprocess.run([str(BASE/'venv/bin/python'),'-m','unittest',*TESTS,'-q'],
        cwd=release,env={**os.environ,'PYTHONPATH':str(release)+':'+str(release/'tests'),
            'WHALEGIRL_DATA_DIR':str(release/'test-runs'),'PYTHONDONTWRITEBYTECODE':'1',
            'LOGURU_LEVEL':'ERROR'},capture_output=True,text=True,timeout=180)
    (backup/'tests.log').write_text(tests.stdout+tests.stderr)
    if tests.returncode:raise RuntimeError('Isolated candidate tests failed; see '+str(backup/'tests.log'))
    if snapshot(release)!=expected:raise RuntimeError('Unexpected candidate changes')
    if snapshot(previous)!=before or (BASE/'current').resolve()!=previous or protected()!=configuration:
        raise RuntimeError('Live code/configuration changed; preserving concurrent updates')
    deadline=time.monotonic()+55
    while True:
        current=state()
        if current.get('gateway',{}).get('status')!='logged_in':
            raise RuntimeError('WeChat is offline; live release unchanged')
        if not current.get('processing') and not inflight():break
        if time.monotonic()>deadline:raise RuntimeError('Core is busy; live release unchanged')
        time.sleep(2)
    wechat_pid=command('docker','exec',CONTAINER,'pgrep','-x','wechat')
    container=command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)
    stopped=False;switched=False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=30)
        stopped=True
        if snapshot(previous)!=before or (BASE/'current').resolve()!=previous or protected()!=configuration:
            raise RuntimeError('Concurrent update detected at switch; preserving it')
        if snapshot(release)!=expected:raise RuntimeError('Candidate changed before switch')
        switch(release,stamp);switched=True
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
        deadline=time.monotonic()+45
        while True:
            try:
                with OPENER.open('http://127.0.0.1:8092/health',timeout=2) as response:health=json.load(response)
                current=state()
                if (health.get('status')=='ok' and not health.get('failed_background_tasks')
                        and current.get('gateway',{}).get('status')=='logged_in'):break
            except (OSError,ValueError):pass
            if time.monotonic()>deadline:raise RuntimeError('Candidate health verification failed')
            time.sleep(1)
        preserved={
            'configuration_and_persona':protected()==configuration,
            'complete_candidate_source':snapshot(release)==expected,
            'previous_release_unchanged':snapshot(previous)==before,
            'wechat_process':command('docker','exec',CONTAINER,'pgrep','-x','wechat')==wechat_pid,
            'wechat_container':command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',CONTAINER)==container,
            'active_release':(BASE/'current').resolve()==release,
        }
        if not all(preserved.values()):raise RuntimeError('Runtime preservation check failed')
        report={'release':str(release),'previous':str(previous),'backup':str(backup),
            'health':health,'wechat_status':current.get('gateway',{}).get('status'),
            'send_paused':bool(current.get('paused_reason')),'preserved':preserved,
            'changed_files':sorted(FILES),'unchanged_entries':len(before)-len(FILES&before.keys()),
            'tests_tail':(tests.stdout+tests.stderr).splitlines()[-5:],
            'disk_free_before':free,'disk_free_after':shutil.disk_usage(DATA).free,
            'database_changed_by_deployment':False,'configuration_changed':False,
            'prompts_changed':False,'real_wechat_tests_sent':0,
            'provider_rejection_resolved':False,'checked_at':time.time()}
        (directory/'deployment.json').write_text(json.dumps(report,ensure_ascii=True,indent=2))
        print(json.dumps(report,ensure_ascii=True),flush=True)
    except BaseException:
        if switched and (BASE/'current').resolve()==release and snapshot(release)==expected:
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=30)
            switch(previous,stamp+'-rollback')
        if stopped:subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
        raise


if __name__=='__main__':
    directory=Path(sys.argv[1])
    with (ASSETS/'content-filter-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        main(directory)

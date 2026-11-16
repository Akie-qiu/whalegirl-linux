"""Report-driven Linux rollback for personality/life releases.

Defaults to a read-only plan. --execute stops only the Python core, changes the
current release symlink and selectively reverts recorded plugin settings. It
never restores a database snapshot or removes any release/data/backup.

Report schema:
  {"kind":"personality-life", "current":"/opt/whalegirl-linux/releases/...",
   "previous":"/opt/whalegirl-linux/releases/...", "settings_delta":{
      "growth_exploration":{"before_present":true,"before":35,
          "after_present":true,"after":45}}}
The two plugin-only setting keys are removed for old Settings(extra='forbid').
Other settings are preserved byte-for-byte in meaning, including Bilibili flags.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request


BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
BACKUPS=Path('/var/backups/whalegirl')
SERVICE='whalegirl-linux.service'
PLUGIN_SETTINGS={'life_autonomy_enabled','growth_mature_traits_chat_enabled'}
ALLOWED_DELTA=PLUGIN_SETTINGS|{'growth_exploration','growth_interval_minutes',
    'bilibili_interval_minutes','tieba_interval_minutes'}
BUSY_QUERIES={
    'outbound_actions':"state='sending'",
    'picture_jobs':"state IN ('generating','sending')",
    'moments_actions':"state='sending'",
    'growth_runs':"state IN ('planning','reading','reflecting')",
    'tieba_runs':"state='running'",
}


def checked_targets(report,base=BASE):
    if report.get('kind')!='personality-life':raise ValueError('Unexpected deployment report kind')
    releases=(base/'releases').resolve(strict=True)
    result=[]
    for name in ('current','previous'):
        raw=report.get(name)
        if not isinstance(raw,str) or not Path(raw).is_absolute():raise ValueError('Report requires absolute '+name)
        path=Path(raw)
        resolved=path.resolve(strict=True)
        if path!=resolved or resolved.parent!=releases or not resolved.is_dir():
            raise ValueError('Report release must be an actual direct child of releases')
        if not (resolved/'whalegirl_linux'/'runtime.py').is_file():raise ValueError('Release Runtime source is missing')
        result.append(resolved)
    if result[0]==result[1]:raise ValueError('Current and previous release must differ')
    return tuple(result)


def reverted_settings(current,delta):
    if not isinstance(current,dict) or not isinstance(delta,dict):raise ValueError('Invalid settings rollback data')
    result=dict(current);changed=[]
    for key,item in delta.items():
        if key not in ALLOWED_DELTA or not isinstance(item,dict):raise ValueError('Rollback delta contains an unapproved setting')
        if key in PLUGIN_SETTINGS:continue
        if not isinstance(item.get('before_present'),bool) or not isinstance(item.get('after_present'),bool):
            raise ValueError('Settings delta must specify presence before and after')
        if (key in current)!=item['after_present'] or (key in current and current[key]!=item.get('after')):
            raise ValueError('Settings changed after deployment; refusing to overwrite '+key)
        if item['before_present']:
            if 'before' not in item:raise ValueError('Missing previous setting value')
            result[key]=item['before']
        else:result.pop(key,None)
        changed.append(key)
    for key in sorted(PLUGIN_SETTINGS):
        if key in result:result.pop(key);changed.append(key)
    return result,sorted(set(changed))


def api(route):
    token=(DATA/'admin.token').read_text().strip()
    request=urllib.request.Request('http://127.0.0.1:8092/'+route,
        headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:
        return json.load(response)


def busy():
    with sqlite3.connect((DATA/'state.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return sum(db.execute('SELECT count(*) FROM '+name+' WHERE '+where).fetchone()[0]
            for name,where in BUSY_QUERIES.items() if name in tables)


def require_old_settings(old,values):
    # Validation runs in an isolated old source interpreter. Never put keys or
    # settings contents into command-line arguments, stdout or traceback logs.
    code="""import json,sys
sys.path.insert(0,sys.argv[1])
from whalegirl_linux.settings import Settings
try:Settings.model_validate(json.load(sys.stdin))
except Exception as exc:
    errors=exc.errors(include_input=False,include_url=False) if hasattr(exc,'errors') else []
    print(json.dumps({'valid':False,'error_type':type(exc).__name__,
        'unsupported_settings':[str(e.get('loc',('unknown',))[0]) for e in errors if e.get('type')=='extra_forbidden']}))
    sys.exit(1)
print(json.dumps({'valid':True}))
"""
    result=subprocess.run([sys.executable,'-I','-c',code,str(old)],input=json.dumps(values),
        capture_output=True,text=True,timeout=20)
    if result.returncode:
        try:detail=json.loads(result.stdout)
        except (ValueError,TypeError):detail={'error_type':'OldSettingsImportFailed'}
        raise ValueError('Old release settings validation failed: '+json.dumps(detail,ensure_ascii=False))


def write_settings(payload):
    temporary=DATA/('settings.personality-rollback-'+str(time.time_ns())+'.tmp')
    with temporary.open('xb') as output:
        os.chmod(temporary,0o600);output.write(payload);output.flush();os.fsync(output.fileno())
    os.replace(temporary,DATA/'settings.json')


def switch(target):
    temporary=BASE/('current.personality-rollback-'+str(time.time_ns()))
    temporary.symlink_to(target,target_is_directory=True)
    os.replace(temporary,BASE/'current')


def systemctl(action,*,check=True):
    subprocess.run(['systemctl',action,SERVICE],check=check,timeout=30,capture_output=True)


def await_health():
    for _ in range(40):
        try:
            health=api('health')
            if health.get('status')=='ok' and not health.get('failed_background_tasks'):return True
        except Exception:pass
        time.sleep(.5)
    return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--execute',action='store_true',help='Apply the validated rollback; default only prints a plan')
    args=parser.parse_args()
    if sys.platform!='linux':raise SystemExit('This deployment rollback runs only on Linux; use the PowerShell SSH wrapper')
    import fcntl
    report=json.loads(args.report.read_text())
    candidate,previous=checked_targets(report)
    current=(BASE/'current').resolve(strict=True)
    if current==previous:
        require_old_settings(previous,json.loads((DATA/'settings.json').read_bytes()))
        print(json.dumps({'already_restored':True,'release':str(previous),'settings_validated':True}));return
    if current!=candidate:raise SystemExit('Active release changed; nothing switched')
    settings_before=(DATA/'settings.json').read_bytes()
    settings_after,changed=reverted_settings(json.loads(settings_before),report.get('settings_delta',{}))
    require_old_settings(previous,settings_after)
    plan={'candidate':str(candidate),'previous':str(previous),'changed_setting_keys':changed,
        'database_restored':False,'data_deleted':False,'wechat_container_restarted':False,'execute':args.execute}
    if not args.execute:print(json.dumps(plan));return
    with (ASSETS/'personality-life-deploy.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        started=time.monotonic()
        while True:
            try:processing=bool(api('api/state').get('processing'))
            except Exception:processing=False
            if not processing and not busy():break
            if time.monotonic()-started>60:raise SystemExit('Work is still in progress; retry when idle')
            time.sleep(1)
        if (BASE/'current').resolve()!=candidate:raise SystemExit('Active release changed while waiting')
        if (DATA/'settings.json').read_bytes()!=settings_before:raise SystemExit('Settings changed while waiting; rerun preview')
        backup=BACKUPS/('personality-life-rollback-'+str(time.time_ns()))
        backup.mkdir(parents=True,mode=0o700)
        (backup/'settings.candidate.json').write_bytes(settings_before)
        (backup/'settings.candidate.json').chmod(0o600)
        systemctl('stop')
        restored=False
        try:
            if busy():raise RuntimeError('An operation crossed the shutdown boundary')
            if (DATA/'settings.json').read_bytes()!=settings_before:raise RuntimeError('Settings changed during shutdown')
            write_settings(json.dumps(settings_after,ensure_ascii=False,indent=2).encode('utf-8'))
            switch(previous);systemctl('start')
            if not await_health():raise RuntimeError('Old core failed its health check')
            restored=True
        except BaseException:
            systemctl('stop',check=False)
            switch(candidate);write_settings(settings_before);systemctl('start',check=False)
            candidate_recovered=await_health()
            result={**plan,'restored':False,'candidate_recovered':candidate_recovered,'backup':str(backup)}
            (backup/'result.json').write_text(json.dumps(result,indent=2));(backup/'result.json').chmod(0o600)
            print(json.dumps(result));raise
        result={**plan,'restored':restored,'backup':str(backup),
            'settings_sha256':hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()}
        (backup/'result.json').write_text(json.dumps(result,indent=2));(backup/'result.json').chmod(0o600)
        print(json.dumps(result))


if __name__=='__main__':main()

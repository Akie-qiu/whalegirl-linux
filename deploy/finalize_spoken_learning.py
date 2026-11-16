"""Finalize this in-progress release's reflection allowance, retaining its backup."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import deploy_spoken_learning as deployment

r=deployment.rollout


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    lock=open(r.ASSETS/'bilibili-spoken-learning-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    report_path=r.ASSETS/'bilibili-spoken-learning-deployment.json'
    report=json.loads(report_path.read_text());release=Path(report['release']);previous=Path(report['previous'])
    if (release.parent!=r.BASE/'releases' or not release.name.startswith('bilibili-spoken-learning-')
            or (r.BASE/'current').resolve()!=release):raise RuntimeError('active_release_changed')
    target=release/'whalegirl_linux/growth.py';old=target.read_bytes();new=Path(sys.argv[1]).read_bytes()
    anchor=b'answer=await self.model_call(engine,system,payload,generation,role,max_tokens=4096)'
    if digest(target)!=report['hashes']['whalegirl_linux/growth.py'] or old.count(anchor)!=1:
        raise RuntimeError('source_changed')
    if new!=old.replace(anchor,anchor.replace(b'4096',b'8192')):raise RuntimeError('unexpected_followup_change')
    compile(new,str(target),'exec')
    names=('settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc','bilibili-session.enc')
    protected={name:digest(r.DATA/name) for name in names if (r.DATA/name).is_file()}
    native=r.command('docker','exec',r.CONTAINER,'pgrep','-x','wechat')
    backup=Path(sys.argv[2]);backup.write_bytes(old);backup.chmod(0o600)
    started=time.monotonic()
    while r.api('state').get('processing') or deployment.inflight():
        if time.monotonic()-started>90:raise RuntimeError('busy_postpone_followup')
        time.sleep(1)
    if (r.BASE/'current').resolve()!=release or digest(target)!=report['hashes']['whalegirl_linux/growth.py']:
        raise RuntimeError('source_changed_during_wait')
    owner=target.stat();stopped=False
    try:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=30);stopped=True
        if deployment.inflight():raise RuntimeError('operation_crossed_shutdown')
        pending=target.with_name('growth.finalize-'+str(time.time_ns())+'.pending')
        fd=os.open(pending,os.O_CREAT|os.O_EXCL|os.O_WRONLY,owner.st_mode&0o777)
        with os.fdopen(fd,'wb') as stream:stream.write(new);stream.flush();os.fsync(stream.fileno())
        os.chown(pending,owner.st_uid,owner.st_gid);os.replace(pending,target)
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
        for _ in range(60):
            try:
                with r.OPENER.open('http://127.0.0.1:8092/health',timeout=3) as response:health=json.load(response)
                state=r.api('state')
                if health.get('status')=='ok' and not health.get('failed_background_tasks') and state.get('gateway',{}).get('status')=='logged_in':break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('finalized_core_not_healthy')
        if any(digest(r.DATA/name)!=value for name,value in protected.items()):raise RuntimeError('configuration_changed')
        if r.command('docker','exec',r.CONTAINER,'pgrep','-x','wechat')!=native:raise RuntimeError('wechat_restarted')
    except BaseException:
        if stopped:
            subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=30)
            r.switch(previous,str(time.time_ns())+'-finalize-rollback')
            subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
        raise
    report['hashes']['whalegirl_linux/growth.py']=digest(target)
    report.update(finalized_at=time.time(),reflection_max_tokens=8192,
        budget_followup_backup=str(backup),database_snapshot_reused=True)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));report_path.chmod(0o600)
    destination=Path(report['config_backup'])/'deployment.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2));destination.chmod(0o600)
    print(json.dumps({'release':str(release),'healthy':True,'wechat_online':True,
        'reflection_max_tokens':8192,'database_snapshot_reused':True}),flush=True)


if __name__=='__main__':main()

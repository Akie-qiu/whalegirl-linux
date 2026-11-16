"""Switch the speech release back without restoring over new chats or memories."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:return json.load(response)


def busy():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
            "SELECT count(*) FROM growth_runs WHERE state IN ('planning','reading','reflecting')"))


def switch(target):
    link=BASE/('current.spoken-rollback-'+str(time.time_ns()))
    link.symlink_to(target,target_is_directory=True);os.replace(link,BASE/'current')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    report=json.loads(args.report.read_text());new,old=Path(report['release']),Path(report['previous'])
    if (new.parent!=BASE/'releases' or old.parent!=BASE/'releases' or not old.is_dir()
            or not new.name.startswith('bilibili-spoken-learning-')):raise SystemExit('Unexpected rollback targets')
    current=(BASE/'current').resolve()
    if current==old:
        print(json.dumps({'already_restored':True,'release':str(old)}));return
    if current!=new:raise SystemExit('Active release changed; nothing switched')
    lock=open('/opt/whalegirl-linux-assets/bilibili-spoken-learning-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    started=time.monotonic()
    while api('api/state').get('processing') or busy():
        if time.monotonic()-started>60:raise SystemExit('Work in progress; retry when idle')
        time.sleep(1)
    subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=30)
    try:
        if busy():raise RuntimeError('Operation crossed shutdown boundary')
        switch(old);subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
        for _ in range(40):
            try:
                health=api('health')
                if health.get('status')=='ok' and not health.get('failed_background_tasks'):break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('Old core failed health check')
    except BaseException:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=30)
        switch(new);subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30);raise
    print(json.dumps({'restored':str(old),'database_restored':False,'configuration_changed':False,'wechat_restarted':False}))


if __name__=='__main__':main()

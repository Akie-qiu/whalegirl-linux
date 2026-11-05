"""Restore a recorded release without restoring an old database over new chats."""
from pathlib import Path
import fcntl
import json
import os
import sqlite3
import subprocess
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')


def old_settings(current,meta):
    result={k:v for k,v in current.items() if k not in meta['new_settings_keys']}
    for key,value in meta['retired_settings'].items():result.setdefault(key,value)
    for key,change in meta.get('changed_settings',{}).items():
        if result.get(key)==change['after']:result[key]=change['before']
    if 'bilibili_feeds' in result:
        result['bilibili_feeds']=[mode for mode in result['bilibili_feeds'] if mode in ('recommend','search','following','favorites','history')]
    # The old engine cannot enforce the new permission model.
    result.update(growth_enabled=False,bilibili_enabled=False,growth_tieba_enabled=False)
    return result


def isolate_database(db):
    db.row_factory=sqlite3.Row;before=[]
    for raw in db.execute("SELECT * FROM memories WHERE source_class='internet_learning'").fetchall():
        old=dict(raw);before.append(old)
        if old['archived'] and old['status']=='forgotten':continue
        after={**old,'archived':1,'status':'forgotten','version':old['version']+1,'updated':time.time()}
        db.execute('UPDATE memories SET archived=?,status=?,version=?,updated=? WHERE id=?',
            (after['archived'],after['status'],after['version'],after['updated'],old['id']))
        db.execute('INSERT INTO memory_revisions(memory_id,account,actor,operation,before_json,after_json,created) VALUES(?,?,?,?,?,?,?)',
            (old['id'],old['account'],'system','internet_release_rollback',
             json.dumps(old,ensure_ascii=False),json.dumps(after,ensure_ascii=False),time.time()))
    db.execute("UPDATE growth_runs SET state='interrupted',reserved=0,error='发布回滚，停止旧队列；保留资料' WHERE state='queued'")
    return before


def save_settings(value):
    path=DATA/'settings.json';stat=path.stat()
    pending=path.with_name('settings.rollback-internet-'+str(time.time_ns())+'.pending')
    fd=os.open(pending,os.O_CREAT|os.O_EXCL|os.O_WRONLY,stat.st_mode&0o777)
    with os.fdopen(fd,'w') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.chmod(pending,stat.st_mode&0o777);os.chown(pending,stat.st_uid,stat.st_gid);os.replace(pending,path)


def switch(target):
    pending=BASE/('current.internet-rollback-'+str(time.time_ns()))
    pending.symlink_to(target,target_is_directory=True);os.replace(pending,BASE/'current')


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)


def busy():
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('sending','generating')",
            "SELECT count(*) FROM growth_runs WHERE state IN ('planning','reading','reflecting')"))


def main():
    here=Path(__file__).resolve().parent;meta=json.loads((here/'release.json').read_text())
    new,old=Path(meta['release']),Path(meta['previous'])
    if (new.parent!=BASE/'releases' or old.parent!=BASE/'releases'
            or not new.name.startswith('internet-growth-20261105-') or not old.is_dir()
            or (BASE/'current').resolve()!=new):
        raise SystemExit('Unexpected active release or rollback target; nothing changed')
    lock=open('/opt/whalegirl-linux-assets/internet-growth-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:processing=api('api/state').get('processing')
    except Exception:processing=False
    if processing or busy():raise SystemExit('Operation in flight; retry when idle')
    subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=True,timeout=40)
    before=json.loads((DATA/'settings.json').read_text());rows=[]
    try:
        with sqlite3.connect(DATA/'state.sqlite3') as db:rows=isolate_database(db)
        saved=here/('internet-statuses-before-rollback-'+str(time.time_ns())+'.json')
        saved.write_text(json.dumps(rows,ensure_ascii=False));saved.chmod(0o600)
        save_settings(old_settings(before,meta));switch(old)
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=35)
        for _ in range(40):
            try:
                health=api('health')
                if health.get('status')=='ok' and not health.get('failed_background_tasks'):break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('Old release health check failed')
    except BaseException:
        subprocess.run(['systemctl','stop','whalegirl-linux.service'],check=False,timeout=40)
        if rows:
            with sqlite3.connect(DATA/'state.sqlite3') as db:
                for row in rows:
                    keys=[key for key in row if key!='id']
                    db.execute('UPDATE memories SET '+','.join(key+'=?' for key in keys)+' WHERE id=?',
                        [row[key] for key in keys]+[row['id']])
        save_settings(before);switch(new)
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=35)
        raise
    print(json.dumps({'restored':str(old),'internet_growth_paused':True,'new_chats_and_memory_content_retained':True,
        'wechat_restarted':False}),flush=True)


if __name__=='__main__':main()

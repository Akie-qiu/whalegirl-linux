"""Apply the narrowly staged speech release with one DB snapshot and rollback."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='bilibili-spoken-learning'
rollout.FILES={'whalegirl_linux/growth.py','whalegirl_linux/growth_memory.py',
    'whalegirl_linux/expression_reference.py','whalegirl_linux/bilibili.py','whalegirl_linux/growth.js',
    'tests/test_video_speech.py','tests/test_expression_reference.py'}
rollout.TEST_MODULES=('test_video_speech','test_expression_reference','test_bilibili')


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return sum(db.execute(query).fetchone()[0] for query in (
            "SELECT count(*) FROM outbound_actions WHERE state='sending'",
            "SELECT count(*) FROM picture_jobs WHERE state IN ('generating','sending')",
            "SELECT count(*) FROM moments_actions WHERE state='sending'",
            "SELECT count(*) FROM growth_runs WHERE state IN ('planning','reading','reflecting')"))


rollout.inflight=inflight


def main():
    rollout.require_space()
    stamp=time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1_000_000_000)
    backup=Path('/var/backups/whalegirl')/('spoken-learning-config-'+stamp)
    backup.mkdir(parents=True,mode=0o700)
    names=('settings.json','persona-cards.json','model-profiles.json','model-api-keys.enc',
        'api-key.enc','image-api-key.enc','search-api-key.enc','encryption.key',
        'bilibili-session.enc','admin.token','gateway.token')
    protected={}
    for name in names:
        source=rollout.DATA/name
        if source.is_file():
            shutil.copy2(source,backup/name);(backup/name).chmod(0o600)
            protected[name]=hashlib.sha256(source.read_bytes()).hexdigest()
    rollout.main()
    report_path=rollout.ASSETS/(rollout.DEPLOYMENT+'-deployment.json')
    report=json.loads(report_path.read_text())
    report.update(config_backup=str(backup),learning_only=True,
        additional_protected_unchanged=all(hashlib.sha256((rollout.DATA/name).read_bytes()).hexdigest()==value
                                         for name,value in protected.items()))
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2));report_path.chmod(0o600)
    (backup/'deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(backup/'deployment.json').chmod(0o600)
    print(json.dumps({'config_backup':str(backup),'database_backup':str(Path(report['backup'])/'state-before.sqlite3'),
        'additional_protected_unchanged':report['additional_protected_unchanged']},ensure_ascii=True),flush=True)


if __name__=='__main__':main()

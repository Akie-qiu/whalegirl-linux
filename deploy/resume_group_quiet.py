"""Finish the already-tested release after freeing disk space; reuse its backup."""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import deploy_moments_recovery as rollout


def main():
    rollout.DEPLOYMENT='group-quiet'
    manifest=json.loads(Path(sys.argv[1]).read_text())
    release=Path(sys.argv[2]).resolve()
    previous=(rollout.BASE/'current').resolve()
    if str(previous)!=manifest['root'] or release.parent!=rollout.BASE/'releases' or not release.name.startswith('group-quiet-'):
        raise RuntimeError('Unexpected release or rollback source')
    for name,expected in manifest['after'].items():
        if rollout.digest(release/name)!=expected:raise RuntimeError('Candidate differs from verified payload: '+name)
    backup=rollout.ASSETS/(release.name.replace('group-quiet-','group-quiet-before-',1))
    tests=(backup/'focused-tests.log').read_text()
    if not re.search(r'Ran 33 tests',tests) or not tests.rstrip().endswith('OK'):
        raise RuntimeError('Candidate checks did not pass')
    if not (backup/'state-before.sqlite3.gz').is_file():raise RuntimeError('Retained recovery backup missing')
    if subprocess.run(['systemctl','is-active','--quiet','whalegirl-linux.service']).returncode==0:
        raise RuntimeError('Core must remain stopped during disk recovery')
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise RuntimeError('Database check failed')
    protected={name:rollout.digest(rollout.DATA/name) for name in ('settings.json','api-key.enc','image-api-key.enc','search-api-key.enc','admin.token')}
    native=rollout.command('docker','exec',rollout.CONTAINER,'pgrep','-x','wechat')
    reader=rollout.command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    container=rollout.command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',rollout.CONTAINER)
    rollout.switch(release,str(time.time_ns())+'-disk-recovered')
    subprocess.run(['systemctl','start','whalegirl-linux.service'],check=True,timeout=30)
    for _ in range(40):
        try:
            with rollout.OPENER.open('http://127.0.0.1:8092/health',timeout=2) as response:health=json.load(response)
            state=rollout.api('state');config=rollout.api('config')
            if health.get('status')=='ok' and not health.get('failed_background_tasks') and state.get('gateway',{}).get('status')=='logged_in':break
        except Exception:
            pass
        time.sleep(.5)
    else:raise RuntimeError('Core has not recovered after disk space was freed')
    preserved={'wechat_pid':rollout.command('docker','exec',rollout.CONTAINER,'pgrep','-x','wechat')==native,
        'reader_pid':rollout.command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')==reader,
        'container':rollout.command('docker','inspect','--format','{{.Id}} {{.RestartCount}}',rollout.CONTAINER)==container,
        'configuration_and_keys':all(rollout.digest(rollout.DATA/name)==value for name,value in protected.items())}
    report={'release':str(release),'previous':str(previous),'backup':str(backup/'state-before.sqlite3.gz'),
        'focused_tests':33,'health':health,'wechat_status':state['gateway']['status'],'paused':bool(state.get('paused_reason')),
        'stickers':state.get('stickers'),'bot_enabled':state.get('enabled'),'independent_search_configured':config.get('search_api_key_configured'),
        'database_quick_check':'ok','preserved':preserved,'real_messages_sent':0,'reused_tested_release':True,'hashes':manifest['after']}
    (rollout.ASSETS/'group-quiet-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='hashes'},ensure_ascii=False))
    if not all(preserved.values()) or report['paused']:raise RuntimeError('Post-recovery preservation check failed')


if __name__=='__main__':main()

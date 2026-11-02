"""Core-only rollout plus native result ownership repair, retaining login."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import time
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='reply-pipeline'
rollout.FILES={'whalegirl_linux/reply_pipeline.py','tests/test_reply_pipeline.py',
    'deploy/native_fast_turn_v1.py','tests/test_fast_send.py'}|{'whalegirl_linux/'+n for n in
    ('runtime.py','app.py','proactive.js','console.js','web.html')}
rollout.CONFIG_DEFAULTS={}
rollout.TEST_MODULES=('test_reply_pipeline','test_fast_send','test_media_delivery',
                      'test_sticker_preferences','test_cache_layout_repair')
original_inflight=rollout.inflight
HELPER=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/native_fast_turn_v1.py')
CAP=HELPER.parent.parent/'whalegirl-jobs/fast-turn-capability.json'


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        growth=db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]
    return original_inflight()+growth


def main():
    manifest=json.loads(Path(sys.argv[2]).read_text());previous=Path(manifest['root'])
    original=HELPER.read_bytes();oldcap=CAP.read_bytes()
    if hashlib.sha256(original).hexdigest()!=manifest['native_before']:raise RuntimeError('Native helper changed')
    with tarfile.open(sys.argv[1]) as archive:helper=archive.extractfile('deploy/native_fast_turn_v1.py').read()
    backup=Path('/opt/whalegirl-linux-assets')/('reply-pipeline-native-before-'+str(time.time_ns()))
    backup.mkdir(mode=0o700);(backup/HELPER.name).write_bytes(original)
    (backup/'capability.json').write_bytes(oldcap)
    original_switch=rollout.switch;changed=False

    def restore():
        HELPER.write_bytes(original);HELPER.chmod(0o644);CAP.write_bytes(oldcap);CAP.chmod(0o644)

    def switch(target,stamp):
        nonlocal changed
        if target==previous:
            if changed:restore();changed=False
        else:
            if hashlib.sha256(HELPER.read_bytes()).hexdigest()!=manifest['native_before']:
                raise RuntimeError('Native helper changed before switch')
            changed=True;HELPER.write_bytes(helper);HELPER.chmod(0o644)
            cap=json.loads(oldcap);cap['helper']=hashlib.sha256(helper).hexdigest()
            CAP.write_text(json.dumps(cap));CAP.chmod(0o644)
        return original_switch(target,stamp)

    rollout.switch=switch;rollout.inflight=inflight
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    try:rollout.main()
    except BaseException:
        if changed:restore()
        raise
    report=Path('/opt/whalegirl-linux-assets/reply-pipeline-deployment.json')
    value=json.loads(report.read_text());value.update(native_backup=str(backup),
        native_helper_verified=HELPER.read_bytes()==helper)
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

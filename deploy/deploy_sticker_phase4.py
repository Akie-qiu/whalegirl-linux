"""Update guarded native sticker reads while retaining the existing login."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time
import deploy_moments_recovery as rollout

NATIVE={'deploy/a11y_safe.py':'/opt/tools/a11y-dump','deploy/chat_select_linux.py':'/opt/tools/chat-select.py'}
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CAP=TOOLS.parent/'whalegirl-jobs/fast-turn-capability.json'
rollout.DEPLOYMENT='reply-sticker-phase4'
rollout.FILES=set(NATIVE)|{'tests/test_sticker_panel_identity.py','tests/test_sticker_anchor_scroll.py',
    'tests/test_native_delivery.py','tests/test_native_favorite_mapping.py'}
rollout.TEST_MODULES=('test_sticker_panel_identity','test_sticker_anchor_scroll',
    'test_native_delivery.NativePopupTests','test_native_favorite_mapping','test_reply_latency.SnapshotTests')
rollout.CONFIG_DEFAULTS={}
original_inflight=rollout.inflight


def mounted(name):
    return subprocess.check_output(['docker','exec',rollout.CONTAINER,'cat',NATIVE[name]],timeout=8)


def install(name,data):
    # Preserve both existing bind-mount inodes. No native process restart.
    (TOOLS/Path(name).name).write_bytes(data)
    if mounted(name)!=data:
        subprocess.run(['docker','exec','-i',rollout.CONTAINER,'python3','-c',
            'import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())',NATIVE[name]],
            input=data,check=True,timeout=8)
    if mounted(name)!=data:raise RuntimeError('Native install did not match payload: '+name)


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        return original_inflight()+db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]


def main():
    manifest=json.loads(Path(sys.argv[2]).read_text());previous=Path(manifest['root'])
    originals={name:mounted(name) for name in NATIVE}
    oldcap=CAP.read_bytes()
    for name,data in originals.items():
        if hashlib.sha256(data).hexdigest()!=manifest['mounted_before'][name]:raise RuntimeError('Native source changed: '+name)
        if (TOOLS/Path(name).name).read_bytes()!=data:raise RuntimeError('Persistent native source differs: '+name)
    with tarfile.open(sys.argv[1]) as archive:payload={name:archive.extractfile(name).read() for name in NATIVE}
    backup=Path('/opt/whalegirl-linux-assets')/('reply-sticker-native-before-'+str(time.time_ns()))
    backup.mkdir(mode=0o700)
    for name,data in originals.items():(backup/Path(name).name).write_bytes(data)
    (backup/'capability.json').write_bytes(oldcap)
    oldswitch=rollout.switch;changed=False

    def switch(target,stamp):
        nonlocal changed
        if target==previous:
            if changed:
                for name,data in originals.items():install(name,data)
                CAP.write_bytes(oldcap)
                changed=False
        else:
            for name,data in originals.items():
                if mounted(name)!=data:raise RuntimeError('Native source changed before switch: '+name)
            changed=True
            for name,data in payload.items():install(name,data)
            capability=json.loads(oldcap)
            capability['selector']=hashlib.sha256(payload['deploy/chat_select_linux.py']).hexdigest()
            CAP.write_text(json.dumps(capability));CAP.chmod(0o644)
        return oldswitch(target,stamp)

    rollout.switch=switch;rollout.inflight=inflight
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    try:rollout.main()
    except BaseException:
        if changed:
            for name,data in originals.items():install(name,data)
            CAP.write_bytes(oldcap)
        raise
    report=Path('/opt/whalegirl-linux-assets/reply-sticker-phase4-deployment.json')
    value=json.loads(report.read_text());value.update(native_backup=str(backup),
        native_verified=all(mounted(name)==data for name,data in payload.items()))
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Install live compact panel reads while preserving native login and state."""
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
HELPER=TOOLS/'native_panel_reader.py'
rollout.DEPLOYMENT='reply-speed-phase7'
rollout.FILES=set(NATIVE)|{'deploy/native_panel_reader.py','tests/test_native_panel_reader.py','tests/test_sticker_leaf_snapshot.py'}
rollout.TEST_MODULES=('test_native_panel_reader','test_sticker_leaf_snapshot','test_sticker_scroll_progress',
    'test_sticker_subtree_delivery','test_sticker_panel_identity','test_native_delivery.NativePopupTests',
    'test_native_favorite_mapping','test_reply_latency.SnapshotTests')
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
    if HELPER.exists():raise RuntimeError('Native panel helper already exists; review before update')
    oldcap=CAP.read_bytes()
    for name,data in originals.items():
        if hashlib.sha256(data).hexdigest()!=manifest['mounted_before'][name]:raise RuntimeError('Native source changed: '+name)
        if (TOOLS/Path(name).name).read_bytes()!=data:raise RuntimeError('Persistent native source differs: '+name)
    with tarfile.open(sys.argv[1]) as archive:
        payload={name:archive.extractfile(name).read() for name in NATIVE}
        helper=archive.extractfile('deploy/native_panel_reader.py').read()
    compile(helper,str(HELPER),'exec')
    backup=Path('/opt/whalegirl-linux-assets')/('reply-speed7-native-before-'+str(time.time_ns()))
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
            HELPER.write_bytes(helper);HELPER.chmod(0o644)
            for name,data in payload.items():install(name,data)
            capability=json.loads(oldcap)
            capability['selector']=hashlib.sha256(payload['deploy/chat_select_linux.py']).hexdigest()
            capability['panel_reader']=hashlib.sha256(helper).hexdigest()
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
    report=Path('/opt/whalegirl-linux-assets/reply-speed-phase7-deployment.json')
    value=json.loads(report.read_text());value.update(native_backup=str(backup),
        native_verified=all(mounted(name)==data for name,data in payload.items()) and HELPER.read_bytes()==helper)
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

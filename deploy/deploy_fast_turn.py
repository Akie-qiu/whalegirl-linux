"""Install UI code only while the core is stopped; preserve the native login."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time
import deploy_moments_recovery as rollout

rollout.DEPLOYMENT='fast-turn'
rollout.FILES={'whalegirl_linux/fast_send.py','deploy/native_fast_turn_v1.py','tests/test_fast_send.py','deploy/chat_select_linux.py'}|{'whalegirl_linux/'+n for n in
    ('runtime.py','gateway.py','delivery.py','settings.py','proactive.js','app.py','console.js','web.html')}
rollout.CONFIG_DEFAULTS={'fast_reply_enabled':True}
rollout.TEST_MODULES=('test_fast_send','test_media_delivery','test_sticker_preferences','test_cache_layout_repair')
original_inflight=rollout.inflight
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
CAP=TOOLS.parent/'whalegirl-jobs/fast-turn-capability.json'


def digest(value):return hashlib.sha256(value).hexdigest()


def mounted():return subprocess.check_output(['docker','exec',rollout.CONTAINER,'cat','/opt/tools/chat-select.py'],timeout=10)


def install_mounted(data):
    code="import sys,pathlib;p=pathlib.Path('/opt/tools/chat-select.py');v=sys.stdin.buffer.read();compile(v,str(p),'exec');p.write_bytes(v)"
    subprocess.run(['docker','exec','-i','-u','root',rollout.CONTAINER,'python3','-c',code],input=data,check=True,timeout=10)


def inflight():
    with sqlite3.connect('file:'+str(rollout.DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        growth=db.execute("SELECT count(*) FROM growth_runs WHERE state='planning'").fetchone()[0]
    return original_inflight()+growth


def main():
    manifest=json.loads(Path(sys.argv[2]).read_text());previous=Path(manifest['root'])
    with tarfile.open(sys.argv[1]) as archive:
        selector=archive.extractfile('deploy/chat_select_linux.py').read()
        helper=archive.extractfile('deploy/native_fast_turn_v1.py').read()
    if digest(mounted())!=manifest['mounted_before']:raise RuntimeError('Active native selector changed')
    original={n:(TOOLS/n).read_bytes() if (TOOLS/n).is_file() else None for n in manifest['native_before']}
    if any((digest(v) if v is not None else None)!=manifest['native_before'][n] for n,v in original.items()):raise RuntimeError('Persistent native files changed')
    oldcap=CAP.read_bytes() if CAP.exists() else None;oldmounted=mounted()
    backup=Path('/opt/whalegirl-linux-assets')/('fast-turn-native-before-'+str(time.time_ns()))
    backup.mkdir(mode=0o700)
    for n,v in original.items():
        if v is not None:(backup/n).write_bytes(v)
    (backup/'mounted-selector.py').write_bytes(oldmounted)
    changed=False;original_switch=rollout.switch

    def switch(target,stamp):
        nonlocal changed
        if target==previous:
            if changed:
                for n,v in original.items():
                    if v is not None:(TOOLS/n).write_bytes(v)
                install_mounted(oldmounted)
                CAP.write_bytes(oldcap if oldcap is not None else b'{"version":0}')
                CAP.chmod(0o644);changed=False
        else:
            if digest(mounted())!=manifest['mounted_before']:raise RuntimeError('Native selector changed before switch')
            changed=True
            (TOOLS/'native_fast_turn_v1.py').write_bytes(helper);(TOOLS/'native_fast_turn_v1.py').chmod(0o644)
            # Retain the inode when this path is bind-mounted into /opt/tools.
            (TOOLS/'chat_select_linux.py').write_bytes(selector)
            if mounted()!=selector:install_mounted(selector)
            if mounted()!=selector:raise RuntimeError('Native selector verification failed')
            CAP.write_text(json.dumps({'version':1,'selector':digest(selector),'helper':digest(helper)}));CAP.chmod(0o644)
        return original_switch(target,stamp)

    rollout.switch=switch;rollout.inflight=inflight
    rollout.REQUIRE_ONLINE=bool(rollout.api('config').get('enabled'))
    try:rollout.main()
    except BaseException:
        if changed:
            for n,v in original.items():
                if v is not None:(TOOLS/n).write_bytes(v)
            install_mounted(oldmounted)
            CAP.write_bytes(oldcap if oldcap is not None else b'{"version":0}');CAP.chmod(0o644)
        raise
    report=Path('/opt/whalegirl-linux-assets/fast-turn-deployment.json')
    value=json.loads(report.read_text());value.update(native_backup=str(backup),native_selector_verified=mounted()==selector,
        native_helper_verified=digest((TOOLS/'native_fast_turn_v1.py').read_bytes())==digest(helper))
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

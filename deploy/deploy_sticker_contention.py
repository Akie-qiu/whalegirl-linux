"""Small, hash-guarded follow-up to the backed-up sticker reliability release."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

B = Path('/opt/whalegirl-linux-assets/sticker-reliability-20261112')
spec = importlib.util.spec_from_file_location('sticker_release', B/'stage/deploy/deploy_sticker_reliability.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
FILES = ('deploy/chat_select_linux.py', 'tests/test_native_delivery.py')


def healthy(expected_login, expected_paused):
    deadline = time.monotonic()+40
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=2) as response:
                health = json.load(response)
            state = release.api('state')
            if (health.get('status')=='ok' and not health.get('failed_background_tasks')
                    and state.get('gateway',{}).get('status')==expected_login
                    and bool(state.get('paused_reason'))==expected_paused):
                return health
        except Exception:
            pass
        time.sleep(.5)
    raise RuntimeError('Core did not return to a healthy logged-in state')


def native_hash():
    return release.command('docker','exec',release.CONTAINER,'sha256sum',release.MOUNTED).split()[0]


def install():
    record = B/'contention-followup.json'
    if record.exists():
        raise RuntimeError('Follow-up already installed; review its record first')
    base = json.loads((B/'manifest.json').read_text())
    current = release.BASE/'current'
    root = current.resolve()
    if str(root)!=base['release'] or release.source_snapshot(root)!=base['source_expected']:
        raise RuntimeError('A concurrent release/source update exists; refusing to overwrite it')
    selector = 'deploy/chat_select_linux.py'
    before = {name:release.digest(root/name) for name in FILES}
    if native_hash()!=before[selector] or release.digest(release.TOOLS/'chat_select_linux.py')!=before[selector]:
        raise RuntimeError('Native selector differs from the reviewed release')
    payload = {name:(B/'stage'/name).read_bytes() for name in FILES}
    for name,data in payload.items():
        compile(data,name,'exec')
    folder = B/('contention-before-'+str(time.time_ns()))
    folder.mkdir(mode=0o700)
    for name in FILES:
        target=folder/name
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        shutil.copy2(root/name,target)
    protected = release.protected_hashes()
    reader = release.reader_pid()
    container = release.container_state()
    live_state=release.api('state')
    login_status=live_state.get('gateway',{}).get('status')
    if login_status not in ('logged_in','logged_out'):
        raise RuntimeError('Unexpected WeChat status; refusing deployment')
    paused=bool(live_state.get('paused_reason'))
    release.wait_idle(time.monotonic()+90)
    changed = False
    try:
        release.command('systemctl','stop','whalegirl-linux.service')
        if release.inflight() or current.resolve()!=root or release.source_snapshot(root)!=base['source_expected']:
            raise RuntimeError('Active work or concurrent changes appeared during draining')
        changed = True
        for name,data in payload.items():
            release.replace(root/name,data)
        release.replace(release.TOOLS/'chat_select_linux.py',payload[selector])
        release.update_mounted_selector(payload[selector])
        tests=subprocess.run([str(release.BASE/'venv/bin/python'),'-m','unittest',
            'test_native_delivery','test_media_delivery','test_sticker_grid_safety',
            'test_sticker_animated_grid','test_sticker_subtree_delivery','test_sticker_anchor_scroll','-q'],
            cwd=root,env={**os.environ,'PYTHONPATH':str(root)+':'+str(root/'tests'),
                          'LOGURU_LEVEL':'WARNING'},capture_output=True,text=True,timeout=120)
        (folder/'tests.log').write_text(tests.stdout+tests.stderr)
        if tests.returncode:
            raise RuntimeError('Follow-up tests failed: '+tests.stderr[-1800:])
        count=re.search(r'Ran (\d+) tests? in ',tests.stdout+tests.stderr)
        if not count:
            raise RuntimeError('Could not confirm the completed test count')
        installed={name:release.digest(root/name) for name in FILES}
        expected={**base['source_expected'],**installed}
        if (native_hash()!=installed[selector] or release.source_snapshot(root)!=expected
                or protected!=release.protected_hashes()):
            raise RuntimeError('Unexpected source or private configuration change')
        release.command('systemctl','start','whalegirl-linux.service')
        health=healthy(login_status,paused)
        if reader!=release.reader_pid() or container!=release.container_state():
            raise RuntimeError('Native reader or WeChat changed unexpectedly')
        report={'at':time.time(),'release':str(root),'backup':str(folder),'before':before,
                'installed':installed,'health':health,'tests_passed':int(count.group(1)),
                'wechat_login_status_unchanged':login_status,
                'paused_state_unchanged':paused,
                'private_files_unchanged':protected==release.protected_hashes(),
                'reader_unchanged':True,'wechat_unchanged':True,'real_test_messages_sent':0}
        record.write_text(json.dumps(report,indent=2))
        record.chmod(0o600)
        print(json.dumps(report))
    except BaseException:
        if changed:
            release.command('systemctl','stop','whalegirl-linux.service')
            for name in FILES:
                release.replace(root/name,(folder/name).read_bytes())
            original=(folder/selector).read_bytes()
            release.replace(release.TOOLS/'chat_select_linux.py',original)
            release.update_mounted_selector(original)
        raise
    finally:
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)


def rollback():
    """Undo the follow-up, then restore the original pre-repair release."""
    record=json.loads((B/'contention-followup.json').read_text())
    root=(release.BASE/'current').resolve()
    if str(root)!=record['release']:
        raise RuntimeError('A newer release is active; refusing rollback')
    for name,expected in record['installed'].items():
        if release.digest(root/name)!=expected:
            raise RuntimeError('Follow-up changed; refusing rollback: '+name)
    folder=Path(record['backup'])
    for name,expected in record['before'].items():
        if release.digest(folder/name)!=expected:
            raise RuntimeError('Backup checksum mismatch: '+name)
    release.wait_idle(time.monotonic()+90)
    release.command('systemctl','stop','whalegirl-linux.service')
    try:
        for name in FILES:
            release.replace(root/name,(folder/name).read_bytes())
        original=(folder/'deploy/chat_select_linux.py').read_bytes()
        release.replace(release.TOOLS/'chat_select_linux.py',original)
        release.update_mounted_selector(original)
    finally:
        subprocess.run(['systemctl','start','whalegirl-linux.service'],check=False,timeout=30)
    release.rollback()


if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('install','rollback'):
        raise SystemExit('usage: deploy_sticker_contention.py install|rollback')
    globals()[sys.argv[1]]()

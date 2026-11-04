"""Install the observed chat-list navigation fix without restarting WeChat."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CONTAINER='whalegirl-wechat-candidate-4118'
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
BASE=Path('/opt/whalegirl-linux')
PYTHON=str(BASE/'venv/bin/python')
EXPECTED='9cb5aae995dba4efa3ddff2fb7ed1926d42bf0a63078acbde46db6f5ab47f2bb'


def install(path,raw):
    owner=path.stat();pending=path.with_name(path.name+'.navigation-'+str(time.time_ns()))
    pending.write_bytes(raw);pending.chmod(owner.st_mode&0o777);os.chown(pending,owner.st_uid,owner.st_gid)
    os.replace(pending,path)


def main(folder):
    release=(BASE/'current').resolve(strict=True)
    selector=(folder/'chat_select_linux.py').read_text('utf-8-sig').replace('\r\n','\n').encode()
    tests=(folder/'test_native_delivery.py').read_bytes()
    ast.parse(selector.decode(),filename='chat_select_linux.py')
    original=(TOOLS/'chat_select_linux.py').read_bytes()
    if hashlib.sha256(original).hexdigest()!=EXPECTED:raise RuntimeError('Native selector changed; preserved')
    backup=folder/('native-navigation-before-'+str(time.time_ns()));backup.mkdir(mode=0o700,exist_ok=False)
    (backup/'chat_select_linux.py').write_bytes(original)
    install(release/'deploy/chat_select_linux.py',selector)
    install(release/'tests/test_native_delivery.py',tests)
    env={**os.environ,'PYTHONPATH':str(release/'tests'),'PYTHONDONTWRITEBYTECODE':'1','LOGURU_LEVEL':'ERROR'}
    with (folder/'native-navigation-tests.log').open('w') as log:
        result=subprocess.run(['runuser','-u','whalegirl-ai','--',PYTHON,'-m','unittest',
            'test_native_delivery','test_sticker_scroll_progress','test_sticker_anchor_scroll','test_native_favorite_mapping','-q'],
            cwd=release,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=35)
    if result.returncode:raise RuntimeError('Native navigation regression failed; native tools not changed')
    native_before=subprocess.check_output(['docker','exec',CONTAINER,'pgrep','-x','wechat'],text=True).strip()
    def sync_helper():
        # This running container has an older single-file bind whose original
        # directory entry is gone. Update its writable inode in place, exactly
        # as start_native_reader.install_helper does; stacking a bind on that
        # deleted mountpoint fails. The durable /data source remains current.
        code="import pathlib;pathlib.Path('/opt/tools/chat-select.py').write_bytes(pathlib.Path('/data/native-tools/chat_select_linux.py').read_bytes())"
        subprocess.run(['docker','exec',CONTAINER,'python3','-c',code],check=True,timeout=8)
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=40)
    try:
        install(TOOLS/'chat_select_linux.py',selector);sync_helper()
        check="import pathlib,hashlib,json; print(json.dumps({n:hashlib.sha256(pathlib.Path(n).read_bytes()).hexdigest() for n in ('/data/native-tools/chat_select_linux.py','/opt/tools/chat-select.py')}))"
        hashes=json.loads(subprocess.check_output(['docker','exec',CONTAINER,'python3','-c',check],text=True,timeout=8))
        if any(v!=hashlib.sha256(selector).hexdigest() for v in hashes.values()):raise RuntimeError('Native bind verification failed')
        native_after=subprocess.check_output(['docker','exec',CONTAINER,'pgrep','-x','wechat'],text=True).strip()
        if native_before!=native_after:raise RuntimeError('Native WeChat PID changed unexpectedly')
    except BaseException:
        install(TOOLS/'chat_select_linux.py',original);sync_helper();raise
    finally:
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=35)
    report=dict(release=str(release),before_sha256=EXPECTED,after_sha256=hashlib.sha256(selector).hexdigest(),
        installed_hashes=hashes,backup=str(backup),native_pid=native_after,native_restarted=False,
        test_tail=(folder/'native-navigation-tests.log').read_text()[-400:],time=time.time())
    path=folder/'native-navigation-deployment.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');path.chmod(0o600)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    folder=Path(sys.argv[1]).resolve(strict=True)
    if folder.parent!=Path('/opt/whalegirl-linux-assets') or not folder.name.startswith('autonomous-chat-canary-'):raise ValueError('Unexpected stage folder')
    main(folder)

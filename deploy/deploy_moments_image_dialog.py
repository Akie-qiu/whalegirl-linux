"""Update the image picker adapter without restarting native WeChat or core."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
ASSETS = Path('/opt/whalegirl-linux-assets')
DATA = Path('/var/lib/whalegirl-linux/core')
TOOLS = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
EXPECTED = '0f0be2f2c7b0d7fa2076d6ff4105920ecca0806ea38a91a39c6e9e06cf61f6f7'
FILES = {'deploy/moments_ui.py', 'tests/test_native_delivery.py',
         'deploy/deploy_moments_image_dialog.py', 'MOMENTS_IMAGE_DIALOG_FIX.md'}


def api(route):
    token = (DATA/'admin.token').read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.load(opener.open(urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token}), timeout=12))


def pids():
    def read(*args):return subprocess.check_output(args, text=True, timeout=10).strip()
    return {'core':read('systemctl','show','whalegirl-linux','-p','MainPID','--value'),
            'reader':read('systemctl','show','whalegirl-native-reader','-p','MainPID','--value'),
            'wechat':read('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')}


def install(path, data):
    owner = (path if path.exists() else path.parent).stat()
    pending = path.with_name(path.name+'.image-dialog-'+str(time.time_ns()))
    pending.write_bytes(data)
    os.chown(pending, owner.st_uid, owner.st_gid)
    pending.chmod((owner.st_mode & 0o777) if path.exists() else 0o640)
    os.replace(pending, path)


def main():
    current = (BASE/'current').resolve()
    native = TOOLS/'moments_ui.py'
    for path in (native, current/'deploy/moments_ui.py'):
        if hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED:
            raise RuntimeError('Moments adapter changed since diagnosis')
    with tarfile.open(ASSETS/'moments-image-dialog-20261022.tar') as archive:
        members = archive.getmembers()
        if {m.name for m in members} != FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment files')
        contents = {m.name:archive.extractfile(m).read() for m in members}
    for name, data in contents.items():
        if name.endswith('.py'):compile(data, name, 'exec')
    for _ in range(40):
        state, moments, library = api('state'), api('moments'), api('sticker-library')
        if not state.get('processing') and not library.get('worker_busy') and not any(
            a['state'] in ('sending','generating') for a in moments.get('actions',[])):
            break
        time.sleep(.25)
    else:raise RuntimeError('UI is busy; defer installation')
    before = pids()
    config = api('config')
    backup = ASSETS/('moments-image-dialog-before-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    targets = {current/name:data for name,data in contents.items()}
    targets[native] = contents['deploy/moments_ui.py']
    previous = {}
    for index, path in enumerate(targets):
        if path.exists():
            previous[path] = path.read_bytes()
            shutil.copy2(path, backup/(str(index)+'-'+path.name))
    try:
        for path, data in targets.items():
            if (BASE/'current').resolve() != current:raise RuntimeError('Release changed')
            install(path, data)
        if pids() != before:raise RuntimeError('Process IDs changed during update')
        if api('config') != config:raise RuntimeError('Configuration changed during update')
        result = {'at':time.time(),'release':str(current),'backup':str(backup),
                  'pids_unchanged':True,'config_preserved':True,
                  'wechat':api('state').get('gateway',{}).get('status'),
                  'native_sha256':hashlib.sha256(native.read_bytes()).hexdigest()}
        (ASSETS/'moments-image-dialog-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except Exception:
        for path, data in previous.items():install(path,data)
        raise


if __name__ == '__main__':main()

"""Deploy current-frame capture and target-level Moments reply deduplication."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux');ASSETS=Path('/opt/whalegirl-linux-assets');DATA=Path('/var/lib/whalegirl-linux/core')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
EXPECTED={'whalegirl_linux/moments.py':'fecd1d6125bae4dd324c3d2917cadde2dbc7098f58baa2eef8f12c431f8ff5ac',
          'whalegirl_linux/app.py':'3d3570800785c36948c9abbf5df678982b966c6a594887a2ec850335c5c4252d',
          'whalegirl_linux/moments.js':'1510ad4518f5a80fa9ade1a092cad4da24cc50df3bca44fd50866a7b52fa2653',
          'deploy/moments_ui.py':'dd9a548aceec7c5bd37373751d001c2a081e4015e926ad01669b32ad7a7224ba'}
FILES={*EXPECTED,'tests/test_moments.py','tests/test_moments_scroll.py',
       'MOMENTS_REPLY_TIMELINE_FIX.md','deploy/deploy_moments_capture_fix.py'}


def api(route):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return json.load(opener.open(urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()}),timeout=12))


def command(*args):return subprocess.check_output(args,text=True,timeout=40).strip()


def install(path,data):
    owner=(path if path.exists() else path.parent).stat()
    pending=path.with_name(path.name+'.capture-fix-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod(owner.st_mode&0o777 if path.exists() else 0o640);os.replace(pending,path)


def main():
    current=(BASE/'current').resolve()
    for name,expected in EXPECTED.items():
        if hashlib.sha256((current/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Source changed since diagnosis: '+name)
    if hashlib.sha256((TOOLS/'moments_ui.py').read_bytes()).hexdigest()!=EXPECTED['deploy/moments_ui.py']:
        raise RuntimeError('Native helper differs from verified source')
    with tarfile.open(ASSETS/'moments-capture-fix-20261023.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected patch files')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    for _ in range(70):
        state,moments=api('state'),api('moments')
        if not state.get('processing') and not any(a['state'] in ['sending','generating'] for a in moments['actions']):break
        time.sleep(.5)
    else:raise RuntimeError('Core busy; no changes made')
    native=command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')
    reader=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    config=hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()
    backup=ASSETS/('moments-capture-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    originals={}
    for name in FILES:
        path=current/name
        if path.exists():
            originals[name]=path.read_bytes();saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,saved)
    command('systemctl','stop','whalegirl-linux')
    try:
        for name,data in contents.items():
            if (BASE/'current').resolve()!=current:raise RuntimeError('Release changed')
            install(current/name,data)
        command('systemctl','start','whalegirl-linux')
        for _ in range(60):
            try:
                state=api('state')
                if state.get('gateway',{}).get('status')=='logged_in':break
            except (OSError,ValueError):pass
            time.sleep(.3)
        else:raise RuntimeError('Core did not become ready')
        if command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')!=native:raise RuntimeError('Native WeChat restarted')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader:raise RuntimeError('Reader restarted')
        if hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()!=config:raise RuntimeError('Configuration changed')
        result={'at':time.time(),'release':str(current),'backup':str(backup),'wechat_pid_preserved':True,
                'reader_pid_preserved':True,'configuration_preserved':True,'wechat':'logged_in',
                'core_sha256':hashlib.sha256((current/'whalegirl_linux/moments.py').read_bytes()).hexdigest()}
        (ASSETS/'moments-capture-fix-deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        command('systemctl','stop','whalegirl-linux')
        for name,data in originals.items():install(current/name,data)
        command('systemctl','start','whalegirl-linux')
        raise


if __name__=='__main__':main()

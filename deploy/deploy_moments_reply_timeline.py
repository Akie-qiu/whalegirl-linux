"""Install the focused comment adapter patch without restarting WeChat or core."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
EXPECTED={'moments_ui.py':{'c62f273aeb06f65f9d953f04188f675c79f845971b239190b745ed4f07c96295',
                          '200bfebadddf9816a0a2b6267108d7cccca8c1cab711663c60a0ecd35c196c5b',
                          '085c227937ab5d1302e4b0051bd90313c9383ecb47cb44353f03746d08728c3c'},
          'moments_layout.py':{'ebee83b18f37775cf6df35a6c9a46b0baf568032e6220760cd0e6489af557ee7',
                              'f2bd80fd37ce5fd4d6262e52c5399ad08d5a4396d67e95c7284113e25fe2ebbb'}}
FILES={'deploy/moments_ui.py','deploy/moments_layout.py','tests/test_moments_scroll.py',
       'MOMENTS_REPLY_TIMELINE_FIX.md','deploy/deploy_moments_reply_timeline.py'}


def api(route):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req=urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+(DATA/'admin.token').read_text().strip()})
    return json.load(opener.open(req,timeout=12))


def pids():
    def read(*args):return subprocess.check_output(args,text=True,timeout=10).strip()
    return {name:read('systemctl','show',name,'-p','MainPID','--value')
            for name in ['whalegirl-linux','whalegirl-native-reader']} | {
        'wechat':read('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')}


def install(path,data):
    owner=(path if path.exists() else path.parent).stat()
    pending=path.with_name(path.name+'.reply-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod(owner.st_mode & 0o777 if path.exists() else 0o640)
    os.replace(pending,path)


def main():
    current=(BASE/'current').resolve()
    for name,expected in EXPECTED.items():
        for path in [TOOLS/name,current/'deploy'/name]:
            if hashlib.sha256(path.read_bytes()).hexdigest() not in expected:
                raise RuntimeError('Adapter changed since diagnosis: '+name)
    with tarfile.open(ASSETS/'moments-reply-timeline-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected patch files')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    for _ in range(60):
        state,moments,library=api('state'),api('moments'),api('sticker-library')
        if not state.get('processing') and not library.get('worker_busy') and not any(
            a['state'] in ['sending','generating'] for a in moments.get('actions',[])):
            break
        time.sleep(.5)
    else:raise RuntimeError('Native UI busy; installation deferred')
    before=pids();config=hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()
    backup=ASSETS/('moments-reply-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    targets={current/name:data for name,data in contents.items()}
    targets.update({TOOLS/name:contents['deploy/'+name] for name in EXPECTED})
    originals={}
    for index,path in enumerate(targets):
        if path.exists():
            originals[path]=path.read_bytes();shutil.copy2(path,backup/(str(index)+'-'+path.name))
    try:
        for path,data in targets.items():
            if (BASE/'current').resolve()!=current:raise RuntimeError('Release changed')
            install(path,data)
        if before!=pids():raise RuntimeError('Running processes changed')
        if hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()!=config:
            raise RuntimeError('Configuration changed')
        result={'at':time.time(),'release':str(current),'backup':str(backup),
                'pids_unchanged':True,'configuration_preserved':True,
                'logged_in':api('state').get('gateway',{}).get('status')=='logged_in',
                'hashes':{name:hashlib.sha256((TOOLS/name).read_bytes()).hexdigest() for name in EXPECTED}}
        (ASSETS/'moments-reply-timeline-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except Exception:
        for path,data in originals.items():install(path,data)
        raise


if __name__=='__main__':main()

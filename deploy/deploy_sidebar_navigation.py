"""Install only static navigation assets; no service/session restarts."""
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
EXPECTED={
    'whalegirl_linux/console.js':'a23486baa188b78d800e044ecd0e1935c16699501b7d889fcaa0ec0d8cbcdf6d',
    'whalegirl_linux/console.css':'da9852096f7a9379d154c8206c4bdccbb53371890c2019aa604ccf187a18876d',
    'whalegirl_linux/web.html':'e1ac2621a580a3a5be25e51531924c99a34b1be97a14431465308659dd00ddf6'}
token=(DATA/'admin.token').read_text().strip();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route):
    return json.load(opener.open(urllib.request.Request('http://127.0.0.1:8092/api/'+route,
        headers={'Authorization':'Bearer '+token}),timeout=12))


def command(*args):return subprocess.check_output(args,text=True,timeout=15).strip()


def process_ids():
    return {'core':command('systemctl','show','whalegirl-linux','-p','MainPID','--value'),
        'reader':command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value'),
        'wechat':command('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')}


def install(path,data):
    owner=path.stat();pending=path.with_name(path.name+'.navigation-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid);pending.chmod(owner.st_mode&0o777)
    os.replace(pending,path)


def deploy():
    current=(BASE/'current').resolve()
    for name,expected in EXPECTED.items():
        if hashlib.sha256((current/name).read_bytes()).hexdigest()!=expected:raise RuntimeError('Live source changed: '+name)
    with tarfile.open(ASSETS/'sidebar-navigation-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=set(EXPECTED) or not all(m.isfile() for m in members):raise RuntimeError('Unexpected static archive')
        contents={m.name:archive.extractfile(m).read() for m in members}
    ids=process_ids();config=api('config');backup=ASSETS/('sidebar-navigation-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    for name in EXPECTED:
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(current/name,saved)
    installed=[]
    try:
        for name in ('whalegirl_linux/console.css','whalegirl_linux/console.js','whalegirl_linux/web.html'):
            if (BASE/'current').resolve()!=current:raise RuntimeError('Another release became active')
            install(current/name,contents[name]);installed.append(name)
        if process_ids()!=ids:raise RuntimeError('A service changed unexpectedly during the static update')
        if api('config')!=config:raise RuntimeError('Configuration changed during static update')
        state=api('state')
        result={'at':time.time(),'release':str(current),'backup':str(backup),'pids_unchanged':True,
            'wechat':state.get('gateway',{}).get('status'),'enabled':state.get('enabled'),
            'files':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()}}
        (ASSETS/'sidebar-navigation-deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        for name in reversed(installed):install(current/name,(backup/name).read_bytes())
        raise


if __name__=='__main__':deploy()

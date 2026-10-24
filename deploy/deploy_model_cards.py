"""Install model switching cards as a static-only console update."""
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
EXPECTED={'console.js':'f0ff3d89ac04dd69cf9ee1691823df7ebf8d7574e40df6881d930e9df9844a42',
          'console.css':'2afaa1b01a946ffdcb41a4191453471a6db2d5d21b21e614c7da03769568032f',
          'web.html':'69c3df6992911c065b0665fb4ddedac42063c7f7b0d829d62cb2aaccdc981247'}


def pids():
    def read(*args):return subprocess.check_output(args,text=True,timeout=12).strip()
    return {name:read('systemctl','show',name,'-p','MainPID','--value')
        for name in ['whalegirl-linux','whalegirl-native-reader']} | {
        'wechat':read('docker','exec','whalegirl-wechat-candidate-4118','pgrep','-x','wechat')}


def install(path,data):
    owner=path.stat();pending=path.with_name(path.name+'.model-cards-'+str(time.time_ns()))
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod(owner.st_mode&0o777);os.replace(pending,path)


def main():
    current=(BASE/'current').resolve();root=current/'whalegirl_linux'
    for name,expected in EXPECTED.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Console changed since inspection: '+name)
    with tarfile.open(ASSETS/'model-cards-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!={'whalegirl_linux/'+name for name in EXPECTED} or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected static files')
        contents={Path(m.name).name:archive.extractfile(m).read() for m in members}
    ids=pids();config=hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()
    backup=ASSETS/('model-cards-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    for name in EXPECTED:shutil.copy2(root/name,backup/name)
    installed=[]
    try:
        for name in ['console.css','console.js','web.html']:
            if (BASE/'current').resolve()!=current:raise RuntimeError('Release changed')
            install(root/name,contents[name]);installed.append(name)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        hashes={}
        for name in EXPECTED:
            url='http://127.0.0.1:8092/'+('' if name=='web.html' else name)+'?v=20261022-model-cards'
            with opener.open(url,timeout=12) as response:data=response.read()
            digest=hashlib.sha256(data).hexdigest()
            if digest!=hashlib.sha256(contents[name]).hexdigest():raise RuntimeError('Served console is stale: '+name)
            hashes[name]=digest
        if pids()!=ids:raise RuntimeError('Process IDs changed')
        if hashlib.sha256((DATA/'settings.json').read_bytes()).hexdigest()!=config:
            raise RuntimeError('Model configuration changed')
        result={'at':time.time(),'backup':str(backup),'release':str(current),
                'pids_unchanged':True,'configuration_preserved':True,'served_asset_hashes':hashes}
        (ASSETS/'model-cards-deployment.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
    except Exception:
        for name in reversed(installed):install(root/name,(backup/name).read_bytes())
        raise


if __name__=='__main__':main()

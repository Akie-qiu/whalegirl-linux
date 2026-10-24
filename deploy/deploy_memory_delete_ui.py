"""Update the memory delete controls without restarting the running services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
EXPECTED={
    'whalegirl_linux/habits.js':'e381c208b652d8eb3cb7ee565ded4020eabf1d32fef90f0388183e46209ce0d5',
    'whalegirl_linux/web.html':'eb21b6b0e5f1e03be3444f20a5f2c661f97a3fcc288d5f5901222c8e722ed7da',
    'whalegirl_linux/improvements.js':'f0094cd55768aee59ecaea424a2be0b12b9664fb971faf7ed822e9b0c5fe3c26',
}


def deploy():
    root=(BASE/'current').resolve()
    stamp=time.strftime('%Y%m%d-%H%M%S')
    backup=ASSETS/('memory-delete-ui-before-'+stamp)
    with tarfile.open(ASSETS/'memory-delete-ui-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=set(EXPECTED) or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected UI archive contents')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,digest in EXPECTED.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Live UI changed: '+name)
    backup.mkdir(mode=0o700)
    for name in EXPECTED:
        saved=backup/Path(name).name
        shutil.copy2(root/name,saved)
    changed=[]
    try:
        if (BASE/'current').resolve()!=root:raise RuntimeError('Release changed during deployment')
        for name,data in contents.items():
            target=root/name;metadata=target.stat()
            pending=target.with_name(target.name+'.memory-delete-'+stamp)
            pending.write_bytes(data);os.chown(pending,metadata.st_uid,metadata.st_gid)
            pending.chmod(metadata.st_mode & 0o777)
            os.replace(pending,target);changed.append(name)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        token=(DATA/'admin.token').read_text().strip()
        request=urllib.request.Request('http://127.0.0.1:8092/api/state',headers={'Authorization':'Bearer '+token})
        state=json.load(opener.open(request,timeout=15))
        for route,name in [('/', 'whalegirl_linux/web.html'),('/habits.js','whalegirl_linux/habits.js'),('/improvements.js','whalegirl_linux/improvements.js')]:
            request=urllib.request.Request('http://127.0.0.1:8092'+route)
            if opener.open(request,timeout=15).read()!=contents[name]:
                raise RuntimeError('The running server did not serve the updated UI: '+name)
        result={'at':time.time(),'release':str(root),'backup':str(backup),
            'files':{n:hashlib.sha256(data).hexdigest() for n,data in contents.items()},
            'wechat':state.get('gateway',{}).get('status'),'enabled':state['enabled'],
            'services_restarted':False,'existing_memories_changed':False}
        (ASSETS/'memory-delete-ui-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except Exception:
        for name in changed:
            target=root/name
            pending=target.with_name(target.name+'.memory-delete-rollback-'+stamp)
            shutil.copy2(backup/Path(name).name,pending)
            metadata=target.stat();os.chown(pending,metadata.st_uid,metadata.st_gid)
            os.replace(pending,target)
        raise


if __name__=='__main__':deploy()

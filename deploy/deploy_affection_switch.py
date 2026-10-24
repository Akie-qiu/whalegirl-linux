"""Publish only static controls for the existing persisted affinity flag."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request

ASSETS=Path('/opt/whalegirl-linux-assets');CURRENT=Path('/opt/whalegirl-linux/current')
DATA=Path('/var/lib/whalegirl-linux/core')
NAMES=('affection.js','console.js','web.html')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=(DATA/'admin.token').read_text().strip()


def api(route):
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+route,headers={'Authorization':'Bearer '+token})
    with opener.open(request,timeout=10) as response:return json.load(response)


def core_pid():
    return subprocess.check_output(['systemctl','show','whalegirl-linux','--property=MainPID','--value'],text=True,timeout=10).strip()


def replace(target,data,stamp,owner):
    temporary=target.with_name(target.name+'.affection-switch-'+stamp)
    temporary.write_bytes(data);os.chown(temporary,owner.st_uid,owner.st_gid);temporary.chmod(owner.st_mode & 0o777)
    os.replace(temporary,target)


def deploy():
    root=CURRENT.resolve();stamp=time.strftime('%Y%m%d-%H%M%S')
    backup=ASSETS/('affection-switch-before-'+stamp);backup.mkdir(mode=0o700)
    with tarfile.open(ASSETS/'affection-switch-20261022.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!={'whalegirl_linux/'+n for n in NAMES} or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected archive members')
        source={Path(m.name).name:archive.extractfile(m).read() for m in members}
    initial=api('config');pid=core_pid()
    original={n:(root/'whalegirl_linux'/n).read_bytes() for n in NAMES}
    owners={n:(root/'whalegirl_linux'/n).stat() for n in NAMES}
    for n in NAMES:shutil.copy2(root/'whalegirl_linux'/n,backup/n)
    if CURRENT.resolve()!=root:raise RuntimeError('Release changed before static update')
    try:
        for n in NAMES:replace(root/'whalegirl_linux'/n,source[n],stamp,owners[n])
        for n in NAMES:
            route='/' if n=='web.html' else '/'+n+'?v=20261022-affection-switch'
            with opener.open('http://127.0.0.1:8092'+route,timeout=10) as response:
                if response.read()!=source[n]:raise RuntimeError('Served asset differs from published source')
        live=api('config');state=api('state')
        if live['affection_enabled']!=initial['affection_enabled'] or live['enabled']!=initial['enabled']:
            raise RuntimeError('A runtime toggle changed during static deployment')
        if core_pid()!=pid:raise RuntimeError('Core process changed during static deployment')
        result={'release':str(root),'backup':str(backup),'affection_enabled':live['affection_enabled'],
            'enabled':live['enabled'],'gateway_status':state['gateway']['status'],'paused':state['paused_reason'],
            'last_error':state['last_error'],'core_pid_unchanged':True,'core_pid':pid,
            'hashes':{n:hashlib.sha256(source[n]).hexdigest() for n in NAMES}}
        (ASSETS/'affection-switch-deployment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
    except BaseException:
        for n in NAMES:replace(root/'whalegirl_linux'/n,original[n],stamp+'-rollback',owners[n])
        raise


if __name__=='__main__':deploy()

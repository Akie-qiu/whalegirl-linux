"""Install picture libraries and queue; preserve the running native WeChat session."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import time
import urllib.request

BASE=Path('/opt/whalegirl-linux')
ASSETS=Path('/opt/whalegirl-linux-assets')
DATA=Path('/var/lib/whalegirl-linux/core')
TOOLS=Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
NAME='whalegirl-wechat-candidate-4118'
EXPECTED=json.loads("{\"whalegirl_linux/settings.py\":\"f1e7b5135b7235371ef1e9c12129800c42f6f3815df780e066fb3022cf223120\",\"whalegirl_linux/runtime.py\":\"31a0b9c36c0a2f85112f2f75f22b3c534bea562e43eba47815076d66b54f2ad6\",\"whalegirl_linux/app.py\":\"ddecefd7d44e44496576dd53060f89eef9d8de33c5558ab5e38bce4376855ccf\",\"whalegirl_linux/gateway.py\":\"1a0c53fe38f145dc9669c3727afd77aee48f9791ea9a917422eee85656ec5d7d\",\"whalegirl_linux/safety.py\":\"512984fe158b65fb192229870a7a846b85b6ca87c312fa1a26109144b1c8dc19\",\"whalegirl_linux/console.js\":\"67ef0319abd439c6072ea3af2ee6877edb427b7a813324b50c5548e76cf9d2f4\",\"whalegirl_linux/moments.py\":\"5ef14f0eddec8a49666fbd2cf1c767cf7ac4d2429e998333390578ba22667a07\",\"whalegirl_linux/moments.js\":\"8339fd15d0f6448f391a71984fb7f19e92d471d45974d8a12e1f6c18e934049f\",\"whalegirl_linux/web.html\":\"d697b26050ae94455e43f4e1c0e75c213b8b835ee43c3a1db6cb868a6f2dbbcc\",\"deploy/chat_select_linux.py\":\"ecd75c99381613123d9df4d9d55aeed0c408f6c4c42691f292a78485b49d0fbd\",\"deploy/moments_ui.py\":\"dd9a548aceec7c5bd37373751d001c2a081e4015e926ad01669b32ad7a7224ba\"}")
NATIVE_EXPECTED=json.loads("{\"chat_select_linux.py\":\"ecd75c99381613123d9df4d9d55aeed0c408f6c4c42691f292a78485b49d0fbd\",\"moments_ui.py\":\"dd9a548aceec7c5bd37373751d001c2a081e4015e926ad01669b32ad7a7224ba\"}")
FILES=set(EXPECTED)|{'whalegirl_linux/images.py','whalegirl_linux/images.js','whalegirl_linux/images.css',
    'deploy/native_image_send.py','tests/test_images.py','IMAGE_TASKS.md','deploy/deploy_images.py'}

def command(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()

def api(path,body=None):
    token=(DATA/'admin.token').read_text().strip()
    request=urllib.request.Request('http://127.0.0.1:8092/api/'+path,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)

def install(path,data):
    owner=(path if path.exists() else path.parent).stat()
    pending=path.with_name(path.name+'.pictures-pending')
    pending.write_bytes(data);os.chown(pending,owner.st_uid,owner.st_gid)
    pending.chmod((owner.st_mode&0o777) if path.exists() else 0o640)
    pending.replace(path)

def bind_chat_helper():
    info=json.loads(command('docker','inspect',NAME))[0]
    code='import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).read_bytes()==pathlib.Path(sys.argv[2]).read_bytes() else 1)'
    check=['docker','exec',NAME,'python3','-c',code,'/data/native-tools/chat_select_linux.py','/opt/tools/chat-select.py']
    if subprocess.run(check,capture_output=True,timeout=10).returncode:
        copy='import pathlib,sys;pathlib.Path(sys.argv[2]).write_bytes(pathlib.Path(sys.argv[1]).read_bytes())'
        copied=subprocess.run(['docker','exec',NAME,'python3','-c',copy,
            '/data/native-tools/chat_select_linux.py','/opt/tools/chat-select.py'],capture_output=True,timeout=10)
        if copied.returncode:
            command('nsenter','--target',str(info['State']['Pid']),'--mount','--root','--wd','--',
                'mount','--bind','/data/native-tools/chat_select_linux.py','/opt/tools/chat-select.py')
    subprocess.run(check,check=True,timeout=10)

def main():
    import fcntl
    lock=open(ASSETS/'pictures-deploy.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    root=(BASE/'current').resolve()
    for name,expected in EXPECTED.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Source changed before deployment: '+name)
    for name,expected in NATIVE_EXPECTED.items():
        if hashlib.sha256((TOOLS/name).read_bytes()).hexdigest()!=expected:
            raise RuntimeError('Native source changed: '+name)
    with tarfile.open(ASSETS/'pictures-20261023.tar') as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=FILES or not all(m.isfile() for m in members):
            raise RuntimeError('Unexpected deployment members')
        contents={m.name:archive.extractfile(m).read() for m in members}
    for name,data in contents.items():
        if name.endswith('.py'):compile(data,name,'exec')
    for _ in range(30):
        if not api('state').get('processing'):break
        time.sleep(.5)
    else:raise RuntimeError('Chat busy; defer update')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        count=db.execute("SELECT count(*) FROM outbound_actions WHERE state='sending'").fetchone()[0]
        count+=db.execute("SELECT count(*) FROM moments_actions WHERE state='sending'").fetchone()[0]
        if count:raise RuntimeError('An outbound action is still sending')
    native_before=command('docker','exec',NAME,'pgrep','-x','wechat')
    reader_before=command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')
    backup=ASSETS/('pictures-before-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
    targets={root/name:data for name,data in contents.items()}
    targets.update({TOOLS/Path(name).name:contents[name] for name in
        ('deploy/chat_select_linux.py','deploy/moments_ui.py','deploy/native_image_send.py')})
    previous={}
    for index,(path,data) in enumerate(targets.items()):
        if path.exists():
            previous[path]=path.read_bytes();shutil.copy2(path,backup/(str(index)+'-'+path.name))
    shutil.copy2(DATA/'settings.json',backup/'settings.json')
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as source:
        with sqlite3.connect(backup/'state.sqlite3') as destination:source.backup(destination)
    subprocess.run(['systemctl','stop','whalegirl-linux'],check=True,timeout=30)
    try:
        if (BASE/'current').resolve()!=root:raise RuntimeError('Release changed')
        for path,data in targets.items():install(path,data)
        bind_chat_helper()
        subprocess.run(['systemctl','start','whalegirl-linux'],check=True,timeout=30)
        for _ in range(40):
            try:api('images');break
            except Exception:time.sleep(.5)
        else:raise RuntimeError('Image API did not become ready')
        api('images/config',{'values':{'image_model':'gpt-image-2.5'}})
        if command('docker','exec',NAME,'pgrep','-x','wechat')!=native_before:
            raise RuntimeError('Native WeChat process changed')
        if command('systemctl','show','whalegirl-native-reader','-p','MainPID','--value')!=reader_before:
            raise RuntimeError('Reader process changed')
        result={'at':time.time(),'release':str(root),'backup':str(backup),'wechat_pid_preserved':True,
            'reader_pid_preserved':True,'generation_model':'gpt-image-2.5',
            'core_enabled':api('config')['enabled'],'image_key_configured':api('images')['image_api_key_configured'],
            'hashes':{name:hashlib.sha256(data).hexdigest() for name,data in contents.items()}}
        (ASSETS/'pictures-deployment.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except Exception:
        subprocess.run(['systemctl','stop','whalegirl-linux'],check=False,timeout=30)
        for path,data in previous.items():install(path,data)
        try:bind_chat_helper()
        finally:subprocess.run(['systemctl','start','whalegirl-linux'],check=False,timeout=30)
        raise

if __name__=='__main__':main()


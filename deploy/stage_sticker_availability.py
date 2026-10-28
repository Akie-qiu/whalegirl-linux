"""Apply only the availability fix to the current production core."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

from stage_memory_hub import merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/sticker-availability'
FILES=['whalegirl_linux/stickers.py','whalegirl_linux/runtime.py','tests/test_sticker_availability.py']


def remote(code):
    return subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3','-'],
        input=code,text=True,capture_output=True,encoding='utf-8',check=True).stdout


def main():
    live=json.loads(remote("import base64,json,pathlib\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nfiles="+repr(FILES)+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in files}}))"))
    before={};after={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name.startswith('tests/'):
            if raw is not None:raise RuntimeError('New test already present in production')
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            value,_=merge_delta((OUT/'local-before'/Path(name).name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),raw.decode('utf-8'),name)
        compile(value,name,'exec')
        path=OUT/'payload'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8',newline='\n')
        after[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    folder='/opt/whalegirl-linux-assets/sticker-availability-'+time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path('+repr(folder)+').mkdir(mode=0o700)')
    subprocess.run(['scp.exe','-q',str(OUT/'release.tar'),str(OUT/'manifest.json'),
        str(ROOT/'deploy/deploy_sticker_availability.py'),'root@120.46.186.50:'+folder+'/'],check=True)
    (OUT/'remote-folder.txt').write_text(folder,encoding='utf-8')
    subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3',
        folder+'/deploy_sticker_availability.py',folder+'/release.tar',folder+'/manifest.json'],check=True)


if __name__=='__main__':main()

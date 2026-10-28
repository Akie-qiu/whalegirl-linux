"""Stage only the pictured-chat JSON recovery fix against current production."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from stage_memory_hub import merge_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/model-format-guard'
FILES=['whalegirl_linux/runtime.py','tests/test_model_format_guard.py','tests/test_runtime_error_diagnostics.py']


def remote(code):
    return subprocess.run(['ssh.exe','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3','-'],
        input=code,text=True,capture_output=True,encoding='utf-8',timeout=60,check=True).stdout


def main():
    live=json.loads(remote("import base64,json,pathlib\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="+repr(FILES)+
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() if (r/n).is_file() else None for n in names}}))"))
    before={};after={}
    for name in FILES:
        raw=base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        before[name]=hashlib.sha256(raw).hexdigest() if raw is not None else None
        if name.startswith('tests/'):
            value=(ROOT/name).read_text(encoding='utf-8')
        else:
            saved=OUT/'server-before'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(raw)
            value,_=merge_delta((OUT/'local-before'/name).read_text(encoding='utf-8'),
                (ROOT/name).read_text(encoding='utf-8'),raw.decode('utf-8'),name)
        compile(value,name,'exec')
        target=OUT/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(value,encoding='utf-8',newline='\n');after[name]=hashlib.sha256(target.read_bytes()).hexdigest()
    (OUT/'manifest.json').write_text(json.dumps({'root':live['root'],'before':before,'after':after},indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(OUT/'payload'/name,arcname=name)
    print('Staged model-format guard against',live['root'])


if __name__=='__main__':main()

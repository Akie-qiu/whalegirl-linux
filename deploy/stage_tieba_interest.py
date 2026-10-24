"""Stage the reviewed Tieba update with a checked live baseline; no service changes."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts'/'tieba-interest'
FILES = ['whalegirl_linux/tieba.py','whalegirl_linux/tieba.js','whalegirl_linux/settings.py',
         'whalegirl_linux/runtime.py','TIEBA.md']


def remote(code):
    return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','root@120.46.186.50','python3 -'],
        input=code,capture_output=True,text=True,encoding='utf-8',check=True).stdout


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    code="import hashlib,json,pathlib;root=pathlib.Path('/opt/whalegirl-linux/current').resolve();print(json.dumps({'root':str(root),'before':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in "+repr(FILES)+"},'python':[str(p) for p in [pathlib.Path('/opt/whalegirl-linux/venv/bin/python'),pathlib.Path('/opt/whalegirl-linux/.venv/bin/python')] if p.exists()]}))"
    manifest=json.loads(remote(code))
    expected={'whalegirl_linux/tieba.py':'07f9f6e2064d8a9e537ed429dfa75882255f3920a4fce9afa2cbdf6b9d8ff79f',
        'whalegirl_linux/tieba.js':'d7537548d928c3d924a04c7102414b117d04396e0a888b5d966e6f4cab241316',
        'whalegirl_linux/settings.py':'3862ac784a31fac2a5c6844fb4f9fad46d7c73b95a4a840c9c84b14706723d1a',
        'TIEBA.md':'9f14c60bd333d81996b8431ef71d697123fb14612a2df9c7f3567632d3173ad0'}
    baseline=OUT/'runtime-before.py'
    subprocess.run(['scp','-q','root@120.46.186.50:'+manifest['root']+'/whalegirl_linux/runtime.py',str(baseline)],check=True)
    source=(ROOT/'whalegirl_linux/runtime.py').read_text(encoding='utf-8')
    old="        system += self.tieba.prompt('\\n'.join(own_text(m) for m in batch))"
    new="        tieba_recall = {}\n        system += self.tieba.prompt('\\n'.join(own_text(m) for m in batch), tieba_recall)\n        timing['tieba_recall'] = tieba_recall"
    if source.count(new)!=1 or source.replace(new,old)!=baseline.read_text(encoding='utf-8'):
        raise RuntimeError('Runtime contains other server differences')
    expected['whalegirl_linux/runtime.py']=hashlib.sha256(baseline.read_bytes()).hexdigest()
    if manifest['before']!=expected:
        raise RuntimeError('Server baseline changed: '+str([k for k in FILES if manifest['before'][k]!=expected[k]]))
    manifest['after']={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(ROOT/name,arcname=name)
    folder='/opt/whalegirl-linux-assets/tieba-interest-update-'+time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path('+repr(folder)+').mkdir(mode=0o700)')
    subprocess.run(['scp','-q',str(OUT/'release.tar'),str(OUT/'manifest.json'),
        str(ROOT/'deploy/deploy_tieba_interest.py'),'root@120.46.186.50:'+folder+'/'],check=True)
    (OUT/'remote-path.txt').write_text(folder,encoding='utf-8')
    print(json.dumps({'staged':folder,'files':len(FILES),'python':manifest['python']}))


if __name__=='__main__':main()

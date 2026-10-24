"""Stage the three share-excerpt files against the last verified release."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

from stage_tieba_interest import remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts'/'tieba-excerpt'
FILES = ['whalegirl_linux/tieba.py', 'whalegirl_linux/tieba.js', 'TIEBA.md']


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    prior=json.loads((ROOT/'artifacts/tieba-interest/manifest.json').read_text(encoding='utf-8'))
    code="import hashlib,json,pathlib;root=pathlib.Path('/opt/whalegirl-linux/current').resolve();print(json.dumps({'root':str(root),'before':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in "+repr(FILES)+"}}))"
    manifest=json.loads(remote(code))
    if manifest['root']!=prior['root'] or manifest['before']!={k:prior['after'][k] for k in FILES}:
        raise RuntimeError('Live baseline changed; no files staged')
    manifest['after']={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with tarfile.open(OUT/'release.tar','w') as archive:
        for name in FILES:archive.add(ROOT/name,arcname=name)
    folder='/opt/whalegirl-linux-assets/tieba-excerpt-update-'+time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path('+repr(folder)+').mkdir(mode=0o700)')
    subprocess.run(['scp','-q',str(OUT/'release.tar'),str(OUT/'manifest.json'),
        str(ROOT/'deploy/deploy_tieba_interest.py'),str(ROOT/'deploy/deploy_tieba_excerpt.py'),
        'root@120.46.186.50:'+folder+'/'],check=True)
    (OUT/'remote-path.txt').write_text(folder,encoding='utf-8')
    print(json.dumps({'staged':folder,'files':len(FILES)}))


if __name__=='__main__':main()

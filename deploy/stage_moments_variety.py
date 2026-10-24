"""Stage only reviewed changes against the live baseline captured this session."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

from stage_tieba_interest import remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts'/'moments-variety'
FILES = ['whalegirl_linux/moments.py', 'whalegirl_linux/moments.js',
         'whalegirl_linux/settings.py', 'MOMENTS.md']


def main():
    baseline = json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
    code = "import hashlib,json,pathlib;root=pathlib.Path('/opt/whalegirl-linux/current').resolve();print(json.dumps({'root':str(root),'before':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in " + repr(FILES) + "}}))"
    manifest = json.loads(remote(code))
    if manifest != baseline:
        raise RuntimeError('Live baseline changed; review before staging')
    for name, expected in baseline['before'].items():
        if hashlib.sha256((OUT/'server-before'/name).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Baseline snapshot mismatch: '+name)
    manifest['after'] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}
    (OUT/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    with tarfile.open(OUT/'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(ROOT/name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/moments-variety-update-'+time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path('+repr(folder)+').mkdir(mode=0o700)')
    subprocess.run(['scp', '-q', str(OUT/'release.tar'), str(OUT/'manifest.json'),
        str(ROOT/'deploy/deploy_tieba_interest.py'), str(ROOT/'deploy/deploy_moments_variety.py'),
        'root@120.46.186.50:'+folder+'/'], check=True)
    (OUT/'remote-path.txt').write_text(folder, encoding='utf-8')
    print(json.dumps({'staged': folder, 'files': len(FILES)}))


if __name__ == '__main__':
    main()

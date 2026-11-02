"""Stage the independent model-profile settings release."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'model-profiles-20261031'
FILES = [
    'whalegirl_linux/settings.py',
    'whalegirl_linux/app.py',
    'whalegirl_linux/console.js',
    'whalegirl_linux/web.html',
]


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
        timeout=60,
    ).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = json.loads(remote(
        "import base64,json,pathlib;"
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "names=" + repr(FILES) + ";"
        "print(json.dumps({'root':str(root),'files':{n:base64.b64encode((root/n).read_bytes()).decode() for n in names}}))"
    ))
    before, after = {}, {}
    payload = OUT / 'payload'
    for name in FILES:
        local = (ROOT / name).read_bytes()
        before[name] = hashlib.sha256(base64.b64decode(live['files'][name])).hexdigest()
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(local)
        after[name] = hashlib.sha256(local).hexdigest()
        saved = OUT / 'server-before' / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(base64.b64decode(live['files'][name]))
    manifest = {'root': live['root'], 'before': before, 'after': after}
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/model-profiles-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_model_profiles.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_model_profiles.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

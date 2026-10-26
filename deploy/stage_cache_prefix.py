"""Stage the prompt-prefix cache update against the exact live release."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'artifacts'/'cache-prefix'
FILES = [
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/affection.py',
    'whalegirl_linux/image_tools.py',
]


def remote(code):
    return subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    code = (
        "import hashlib,json,pathlib;root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "print(json.dumps({'root':str(root),'before':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() "
        "for n in " + repr(FILES) + "}}))"
    )
    manifest = json.loads(remote(code))
    local_after = {
        name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        for name in FILES
    }
    # Compare the captured live release to the committed base, then publish
    # only the current cache-prefix working-tree versions.
    before = {}
    for name in FILES:
        source = subprocess.check_output(['git', 'show', 'HEAD:' + name])
        before[name] = hashlib.sha256(source).hexdigest()
    if set(manifest['before']) != set(FILES) or manifest['before'] != before:
        raise RuntimeError('Live baseline differs from local HEAD; no files staged')
    manifest['after'] = local_after
    manifest_path = OUT/'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT/'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(ROOT/name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/cache-prefix-update-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run(
        ['scp', '-q', str(archive_path), str(manifest_path),
         str(ROOT/'deploy/deploy_cache_prefix.py'),
         'root@120.46.186.50:' + folder + '/'],
        check=True,
    )
    (OUT/'remote-path.txt').write_text(folder, encoding='utf-8')
    print(json.dumps({'staged': folder, 'files': len(FILES)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Ship the missing reference-variant method, preserving other live sources."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/image-reference-enqueue'
FILES = ['whalegirl_linux/image_references.py', 'tests/test_moments_references.py']


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def main():
    baseline = json.loads((OUT / 'before.json').read_text(encoding='utf-8-sig'))
    live = json.loads(remote(
        "import hashlib,json,pathlib;"
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "names=" + repr(FILES) + ";"
        "print(json.dumps({'root':str(root),'before':{n:(hashlib.sha256((root/n).read_bytes()).hexdigest() "
        "if (root/n).is_file() else None) for n in names}}))"
    ))
    if live['root'] != baseline['root'] or live['before'][FILES[0]] != baseline['sha256']:
        raise RuntimeError('Production changed since the diagnosed failure')
    old = (OUT / 'image_references-before.py').read_text(encoding='utf-8').splitlines(keepends=True)
    new = (ROOT / FILES[0]).read_text(encoding='utf-8').splitlines(keepends=True)
    changed = [op for op in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
               if op[0] != 'equal']
    if len(changed) != 1 or changed[0][0] != 'insert':
        raise RuntimeError('Only the missing method may be added in this deployment')
    _, _, _, start, stop = changed[0]
    if '    def detect_variant(' not in ''.join(new[start:stop]):
        raise RuntimeError('Unexpected reference-library change')

    payload = OUT / 'payload'
    for name in FILES:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (ROOT / name).read_bytes()
        compile(content, name, 'exec')
        target.write_bytes(content)
    manifest = {**live, 'after': {
        name: hashlib.sha256((payload / name).read_bytes()).hexdigest() for name in FILES
    }}
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/image-reference-enqueue-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_image_reference_enqueue.py'), 'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_image_reference_enqueue.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

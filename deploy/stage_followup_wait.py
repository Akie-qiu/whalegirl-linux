"""Deploy only the reviewed followup-wait and sticker retry changes."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/followup-wait'
SOURCE_NAMES = [
    'runtime.py', 'store.py', 'gateway.py', 'safety.py', 'settings.py',
    'upstream/ai_engine.py', 'habits.js', 'console.js', 'web.html',
]
TESTS = ['test_chat_habits.py', 'test_media_delivery.py', 'test_model_controls.py']
FILES = ['whalegirl_linux/' + name for name in SOURCE_NAMES] + ['tests/' + name for name in TESTS]


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def selected_changes(name, allowed, expected_changes):
    """Keep unrelated, pre-existing workspace changes out of this release."""
    before = (OUT / 'before/whalegirl_linux' / name).read_text(encoding='utf-8').splitlines(keepends=True)
    after = (ROOT / 'whalegirl_linux' / name).read_text(encoding='utf-8').splitlines(keepends=True)
    ops = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    changed = {index for index, (op, *_) in enumerate(ops) if op != 'equal'}
    if len(changed) != expected_changes or not allowed <= changed:
        raise RuntimeError('Reviewed source sections changed: ' + name)
    result = []
    for index, (op, i, j, k, l) in enumerate(ops):
        result.extend(after[k:l] if index in allowed else before[i:j])
    return ''.join(result)


def main():
    baseline = json.loads((OUT / 'before.json').read_text(encoding='utf-8-sig'))
    live = json.loads(remote(
        "import hashlib,json,pathlib;"
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "names=" + repr(FILES) + ";"
        "print(json.dumps({'root':str(root),'before':{n:(hashlib.sha256((root/n).read_bytes()).hexdigest() "
        "if (root/n).is_file() else None) for n in names}}))"
    ))
    if live['root'] != baseline['root'] or any(
        live['before'].get(name) != digest for name, digest in baseline['before'].items()
    ):
        raise RuntimeError('Production changed since the reviewed snapshot')

    payload = OUT / 'payload'
    for name in SOURCE_NAMES:
        target = payload / 'whalegirl_linux' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'runtime.py':
            source = selected_changes(name, {
                1, 3, 5, 7, 9, 11, 13, 15, 17, 31, 33, 45, 47, 49, 53,
            }, 27)
        elif name == 'upstream/ai_engine.py':
            # Keep the existing streaming implementation unchanged.
            source = selected_changes(name, {1, 3, 5}, 5)
        elif name == 'web.html':
            source = selected_changes(name, {5}, 3)
            source = source.replace('/console.js?v=20261027-context-range', '/console.js?v=20261028-followup')
            source = source.replace('/habits.js?v=20261026-bento-ui', '/habits.js?v=20261028-followup')
        else:
            source = (ROOT / 'whalegirl_linux' / name).read_text(encoding='utf-8')
        if name.endswith('.py'):
            compile(source, name, 'exec')
        target.write_text(source, encoding='utf-8', newline='\n')

    for name in TESTS:
        target = payload / 'tests' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / 'tests' / name).read_bytes())

    manifest = {**live, 'after': {
        name: hashlib.sha256((payload / name).read_bytes()).hexdigest() for name in FILES
    }}
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/followup-wait-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_followup_wait.py'), 'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_followup_wait.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

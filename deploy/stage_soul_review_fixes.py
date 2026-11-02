"""Stage only the reviewed mood and JSON-parser corrections."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'soul-fixes-20261031'
FILES = [
    'whalegirl_linux/conversation.py',
    'whalegirl_linux/autonomy.py',
    'whalegirl_linux/stickers.py',
    'tests/test_soul_upgrade_v5.py',
    'tests/test_soul_upgrade_regressions.py',
]


def remote(source):
    result = subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'], input=source,
        capture_output=True, text=True, encoding='utf-8', check=True, timeout=45,
    )
    return result.stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = json.loads(remote(
        "from pathlib import Path;import base64,json;"
        "root=Path('/opt/whalegirl-linux/current').resolve();names=" + repr(FILES) + ';'
        "print(json.dumps({'root':str(root),'files':{n:base64.b64encode((root/n).read_bytes()).decode() if (root/n).is_file() else None for n in names}}))"
    ))
    before, after = {}, {}
    review_root = ROOT / 'artifacts' / 'gemini-review-20261031-1548' / 'after'
    for name in FILES:
        raw = base64.b64decode(live['files'][name]) if live['files'][name] is not None else None
        # Abort instead of overwriting edits made after the review. Line-ending
        # differences have no semantic effect; all other changes need review.
        if raw is not None:
            if not (review_root / name).is_file():
                raise RuntimeError('Unexpected existing production file: ' + name)
            reference = (review_root / name).read_text(encoding='utf-8')
            if raw.decode('utf-8').replace('\r\n', '\n') != reference.replace('\r\n', '\n'):
                raise RuntimeError('Production source changed since review: ' + name)
        before[name] = hashlib.sha256(raw).hexdigest() if raw is not None else None
        content = (ROOT / name).read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8')
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        after[name] = hashlib.sha256(content).hexdigest()
        if raw is not None:
            saved = OUT / 'server-before' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(raw)
    manifest = OUT / 'manifest.json'
    manifest.write_text(json.dumps({'root': live['root'], 'before': before, 'after': after}, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(OUT / 'payload' / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/soul-review-fixes-' + time.strftime('%Y%m%d-%H%M%S')
    remote('from pathlib import Path;Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-O', '-q', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        str(archive_path), str(manifest), str(ROOT / 'deploy/deploy_soul_review_fixes.py'),
        str(ROOT / 'deploy/deploy_model_profiles.py'), 'root@120.46.186.50:' + folder + '/',
    ], check=True, timeout=60)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_soul_review_fixes.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True, timeout=180)


if __name__ == '__main__':
    main()

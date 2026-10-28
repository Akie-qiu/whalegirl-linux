"""Stage the topic relevance and emotional-sticker patch against the live release."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'topic-sticker'
FILES = [
    'whalegirl_linux/context.py',
    'whalegirl_linux/conversation.py',
    'whalegirl_linux/learning.py',
    'whalegirl_linux/memory.py',
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/stickers.py',
    'whalegirl_linux/store.py',
    'whalegirl_linux/topic_relevance.py',
]


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    code = (
        "import hashlib,json,pathlib;"
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "names=" + repr(FILES) + ";"
        "print(json.dumps({'root':str(root),'before':{n:(hashlib.sha256((root/n).read_bytes()).hexdigest() "
        "if (root/n).is_file() else None) for n in names}}))"
    )
    manifest = json.loads(remote(code))
    manifest['after'] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in FILES
    }
    (OUT / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(ROOT / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/topic-sticker-update-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(OUT / 'release.tar'), str(OUT / 'manifest.json'),
        str(ROOT / 'deploy/deploy_topic_sticker.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    (OUT / 'remote-path.txt').write_text(folder, encoding='utf-8')
    print(json.dumps({'staged': folder, 'files': len(FILES)}, ensure_ascii=False))


if __name__ == '__main__':
    main()

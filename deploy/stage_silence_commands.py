"""Package and deploy the addressed silence-command handling."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'silence-commands'
FILES = ['whalegirl_linux/runtime.py', 'tests/test_silence_commands.py']


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
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(ROOT / name, arcname=name)

    folder = '/opt/whalegirl-linux-assets/silence-commands-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1_000_000_000)
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_silence_commands.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_silence_commands.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

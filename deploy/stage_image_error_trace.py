"""Add sanitized source locations to core processing errors, then deploy."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'image-error-trace'
FILES = ['whalegirl_linux/runtime.py', 'tests/test_runtime_error_diagnostics.py']


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    payload = OUT / 'payload'
    runtime_path = payload / FILES[0]
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    remote_info = json.loads(remote(
        "import hashlib,json,pathlib;"
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve();"
        "names=" + repr(FILES) + ";"
        "print(json.dumps({'root':str(root),'before':{n:(hashlib.sha256((root/n).read_bytes()).hexdigest() "
        "if (root/n).is_file() else None) for n in names}}))"
    ))
    subprocess.run([
        'scp.exe', '-q', 'root@120.46.186.50:/opt/whalegirl-linux/current/' + FILES[0],
        str(runtime_path),
    ], check=True)
    source = runtime_path.read_text(encoding='utf-8')
    import_old = 'import time\nimport unicodedata\n'
    import_new = 'import time\nimport traceback\nimport unicodedata\n'
    if source.count(import_old) != 1:
        raise RuntimeError('Active runtime import anchor changed')
    source = source.replace(import_old, import_new, 1)
    anchor = '\ndef normal(value):\n'
    helper = (
        '\ndef exception_location(exc):\n'
        '    """Return a private-data-safe source location for a caught runtime error."""\n'
        '    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []\n'
        '    if not frames:\n'
        "        return 'unknown'\n"
        '    frame = frames[-1]\n'
        "    return f'{Path(frame.filename).name}:{frame.name}:{frame.lineno}'\n"
        '\n\ndef normal(value):\n'
    )
    if source.count(anchor) != 1:
        raise RuntimeError('Active runtime helper anchor changed')
    source = source.replace(anchor, helper, 1)
    error_old = 'self.last_error = f"处理异常：{type(exc).__name__}"'
    error_new = 'self.last_error = f"处理异常：{type(exc).__name__}（{exception_location(exc)}）"'
    if source.count(error_old) != 1:
        raise RuntimeError('Active worker error handler changed')
    runtime_path.write_text(source.replace(error_old, error_new, 1), encoding='utf-8', newline='\n')

    test_path = payload / FILES[1]
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_bytes((ROOT / FILES[1]).read_bytes())
    manifest = {
        'root': remote_info['root'],
        'before': remote_info['before'],
        'after': {
            name: hashlib.sha256((payload / name).read_bytes()).hexdigest()
            for name in FILES
        },
    }
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)

    folder = '/opt/whalegirl-linux-assets/image-error-trace-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_image_error_trace.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_image_error_trace.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

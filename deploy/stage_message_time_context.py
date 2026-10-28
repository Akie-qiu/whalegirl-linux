"""Attach Beijing send times to current model inputs; preserve other live context logic."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/message-time-context'
FILES = ['whalegirl_linux/context.py', 'tests/test_message_time_context.py']


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
        raise RuntimeError('Production context changed since inspection')
    source = (OUT / 'context-before.py').read_text(encoding='utf-8')
    patches = [
        ('\ndef line(row, labels):',
         "\ndef message_time(stamp):\n"
         "    return datetime.fromtimestamp(stamp, ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')\n\n\n"
         'def line(row, labels):'),
        ('              "\\n【当前需要回复的消息｜本轮主题优先】\\n" +',
         '              "\\n【本轮处理时间｜北京时间】\\n" + message_time(time.time()) +\n'
         '              "\\n【当前需要回复的消息｜本轮主题优先】\\n" +'),
        ('f"[{m[\'sender\']}] {clip(',
         'f"[{message_time(m[\'timestamp\'])}｜{m[\'sender\']}] {clip('),
    ]
    for old, new in patches:
        if source.count(old) != 1:
            raise RuntimeError('Reviewed context anchor changed')
        source = source.replace(old, new, 1)

    payload = OUT / 'payload'
    for name in FILES:
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = source.encode('utf-8') if name == FILES[0] else (ROOT / name).read_bytes()
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
    folder = '/opt/whalegirl-linux-assets/message-time-context-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_message_time_context.py'), 'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_message_time_context.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

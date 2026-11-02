"""Stage and deploy the model-card endpoint preservation fix."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'model-endpoint-preservation-20261031'
FILES = ['whalegirl_linux/console.js', 'whalegirl_linux/web.html']


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8', check=True,
        timeout=60,
    ).stdout


def patch_console(live):
    local = (ROOT / 'whalegirl_linux/console.js').read_text(encoding='utf-8')
    local_start = local.index('const MODEL_PRESETS')
    local_end = local.index('function chatName')
    block = local[local_start:local_end]
    if 'MODEL_ENDPOINT_MEMORY_KEY' not in block:
        raise RuntimeError('Local model endpoint preservation patch is missing')
    start = live.index('const MODEL_PRESETS')
    end = live.index('function chatName')
    return live[:start] + block + live[end:]


def patch_web(live):
    old = '点卡片填入对应配置，再用下方原有字段编辑或保存；切换在保存后生效。'
    new = '切换卡片时只调整模型和协议；已有自定义接口地址会保留，点击“自定义接口”可恢复上次地址。修改保存后生效。'
    if new in live:
        return live
    if live.count(old) != 1:
        raise RuntimeError('Model preset hint anchor is not unique in production web.html')
    return live.replace(old, new, 1)


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
        raw = base64.b64decode(live['files'][name])
        before[name] = hashlib.sha256(raw).hexdigest()
        live_text = raw.decode('utf-8')
        value = patch_console(live_text) if name.endswith('console.js') else patch_web(live_text)
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        saved = OUT / 'server-before' / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(raw)
    manifest = {'root': live['root'], 'before': before, 'after': after}
    manifest_path = OUT / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    archive_path = OUT / 'release.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)
    folder = '/opt/whalegirl-linux-assets/model-endpoint-preservation-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib;pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(archive_path), str(manifest_path),
        str(ROOT / 'deploy/deploy_model_endpoint_preservation.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_model_endpoint_preservation.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], check=True)


if __name__ == '__main__':
    main()

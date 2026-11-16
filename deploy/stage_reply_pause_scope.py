"""Stage and deploy the scoped reply-pause repair from the live release."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

from stage_tool_reply_guard import merge_delta

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/reply-pause-scope'
FILES = [
    'whalegirl_linux/safety.py',
    'whalegirl_linux/recovery.py',
    'whalegirl_linux/runtime.py',
    'whalegirl_linux/moments.py',
    'deploy/native_recovery.py',
    'deploy/chat_select_linux.py',
    'tests/test_ui_recovery.py',
]


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8',
        check=True, timeout=60,
    ).stdout


def live_files():
    code = (
        'import base64,hashlib,json,pathlib\n'
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve()\n"
        f'names={FILES!r}\n'
        "print(json.dumps({'root':str(root),'files':{n:base64.b64encode((root/n).read_bytes()).decode() if (root/n).is_file() else None for n in names}}))"
    )
    return json.loads(remote(code))


def build_payload(live):
    before = {}
    after = {}
    anchors = {}
    payload = OUT / 'payload'
    prior_manifest = OUT / 'manifest.json'
    prior_payload = {}
    if prior_manifest.is_file():
        for name in FILES:
            path = payload / name
            if path.is_file():
                prior_payload[name] = path.read_text(encoding='utf-8')
    for name in FILES:
        raw = base64.b64decode(live['files'][name])
        before[name] = hashlib.sha256(raw).hexdigest()
        (OUT / 'server-before' / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / 'server-before' / name).write_bytes(raw)
        production = raw.decode('utf-8')
        if name == 'whalegirl_linux/runtime.py':
            old = "        safety_held = self.safety.state(account).get('hold_reason') or self.paused_reason or not settings.enabled\n"
            new = (
                "        # Keep collecting messages while automatic UI recovery is pending;\n"
                "        # only an actual hold or the global switch changes their dispatch state.\n"
                "        safety_held = self.safety.held(account, include_recovery=False) or self.paused_reason or not settings.enabled\n"
            )
            if new in production:
                value = production
                anchors[name] = ['runtime poll safety gate already present']
            else:
                if production.count(old) != 1:
                    raise RuntimeError('Live runtime recovery anchor changed; nothing deployed')
                value = production.replace(old, new, 1)
                anchors[name] = ['runtime poll safety gate']
        elif name in prior_payload:
            value, anchors[name] = merge_delta(prior_payload[name],
                                               (ROOT / name).read_text(encoding='utf-8'),
                                               production, name)
        else:
            baseline = subprocess.check_output(
                ['git', 'show', 'HEAD:' + name], cwd=ROOT,
            ).decode('utf-8')
            local = (ROOT / name).read_text(encoding='utf-8')
            value, anchors[name] = merge_delta(baseline, local, production, name)
        if name.endswith('.py'):
            compile(value, name, 'exec')
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {'root': live['root'], 'before': before, 'after': after,
                'unique_line_anchors': anchors}
    (OUT / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(payload / name, arcname=name)
    return manifest


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = live_files()
    manifest = build_payload(live)
    folder = '/opt/whalegirl-linux-assets/reply-pause-scope-' + time.strftime('%Y%m%d-%H%M%S')
    remote('import pathlib; pathlib.Path(' + repr(folder) + ').mkdir(mode=0o700)')
    subprocess.run([
        'scp.exe', '-q', str(OUT / 'release.tar'), str(OUT / 'manifest.json'),
        str(ROOT / 'deploy/deploy_reply_pause_scope.py'),
        'root@120.46.186.50:' + folder + '/',
    ], check=True)
    result = subprocess.run([
        'ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        'root@120.46.186.50', 'python3', folder + '/deploy_reply_pause_scope.py',
        folder + '/release.tar', folder + '/manifest.json',
    ], capture_output=True, text=True, encoding='utf-8', check=True, timeout=240)
    print(result.stdout.strip())
    print(json.dumps({'staged_files': len(FILES), 'remote_folder': folder,
                      'manifest': manifest}, ensure_ascii=False))


if __name__ == '__main__':
    main()

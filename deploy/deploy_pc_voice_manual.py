"""Guarded, small handshake patch; preserves current release and private data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request

BASE = Path('/opt/whalegirl-linux')
CURRENT = BASE / 'current'
DATA = Path('/var/lib/whalegirl-linux/core')
B = Path('/opt/whalegirl-linux-assets/pc-voice-manual-20261111')
EXPECTED = {
    'whalegirl_linux/audio_provider.py': '0d0b91338ffd1f1dfc539c6ecd52e2417bc135c6e91409aec3814042f19e0c3b',
    'deploy/pc_voice_bridge.py': '97911d9f308cb888aaad6bfffed32d237a4c8b266608cde3145a05012b3b831e',
    'tests/test_pc_voice.py': 'd3d227ae253e41dfdf8e3d4e05574c307f182d98464aa73d1213aca01e395b18',
}
FILES = [*EXPECTED, 'deploy/pc_voice_common.py']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    target = B / name
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    target.chmod(0o600)


def api(path, value=None):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        data=json.dumps(value).encode() if value is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def replace(source, destination, metadata):
    temp = destination.with_name(destination.name + '.manual-new')
    temp.write_bytes(source.read_bytes())
    os.chown(temp, metadata.st_uid, metadata.st_gid)
    temp.chmod(0o640)
    os.replace(temp, destination)  # Never modify a rollback hardlink in place.


def prepare():
    root = CURRENT.resolve()
    assert root.name == 'pc-voice-20261111-183639', 'Newer release exists'
    assert not (B / 'manifest.json').exists(), 'Already prepared'
    assert all(digest(root / n) == h for n, h in EXPECTED.items()), 'Concurrent source update'
    B.mkdir(mode=0o700, exist_ok=True)
    hashes = {str(p.relative_to(root)): digest(p) for p in root.joinpath('whalegirl_linux').rglob('*')
              if p.is_file() and p.suffix in ('.py', '.js', '.html', '.css')}
    for name in EXPECTED:
        target = B / 'before' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    for directory in ('whalegirl_linux', 'tests', 'deploy'):
        shutil.copytree(root / directory, B / 'check' / directory, copy_function=os.link,
            ignore=shutil.ignore_patterns('__pycache__', 'test-runs', 'artifacts', '.pytest_cache'))
    for name in FILES:
        target = B / 'check' / name
        replace(B / 'stage' / name, target, (root / name).stat() if (root / name).exists() else root.stat())
    private = {n: digest(DATA / n) for n in ('settings.json', 'persona-cards.json', 'admin.token', 'gateway.token')}
    save('manifest.json', {'root': str(root), 'before_hashes': EXPECTED,
        'after_hashes': {n: digest(B / 'stage' / n) for n in FILES},
        'private_hashes': private, 'source_hashes': hashes, 'prepared': time.time()})
    print(json.dumps({'prepared': True, 'backup': str(B), 'production_changed': False}))


def test():
    root = Path(json.loads((B / 'manifest.json').read_text())['root'])
    if not (B / 'check/styles').exists():
        shutil.copytree(root / 'styles', B / 'check/styles', copy_function=os.link)
    command = ['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
        '-p', 'CPUQuota=50%', '-p', 'MemoryMax=256M', '-p', 'WorkingDirectory=' + str(B / 'check'),
        '-E', 'PYTHONPATH=' + str(B / 'check/tests'), '/opt/whalegirl-linux/venv/bin/python',
        '-m', 'unittest', 'test_audio', 'test_pc_voice', 'test_native_voice_compat', 'test_voice_worker', '-q']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    (B / 'linux-tests.log').write_text(result.stdout)
    print(result.stdout[-2200:])
    assert result.returncode == 0, 'Linux tests failed'
    save('tests-passed.json', {'at': time.time(), 'returncode': result.returncode})


def activate():
    manifest = json.loads((B / 'manifest.json').read_text())
    root = Path(manifest['root'])
    assert CURRENT.resolve() == root
    assert (B / 'tests-passed.json').exists()
    for name, value in manifest['source_hashes'].items():
        assert digest(root / name) == value, 'Concurrent change: ' + name
    assert all(digest(root / n) == h for n, h in EXPECTED.items())
    assert all(digest(DATA / n) == h for n, h in manifest['private_hashes'].items())
    for name, value in manifest['after_hashes'].items():
        assert digest(B / 'stage' / name) == value
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        assert not db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone(), 'Audio work is active'
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    try:
        for name in FILES:
            replace(B / 'stage' / name, root / name, (root / name).stat() if (root / name).exists() else root.stat())
        import pwd
        user = pwd.getpwnam('whalegirl-ai')
        subprocess.run(['/opt/whalegirl-linux/venv/bin/python', '-c',
            'from whalegirl_linux.audio_provider import AudioProviderClient'],
            cwd=root, user=user.pw_uid, group=user.pw_gid, check=True)
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        for _ in range(25):
            time.sleep(1)
            try:
                health = api('/health')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks'):
                    save('activated.json', {'at': time.time(), 'health': health})
                    print(json.dumps({'activated': True, 'health': health}))
                    return
            except (OSError, ValueError):
                pass
        raise RuntimeError('Health check failed')
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
        for name in EXPECTED:
            replace(B / 'before' / name, root / name, (root / name).stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        raise


def enable_owner():
    assert (B / 'activated.json').exists()
    core, before = api('/api/config'), api('/api/audio')
    owners = set(core.get('owner_ids', [])) & set(core['allowed_chat_ids'])
    owners = sorted(x for x in owners if not x.endswith('@chatroom'))
    assert len(owners) == 1, 'Owner private chat is ambiguous'
    settings = before['settings']
    assert settings['speech_profile'] == 'pc-voice'
    added = [x for x in owners if x not in settings['allowed_chat_ids']]
    save('owner-audio-before.json', {'revision': before['revision'], 'settings': settings})
    settings['allowed_chat_ids'] = list(dict.fromkeys([*settings['allowed_chat_ids'], *owners]))
    after = api('/api/audio/config', {'revision': before['revision'], 'settings': settings})
    save('owner-enabled.json', {'revision': after['revision'], 'added': added, 'at': time.time()})
    print(json.dumps({'owner_private_enabled': True, 'added_private_chats': len(added),
                      'audio_chat_count': len(after['settings']['allowed_chat_ids'])}))


def rollback():
    manifest = json.loads((B / 'manifest.json').read_text())
    root = Path(manifest['root'])
    assert CURRENT.resolve() == root, 'Newer release exists'
    assert all(digest(root / n) == h for n, h in manifest['after_hashes'].items()), 'Newer patch exists'
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    for name in EXPECTED:
        replace(B / 'before' / name, root / name, (root / name).stat())
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    # The unused small common module can remain; no data or source deletion.
    save('rolled-back.json', {'at': time.time()})
    print(json.dumps({'handshake_patch_rolled_back': True, 'chats_memories_and_audio_config_preserved': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'test', 'activate', 'enable_owner', 'rollback'])
    globals()[parser.parse_args().operation]()

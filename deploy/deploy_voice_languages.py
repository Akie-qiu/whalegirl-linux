"""Small hash-guarded language fix, no production Store construction or settings writes."""
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

B = Path('/opt/whalegirl-linux-assets/voice-languages-20261112')
CURRENT = Path('/opt/whalegirl-linux/current')
DATA = Path('/var/lib/whalegirl-linux/core')
FILES = ['whalegirl_linux/audio.py', 'whalegirl_linux/audio_provider.py', 'whalegirl_linux/audio_tools.py',
         'deploy/pc_voice_bridge.py', 'deploy/pc_voice_engine.py', 'tests/test_pc_voice.py', 'tests/test_voice_languages.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    target = B / name
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    target.chmod(0o600)


def api(path):
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def replace(source, target, metadata):
    temporary = target.with_name(target.name + '.language-new')
    temporary.write_bytes(source.read_bytes())
    os.chown(temporary, metadata.st_uid, metadata.st_gid)
    temporary.chmod(0o640)
    os.replace(temporary, target)


def source_check(manifest, after=False):
    assert str(CURRENT.resolve()) == manifest['root'], 'Current release changed; rebase the candidate'
    for name, expected in manifest['after' if after else 'before'].items():
        path = CURRENT.resolve() / name
        assert (sha(path) if path.exists() else None) == expected, 'Concurrent change: ' + name


def prepare():
    assert not (B / 'manifest.json').exists(), 'Already prepared; do not replace the baseline'
    expected = json.loads((B / 'expected.json').read_text())
    root = CURRENT.resolve()
    assert str(root) == expected['current']
    for name in FILES:
        path = root / name
        assert (sha(path) if path.exists() else None) == expected['files'][name], 'Concurrent change: ' + name
    assert shutil.disk_usage('/').free > 650 * 1024**2
    before = {}
    for name in FILES:
        path = root / name
        before[name] = sha(path) if path.exists() else None
        if path.exists():
            target = B / 'before' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    for name in ('settings.json', 'persona-cards.json'):
        shutil.copy2(DATA / name, B / ('before-' + name))
        (B / ('before-' + name)).chmod(0o600)
    for folder in ('whalegirl_linux', 'tests', 'deploy', 'styles'):
        if (root / folder).is_dir():
            shutil.copytree(root / folder, B / 'check' / folder, copy_function=os.link,
                ignore=shutil.ignore_patterns('__pycache__', 'artifacts', 'test-runs', '.pytest_cache'))
    (B / 'check/test-runs').mkdir()
    for name in FILES:
        assert (B / 'stage' / name).is_file()
        destination = B / 'check' / name
        replace(B / 'stage' / name, destination,
                (root / name).stat() if (root / name).exists() else destination.parent.stat())
    manifest = {'root': str(root), 'before': before,
                'after': {name: sha(B / 'stage' / name) for name in FILES}, 'at': time.time(),
                'private_hashes': {name: sha(DATA / name) for name in ('settings.json', 'persona-cards.json', 'admin.token', 'gateway.token')},
                'reader_pid': subprocess.check_output(['systemctl', 'show', 'whalegirl-native-reader.service', '-p', 'MainPID', '--value'], text=True).strip()}
    save('manifest.json', manifest)
    print(json.dumps({'prepared': True, 'files': len(FILES), 'backup': str(B)}))


def test():
    unit = 'whalegirl-voice-language-tests-20261112'
    runner = '''import faulthandler,unittest,pathlib
from unittest.mock import patch
original=pathlib.Path.read_text
def fixture_read(path,*args,**kwargs):
    if str(path)=="/proc/meminfo": return "MemAvailable: 1048576 kB\\n"
    return original(path,*args,**kwargs)
faulthandler.dump_traceback_later(40)
with patch.object(pathlib.Path,"read_text",fixture_read):
    r=unittest.main(module=None,argv=["unittest","test_audio","test_pc_voice","test_voice_dynamics","test_voice_languages"],verbosity=2,exit=False)
faulthandler.cancel_dump_traceback_later()
raise SystemExit(not r.result.wasSuccessful())'''
    command = ['systemd-run', '--unit=' + unit, '--quiet', '--wait', '--pipe', '--collect', '-p', 'CPUQuota=50%',
        '-p', 'MemoryMax=256M', '-p', 'WorkingDirectory=' + str(B / 'check'),
        '-E', 'PYTHONPATH=' + str(B / 'check/tests'), '/opt/whalegirl-linux/venv/bin/python',
        '-c', runner]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    except subprocess.TimeoutExpired as exc:
        subprocess.run(['systemctl', 'stop', unit], check=False, timeout=15)
        (B / 'linux-tests-timeout.log').write_bytes((exc.stdout or b'') + (exc.stderr or b''))
        raise
    (B / 'linux-tests.log').write_text(result.stdout + result.stderr)
    print((result.stdout + result.stderr)[-2400:])
    assert result.returncode == 0, 'Candidate checks failed; production unchanged'
    save('tested.json', {'at': time.time(), 'returncode': 0,
                        'unit_host_mem_available_fixture_kb': 1048576,
                        'production_resource_protection_changed': False})


def activate():
    manifest = json.loads((B / 'manifest.json').read_text())
    assert (B / 'tested.json').exists()
    source_check(manifest)
    assert all(sha(B / 'stage' / name) == expected for name, expected in manifest['after'].items())
    until = time.monotonic() + 120
    while True:
        with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
            active = db.execute("SELECT 1 FROM audio_jobs WHERE state IN ('generating','downloading','sending') LIMIT 1").fetchone()
            moments = db.execute("SELECT 1 FROM moments_actions WHERE state IN ('generating','sending') LIMIT 1").fetchone()
        if not active and not moments and not api('/api/state').get('processing'):
            break
        assert time.monotonic() < until, 'Work is active; candidate remains prepared'
        time.sleep(2)
    root = Path(manifest['root'])
    source_check(manifest)
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    try:
        for name in FILES:
            path = root / name
            replace(B / 'stage' / name, path, path.stat() if path.exists() else path.parent.stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        for _ in range(30):
            time.sleep(1)
            try:
                health = api('/health')
                if health.get('status') == 'ok' and not health.get('failed_background_tasks'):
                    save('activated.json', {'at': time.time(), 'health': health})
                    print(json.dumps({'activated': True, 'health': health}))
                    return
            except (OSError, ValueError):
                pass
        raise RuntimeError('Health did not recover')
    except BaseException:
        subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
        for name, expected in manifest['before'].items():
            if expected is not None:
                replace(B / 'before' / name, root / name, (root / name).stat())
        subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
        raise


def rollback():
    manifest = json.loads((B / 'manifest.json').read_text())
    source_check(manifest, after=True)
    root = Path(manifest['root'])
    subprocess.run(['systemctl', 'stop', 'whalegirl-linux.service'], check=True)
    for name, expected in manifest['before'].items():
        if expected is not None:
            replace(B / 'before' / name, root / name, (root / name).stat())
    subprocess.run(['systemctl', 'start', 'whalegirl-linux.service'], check=True)
    print(json.dumps({'rolled_back_code': True, 'settings_and_data_restored': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['prepare', 'test', 'activate', 'rollback'])
    globals()[parser.parse_args().operation]()

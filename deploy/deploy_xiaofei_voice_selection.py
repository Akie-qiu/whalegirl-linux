"""Guarded, reversible deployment for the Xiaofei local voice selector."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

import deploy_voice_languages as base

B = Path('/opt/whalegirl-linux-assets/xiaofei-voice-20261113')
FILES = ['whalegirl_linux/' + name for name in
         ('audio.py', 'audio_provider.py', 'audio_tools.py', 'audio_api.py', 'audio.js')]
FILES += ['deploy/' + name for name in
          ('pc_voice_bridge.py', 'pc_voice_engine.py', 'pc_voice_selection.py', 'pc_voice_control.py')]
FILES += ['tests/test_voice_selection.py']
base.B, base.FILES = B, FILES


def api(path):
    request = urllib.request.Request(
        'http://127.0.0.1:8092' + path,
        headers={'Authorization': 'Bearer ' + (base.DATA / 'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def inspect():
    root = base.CURRENT.resolve()
    result = {'current': str(root),
              'files': {name: base.sha(root / name) if (root / name).exists() else None for name in FILES},
              'disk_free_mib': shutil.disk_usage('/').free // 1024**2,
              'health': api('/health'), 'wechat': api('/api/state')['gateway'].get('status')}
    print(json.dumps(result))


def test():
    unit = 'whalegirl-xiaofei-voice-tests-20261113'
    runner = '''import faulthandler,unittest,pathlib,sys
from unittest.mock import patch
sys.path.insert(0, '/opt/whalegirl-linux-assets/xiaofei-voice-20261113/stage/tests')
original=pathlib.Path.read_text
def fixture_read(path,*args,**kwargs):
    if str(path)=="/proc/meminfo": return "MemAvailable: 1048576 kB\\n"
    return original(path,*args,**kwargs)
faulthandler.dump_traceback_later(40)
with patch.object(pathlib.Path,"read_text",fixture_read):
    loader=unittest.TestLoader(); suite=unittest.TestSuite()
    for name in ("test_audio","test_pc_voice","test_voice_dynamics","test_voice_languages","test_voice_selection"):
        suite.addTests(loader.loadTestsFromName(name))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
faulthandler.cancel_dump_traceback_later()
raise SystemExit(not result.wasSuccessful())'''
    command = ['systemd-run', '--unit=' + unit, '--quiet', '--wait', '--pipe', '--collect',
        '-p', 'CPUQuota=50%', '-p', 'MemoryMax=256M', '-p', 'WorkingDirectory=' + str(B / 'check'),
        '-E', 'PYTHONPATH=' + str(B / 'check/tests'), '/opt/whalegirl-linux/venv/bin/python', '-c', runner]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=100)
    except subprocess.TimeoutExpired:
        subprocess.run(['systemctl', 'stop', unit], check=False, timeout=15)
        raise
    (B / 'linux-tests.log').write_text(result.stdout + result.stderr)
    print((result.stdout + result.stderr)[-2800:])
    assert result.returncode == 0, 'Candidate failed; production untouched'
    base.save('tested.json', {'at': time.time(), 'returncode': 0,
        'unit_host_mem_available_fixture_kb': 1048576, 'production_resource_protection_changed': False,
        'test_audio_source': 'current workspace test file, test-only; not deployed'})


def final():
    manifest = json.loads((B / 'manifest.json').read_text())
    base.source_check(manifest, after=True)
    state, audio = api('/api/state'), api('/api/audio')
    result = {'health': api('/health'), 'wechat': state['gateway'].get('status'),
              'paused': bool(state.get('paused_reason')), 'pc': api('/api/audio/pc-status'),
              'audio_revision': audio['revision'], 'default_preset': audio['settings']['default_preset'],
              'moments_has_error': bool(api('/api/moments').get('error')),
              'reader_unchanged': subprocess.check_output(
                  ['systemctl', 'show', 'whalegirl-native-reader.service', '-p', 'MainPID', '--value'],
                  text=True).strip() == manifest['reader_pid'],
              'private_files_unchanged': {name: base.sha(base.DATA / name) == expected
                  for name, expected in manifest['private_hashes'].items()}}
    base.save('final-check.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['inspect', 'prepare', 'test', 'activate', 'rollback', 'final'])
    operation = parser.parse_args().operation
    (getattr(base, operation) if operation in {'prepare', 'activate', 'rollback'} else globals()[operation])()

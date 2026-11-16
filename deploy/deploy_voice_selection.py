"""Small guarded addition to the already-deployed language repair."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

import deploy_voice_languages as base

B = Path('/opt/whalegirl-linux-assets/voice-selection-20261112')
FILES = ['whalegirl_linux/' + name for name in ('audio.py', 'audio_provider.py', 'audio_tools.py', 'audio_api.py', 'audio.js')]
FILES += ['deploy/' + name for name in ('pc_voice_bridge.py', 'pc_voice_engine.py', 'pc_voice_selection.py', 'pc_voice_control.py')]
FILES += ['tests/test_voice_selection.py']
base.B, base.FILES = B, FILES


def api(path, value=None):
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        headers={'Authorization': 'Bearer ' + (base.DATA / 'admin.token').read_text().strip(), 'Content-Type': 'application/json'},
        data=json.dumps(value, ensure_ascii=False).encode() if value is not None else None)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def inspect():
    root = base.CURRENT.resolve()
    result = {'current': str(root), 'files': {name: base.sha(root / name) if (root / name).exists() else None for name in FILES},
              'disk_free_mib': __import__('shutil').disk_usage('/').free // 1024**2,
              'health': api('/health'), 'wechat': api('/api/state')['gateway'].get('status')}
    print(json.dumps(result))


def test():
    unit = 'whalegirl-voice-selection-tests-20261112'
    runner = '''import faulthandler,unittest,pathlib
from unittest.mock import patch
original=pathlib.Path.read_text
def fixture_read(path,*args,**kwargs):
    if str(path)=="/proc/meminfo": return "MemAvailable: 1048576 kB\\n"
    return original(path,*args,**kwargs)
faulthandler.dump_traceback_later(40)
with patch.object(pathlib.Path,"read_text",fixture_read):
    r=unittest.main(module=None,argv=["unittest","test_audio","test_pc_voice","test_voice_dynamics","test_voice_languages","test_voice_selection"],verbosity=1,exit=False)
faulthandler.cancel_dump_traceback_later()
raise SystemExit(not r.result.wasSuccessful())'''
    try:
        result = subprocess.run(['systemd-run', '--unit=' + unit, '--quiet', '--wait', '--pipe', '--collect',
            '-p', 'CPUQuota=50%', '-p', 'MemoryMax=256M', '-p', 'WorkingDirectory=' + str(B / 'check'),
            '-E', 'PYTHONPATH=' + str(B / 'check/tests'), '/opt/whalegirl-linux/venv/bin/python', '-c', runner],
            capture_output=True, text=True, timeout=100)
    except subprocess.TimeoutExpired:
        subprocess.run(['systemctl', 'stop', unit], check=False, timeout=15)
        raise
    (B / 'linux-tests.log').write_text(result.stdout + result.stderr)
    print((result.stdout + result.stderr)[-2800:])
    assert result.returncode == 0, 'Candidate failed; production untouched'
    base.save('tested.json', {'at': time.time(), 'returncode': 0,
                             'unit_host_mem_available_fixture_kb': 1048576,
                             'production_resource_protection_changed': False})


def configure():
    snapshot = api('/api/audio')
    assert not (B / 'before-audio-config.json').exists(), 'Inspect existing configuration backup'
    base.save('before-audio-config.json', snapshot)
    values = snapshot['settings']
    profile = values['providers'][values['speech_profile']]
    assert profile['backend'] == 'pc_bridge'
    assert 'yachiyo' not in values['presets'], 'Existing owner preset must be reviewed'
    source = next(p for p in values['presets'].values() if p['voice'] == 'cyrene')
    values['presets']['yachiyo'] = {**source, 'name': '八千代', 'voice': 'yachiyo', 'pitch': 0, 'speed': 1, 'gain_db': 0}
    if any(values['presets'][key]['voice'] == 'cyrene' for key in values['ai_preset_ids']):
        values['ai_preset_ids'].append('yachiyo')
    result = api('/api/audio/config', {'revision': snapshot['revision'], 'settings': values})
    base.save('configured.json', {'at': time.time(), 'revision': result['revision'], 'preset': 'yachiyo'})
    print(json.dumps({'configured': True, 'revision': result['revision'], 'default_unchanged': values['default_preset']}))


def final():
    manifest = json.loads((B / 'manifest.json').read_text())
    base.source_check(manifest, after=True)
    state, audio = api('/api/state'), api('/api/audio')
    result = {'health': api('/health'), 'wechat': state['gateway'].get('status'),
              'paused': bool(state.get('paused_reason')), 'pc': api('/api/audio/pc-status'),
              'audio_revision': audio['revision'], 'default_preset': audio['settings']['default_preset'],
              'moments_has_error': bool(api('/api/moments').get('error')),
              'reader_unchanged': subprocess.check_output(['systemctl','show','whalegirl-native-reader.service','-p','MainPID','--value'],text=True).strip() == manifest['reader_pid'],
              'private_files_unchanged': {name: base.sha(base.DATA / name) == expected for name, expected in manifest['private_hashes'].items()}}
    base.save('final-check.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['inspect','prepare','test','activate','rollback','configure','final'])
    name = parser.parse_args().operation
    (getattr(base, name) if name in {'prepare','activate','rollback'} else globals()[name])()

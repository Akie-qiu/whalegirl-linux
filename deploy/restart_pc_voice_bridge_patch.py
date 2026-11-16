"""Reload only the idle, identity-checked local adapter; retain the loaded model."""
import json
from pathlib import Path
import shutil
import time
import urllib.request

import psutil

ROOT = Path('D:/appData/qqbot/voise/runtime')
PROJECT = Path('D:/appData/qqbot/wechat-linux')


def health(token):
    request = urllib.request.Request('http://127.0.0.1:9882/health',
        headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def main():
    token = json.loads((ROOT / 'bridge.json').read_text(encoding='utf-8'))['token']
    before = health(token)
    assert not before.get('busy'), 'Voice generation is running'
    connections = [c for c in psutil.net_connections(kind='tcp')
        if c.status == psutil.CONN_LISTEN and c.laddr.port == 9882]
    assert len(connections) == 1
    process = psutil.Process(connections[0].pid)
    args = [x.replace('\\', '/').lower() for x in process.cmdline()]
    assert str(PROJECT / 'deploy/pc_voice_bridge.py').replace('\\', '/').lower() in args
    assert str(ROOT / 'bridge.json').replace('\\', '/').lower() in args
    backup = ROOT / 'before-voice-dynamics-20261111'
    backup.mkdir(exist_ok=True)
    previous = PROJECT / 'artifacts/voice-dynamics-moments-20261111/online-before/deploy/pc_voice_bridge.py'
    if not (backup / 'pc_voice_bridge.py').exists():
        shutil.copy2(previous, backup / 'pc_voice_bridge.py')
    (backup / 'before-health.json').write_text(json.dumps({k: before.get(k)
        for k in ('instance_id', 'ready', 'busy')}, indent=2), encoding='utf-8')
    old_pid = process.pid
    process.terminate()
    process.wait(timeout=8)
    for _ in range(30):
        time.sleep(1)
        try:
            result = health(token)
            if result.get('ready') and result.get('speech_controls'):
                print(json.dumps({'adapter_reloaded': True, 'previous_pid': old_pid,
                    'speech_controls': result['speech_controls'], 'model_was_not_restarted': True}))
                return
        except (OSError, ValueError):
            pass
    raise RuntimeError('The owner-started worker did not recover the adapter in time')


if __name__ == '__main__':
    main()

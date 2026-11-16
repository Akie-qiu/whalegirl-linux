"""Guarded static console update; no service or WeChat restart, no audio tasks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

B = Path('/opt/whalegirl-linux-assets/voice-model-console-20261112')
PREVIOUS = Path('/opt/whalegirl-linux-assets/voice-selection-20261112')
CURRENT = Path('/opt/whalegirl-linux/current')
DATA = Path('/var/lib/whalegirl-linux/core')
FILES = ['whalegirl_linux/audio.js', 'deploy/pc_voice_control.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    path = B / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    path.chmod(0o600)


def api(path):
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        headers={'Authorization':'Bearer ' + (DATA / 'admin.token').read_text().strip()})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def guard(manifest, after=False):
    assert str(CURRENT.resolve()) == manifest['root'], 'Release changed; preserve the new release'
    for name, expected in manifest['after' if after else 'before'].items():
        assert sha(CURRENT / name) == expected, 'Concurrent change: ' + name


def replace(source, target):
    metadata = target.stat()
    temp = target.with_name(target.name + '.voice-model-new')
    assert not temp.exists(), 'Review a previous incomplete replacement first'
    temp.write_bytes(source.read_bytes())
    temp.chmod(metadata.st_mode & 0o777)
    os.chown(temp, metadata.st_uid, metadata.st_gid)
    os.replace(temp, target)


def install():
    assert not (B / 'manifest.json').exists(), 'Keep the original baseline'
    previous = json.loads((PREVIOUS / 'manifest.json').read_text())
    assert str(CURRENT.resolve()) == previous['root']
    before = {}
    for name in FILES:
        assert sha(CURRENT / name) == previous['after'][name], 'Concurrent update: ' + name
        assert (B / 'stage' / name).is_file()
        target = B / 'before' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CURRENT / name, target)
        before[name] = sha(target)
    manifest = {'root':str(CURRENT.resolve()), 'at':time.time(), 'before':before,
        'after':{name:sha(B / 'stage' / name) for name in FILES},
        'private_hashes':{name:sha(DATA / name) for name in ('settings.json','persona-cards.json','admin.token','gateway.token')},
        'reader_pid':subprocess.check_output(['systemctl','show','whalegirl-native-reader.service','-p','MainPID','--value'],text=True).strip()}
    save('manifest.json', manifest)
    guard(manifest)
    for name in FILES:
        replace(B / 'stage' / name, CURRENT / name)
    guard(manifest, True)
    print(json.dumps({'installed':True,'files':FILES,'restarted':False}))


def final():
    manifest = json.loads((B / 'manifest.json').read_text())
    guard(manifest, True)
    previous = json.loads((PREVIOUS / 'manifest.json').read_text())
    for name, expected in previous['after'].items():
        if name not in FILES:
            assert sha(CURRENT / name) == expected, 'Other deployed voice code changed: ' + name
    state = api('/api/state')
    result = {'at':time.time(),'health':api('/health'),'wechat':state['gateway'].get('status'),
        'paused':bool(state.get('paused_reason')),'pc':api('/api/audio/pc-status'),
        'reader_unchanged':subprocess.check_output(['systemctl','show','whalegirl-native-reader.service','-p','MainPID','--value'],text=True).strip()==manifest['reader_pid'],
        'private_files_unchanged':{name:sha(DATA / name)==expected for name,expected in manifest['private_hashes'].items()},
        'explicit_synthesis_requests':0,'wechat_test_messages':0}
    save('final-check.json',result)
    print(json.dumps(result,ensure_ascii=False))


def rollback():
    manifest = json.loads((B / 'manifest.json').read_text())
    guard(manifest, True)
    for name in FILES:
        replace(B / 'before' / name, CURRENT / name)
    guard(manifest)
    save('rollback.json',{'at':time.time(),'restored':FILES,'restarted':False})
    print(json.dumps({'restored':FILES,'restarted':False}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['install','final','rollback'])
    globals()[parser.parse_args().operation]()

"""Private synthetic previews and read-only deployment checks; never sends chat."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

DATA = Path('/var/lib/whalegirl-linux/core')
B = Path('/opt/whalegirl-linux-assets/voice-languages-20261112')
CASES = {'japanese': ('おはよう。今日もよろしくね。', 'ja'),
         'mixed': ('先用中文说早上好。おはよう、今日は元気ですか？Hello, nice to meet you.', 'auto')}


def api(path, value=None):
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        headers={'Authorization': 'Bearer ' + (DATA / 'admin.token').read_text().strip(), 'Content-Type': 'application/json'},
        data=json.dumps(value, ensure_ascii=False).encode() if value is not None else None)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def save(name, value):
    path = B / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def submit():
    pc = api('/api/audio/pc-status')
    assert pc['connected'] and {'auto', 'ja'} <= set(pc.get('languages', []))
    result = {}
    for name, (text, language) in CASES.items():
        identity = hashlib.sha256(('voice-language-private-20261112-v1|' + name).encode()).hexdigest()[:32]
        job = api('/api/audio/preview', {'kind': 'speech', 'request_id': identity,
                                       'arguments': {'text': text, 'language': language}})
        result[name] = {'id': job['id'], 'state': job['state'], 'language': language}
        save('preview-intents.json', result)
    print(json.dumps({'private_previews': result, 'wechat_sent': False}), flush=True)


def check(final=False):
    results = {}
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for name, entry in json.loads((B / 'preview-intents.json').read_text()).items():
            row = dict(db.execute('SELECT * FROM audio_jobs WHERE id=?', (entry['id'],)).fetchone())
            captured = json.loads(row['config_json'])
            results[name] = {**{key: row[key] for key in ('id', 'state', 'duration', 'error')},
                             'requested_language': json.loads(row['args_json']).get('language'),
                             'synthesis_language': captured.get('synthesis_language')}
            if final:
                assert row['state'] == 'ready' and captured['synthesis_language'] == entry['language']
                assert row['destination'] == 'preview'
                source = (DATA / 'audio-media' / row['output_file']).resolve()
                assert source.is_relative_to(DATA / 'audio-media') and source.is_file()
                output = B / (name + '-preview.wav')
                output.write_bytes(source.read_bytes())
                output.chmod(0o600)
    value = {'at': time.time(), 'previews': results, 'pc': api('/api/audio/pc-status'), 'wechat_sent': False}
    if final:
        manifest = json.loads((B / 'manifest.json').read_text())
        state, moments = api('/api/state'), api('/api/moments')
        value.update(health=api('/health'), wechat=state['gateway'].get('status'),
                     paused=bool(state.get('paused_reason')), moments_has_error=bool(moments.get('error')),
                     current=str(Path('/opt/whalegirl-linux/current').resolve()),
                     configurations_unchanged={name: hashlib.sha256((DATA / name).read_bytes()).hexdigest() == expected
                                              for name, expected in manifest['private_hashes'].items()},
                     reader_pid_unchanged=subprocess.check_output(['systemctl', 'show', 'whalegirl-native-reader.service',
                         '-p', 'MainPID', '--value'], text=True).strip() == manifest['reader_pid'])
        assert value['health']['status'] == 'ok' and not value['health']['failed_background_tasks']
    save('final-check.json' if final else 'preview-status.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['submit', 'status', 'final'])
    operation = parser.parse_args().operation
    submit() if operation == 'submit' else check(operation == 'final')

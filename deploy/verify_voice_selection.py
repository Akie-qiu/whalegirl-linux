"""Live cloud-to-PC private synthesis and bounded admin switch checks; no chat sends."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
import uuid

import deploy_voice_selection as deploy

B, DATA = deploy.B, deploy.base.DATA
CASES = {'zh': ('早上好，今天也想听听你的新鲜事。', 'zh'),
         'ja': ('おはよう。今日はどんなお話をしようかな。', 'ja')}


def status():
    pc = deploy.api('/api/audio/pc-status')
    audio = deploy.api('/api/audio')
    result = {'pc': pc, 'revision': audio['revision'],
              'default_preset': audio['settings']['default_preset'],
              'model': audio['settings']['providers'][audio['settings']['speech_profile']]['model']}
    deploy.base.save('live-status.json', result)
    print(json.dumps(result, ensure_ascii=False))


def submit(voice):
    status = deploy.api('/api/audio/pc-status')
    assert status.get('connected') and status.get('active_voice') == voice
    result = {}
    for name, (text, language) in CASES.items():
        identity = hashlib.sha256(('voice-selection-private-20261112-v1|' + voice + '|' + name).encode()).hexdigest()[:32]
        job = deploy.api('/api/audio/preview', {'kind': 'speech', 'request_id': identity,
            'arguments': {'text': text, 'language': language}})
        result[name] = {'id': job['id'], 'state': job['state'], 'language': language}
        deploy.base.save(voice + '-previews.json', result)
    print(json.dumps({'private_previews': result, 'voice': voice, 'wechat_sent': False}))


def check(voice):
    results = {}
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for name, entry in json.loads((B / (voice + '-previews.json')).read_text()).items():
            row = dict(db.execute('SELECT * FROM audio_jobs WHERE id=?', (entry['id'],)).fetchone())
            capture = json.loads(row['config_json'])
            results[name] = {**{key: row[key] for key in ('id','state','duration','error')},
                'voice': (capture.get('preset') or {}).get('voice'), 'language': capture.get('synthesis_language'),
                'timing': json.loads(row['timing_json'])}
            if row['state'] == 'ready':
                assert row['destination'] == 'preview' and capture['preset']['voice'] == voice
                assert capture['synthesis_language'] == entry['language']
                source = (DATA / 'audio-media' / row['output_file']).resolve()
                assert source.is_relative_to(DATA / 'audio-media') and source.is_file()
                output = B / (voice + '-' + name + '-preview.wav')
                output.write_bytes(source.read_bytes()); output.chmod(0o600)
    deploy.base.save(voice + '-preview-results.json', {'results': results, 'wechat_sent': False})
    print(json.dumps(results, ensure_ascii=False))


def switch(voice):
    assert voice in {'cyrene','yachiyo'}
    identity = uuid.uuid4().hex
    result = deploy.api('/api/audio/pc-select', {'voice_id': voice, 'request_id': identity})
    deploy.base.save('switch-to-' + voice + '.json', {'at': time.time(), **result})
    assert result['id'] == identity and result['voice_id'] == voice
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['status','submit','check','switch'])
    parser.add_argument('--voice', choices=['cyrene','yachiyo'], default='yachiyo')
    args = parser.parse_args()
    status() if args.operation == 'status' else globals()[args.operation](args.voice)

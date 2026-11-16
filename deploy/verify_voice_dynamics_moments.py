"""Real private previews only; never sends WeChat or publishes Moments."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

B = Path('/opt/whalegirl-linux-assets/voice-dynamics-moments-20261111')
DATA = Path('/var/lib/whalegirl-linux/core')


def api(path, value=None):
    token = (DATA / 'admin.token').read_text().strip()
    request = urllib.request.Request('http://127.0.0.1:8092' + path,
        data=json.dumps(value, ensure_ascii=False).encode() if value is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def save(name, value):
    p = B / name
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    p.chmod(0o600)


def voice():
    status = api('/api/audio/pc-status')
    assert status['connected'] and status.get('speech_controls'), 'New adapter not ready'
    cases = [('neutral', {}, 'd4db8fb87ea4443e90eab896dce50441'),
        ('lively', {'delivery': {'speed': 1.1, 'pitch': .6, 'gain_db': 1, 'pause_seconds': .18}},
         'd4db8fb87ea4443e90eab896dce50442')]
    identities = {}
    for name, controls, identity in cases:
        result = api('/api/audio/preview', {'kind': 'speech', 'arguments': {
            'text': '来啦！今天想先听我说什么？', **controls}, 'request_id': identity})
        identities[name] = result['id']
    save('real-voice-intents.json', {'at': time.time(), 'jobs': identities})
    until = time.monotonic() + 180
    while time.monotonic() < until:
        jobs = {j['id']: j for j in api('/api/audio')['jobs']}
        rows = {n: jobs[i] for n, i in identities.items()}
        if all(r['state'] not in ('queued', 'generating', 'downloading') for r in rows.values()):
            results = {n: {k: r[k] for k in ('id', 'state', 'duration', 'applied', 'timing', 'error', 'playable')}
                for n, r in rows.items()}
            save('real-voice-results.json', {'kind': 'real_pc_model_previews', 'wechat_sent': False, 'cases': results})
            print(json.dumps(results, ensure_ascii=False))
            assert all(r['state'] == 'ready' for r in rows.values()), 'Private voice preview failed'
            return
        time.sleep(2)
    raise TimeoutError('Preview still pending; retained IDs, do not resubmit')


def image():
    assert not (B / 'real-image-intent.json').exists(), 'An image test was already submitted; inspect it first'
    save('real-image-intent.json', {'at': time.time(), 'phase': 'submitting', 'destination': 'library'})
    result = api('/api/images/jobs', {'kind': 'generate', 'destination': 'library',
        'prompt': '画一只在海边开心挥手的小蓝鲸，清爽插画，正方形构图，不要文字。',
        'aspect_ratio': '1:1'})
    save('real-image-intent.json', {'at': time.time(), 'phase': 'submitted', 'response_keys': list(result),
        'id': result.get('id') or result.get('task_id')})
    print(json.dumps({'image_test_submitted': True, 'response_keys': list(result),
        'id': result.get('id') or result.get('task_id'), 'destination': 'library'}))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('operation', choices=['voice', 'image'])
    globals()[p.parse_args().operation]()

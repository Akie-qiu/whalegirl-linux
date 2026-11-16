"""Run one in-memory Xiaofei inference and restore the stopped voice selection."""
import hashlib
import io
import json
from pathlib import Path
import sys
import wave

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import read_control, read_worker, write_json
from pc_voice_engine import voice_catalog
from gsv_lite_voice_server import load_tts, make_app


ROOT = Path('D:/appData/qqbot/voise/runtime')
TEXT = '你好呀，我是小菲，今天也很有精神。'


def main():
    if read_control(ROOT)['desired'] or read_worker(ROOT).get('phase') != 'stopped':
        raise SystemExit('Stop the owner voice worker before local model acceptance')
    engine_path, control_path = ROOT / 'engine.json', ROOT / 'service-control.json'
    if not control_path.exists():
        raise SystemExit('A persisted voice control record is required for reversible testing')
    original_engine = engine_path.read_bytes()
    original_control = control_path.read_bytes()
    before_hash = hashlib.sha256(original_engine).hexdigest()
    target = next((row['engine'] for row in voice_catalog(ROOT) if row['id'] == 'xiaofei'), None)
    if not target:
        raise SystemExit('Xiaofei is not in the owner voice catalog')
    try:
        write_json(engine_path, target)
        write_json(control_path, {**read_control(ROOT), 'desired': True})
        state = load_tts(ROOT, 'xiaofei-v2')
        headers = {'Authorization': 'Bearer ' + state['token']}
        with TestClient(make_app(state)) as client:
            health = client.get('/health', headers=headers)
            if health.status_code != 200 or health.json().get('engine_fingerprint') != state['fingerprint']:
                raise AssertionError('GSV-TTS-Lite health fingerprint did not match')
            response = client.post('/v1/tts', headers=headers, json={
                'request_id': 'a' * 32, 'text': TEXT, 'preset': 'xiaofei',
                'language': 'zh', 'speed_factor': 1, 'fragment_interval': .3,
            })
            if response.status_code != 200:
                raise AssertionError('Local synthesis failed with HTTP ' + str(response.status_code))
            with wave.open(io.BytesIO(response.content)) as wav:
                duration = wav.getnframes() / wav.getframerate()
                if wav.getframerate() != 32000 or wav.getsampwidth() != 2 or not .1 <= duration <= 120:
                    raise AssertionError('Generated WAV failed structural checks')
            print(json.dumps({'backend': response.headers.get('X-GSV-TTS-Backend'),
                'voice_id': response.headers.get('X-Voice-ID'),
                'duration_seconds': round(duration, 3), 'bytes': len(response.content),
                'generation_seconds': response.headers.get('X-GSV-TTS-Seconds'),
                'fingerprint': response.headers.get('X-GSV-TTS-Fingerprint'),
                'audio_persisted': False}, ensure_ascii=False))
    finally:
        engine_path.write_bytes(original_engine)
        control_path.write_bytes(original_control)
    restored = hashlib.sha256(engine_path.read_bytes()).hexdigest()
    if restored != before_hash or read_control(ROOT)['desired']:
        raise AssertionError('The previous stopped voice state was not restored')
    print('previous_voice_restored=true worker_desired=false')


if __name__ == '__main__':
    main()

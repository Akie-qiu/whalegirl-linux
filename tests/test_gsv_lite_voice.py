"""GSV-TTS-Lite adapter safety and finite WAV response tests."""
import io
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
import wave

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
from pc_voice_common import write_json
from gsv_lite_voice_server import make_app


TOKEN = 'test-only-' + 'x' * 48
FINGERPRINT = 'a' * 24


class FakeTTS:
    def __init__(self):
        self.calls = []

    def infer_batched(self, **values):
        self.calls.append(values)
        return [SimpleNamespace(audio_data=np.zeros(16000, dtype=np.float32), samplerate=32000)]


class GsvLiteAdapterTests(unittest.TestCase):
    def test_real_isolated_entrypoint_can_start_from_an_unrelated_directory(self):
        # Do not import the adapter through the test runner's modified sys.path.
        # Exercise the exact -I launch contract used by the Windows supervisor.
        result = subprocess.run([sys.executable, '-X', 'utf8', '-I',
            str(ROOT / 'deploy/gsv_lite_voice_server.py'), '--help'],
            cwd=self.root, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'--runtime', result.stdout)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.voice_root = self.root / 'runtime'
        self.voice_root.mkdir()
        write_json(self.voice_root / 'engine.json', {
            'backend': 'gsv-lite', 'package': 'xiaofei-v2',
            'fingerprint': FINGERPRINT, 'voice_id': 'xiaofei',
        })
        write_json(self.voice_root / 'service-control.json', {'desired': True})
        self.tts = FakeTTS()
        self.state = {
            'token': TOKEN, 'runtime': self.voice_root, 'fingerprint': FINGERPRINT,
            'voice_id': 'xiaofei', 'tts': self.tts, 'package': self.root / 'package',
            'prompt_text': '测试参考音频', 'prompt_language': 'zh',
            'gpt_model': 'gpt', 'sovits_model': 'sovits', 'max_bytes': 1024 * 1024,
        }
        self.client = TestClient(make_app(self.state))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def body(self, **extra):
        return {'request_id': 'b' * 32, 'text': '你好呀。', 'preset': 'xiaofei',
                'language': 'zh', **extra}

    def test_auth_language_selection_and_wav_response(self):
        self.assertEqual(self.client.get('/health').status_code, 401)
        headers = {'Authorization': 'Bearer ' + TOKEN}
        health = self.client.get('/health', headers=headers)
        self.assertEqual(health.json()['backend'], 'gsv-lite')
        self.assertEqual(health.json()['languages'], ['auto', 'zh', 'ja'])

        response = self.client.post('/v1/tts', headers=headers, json=self.body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-GSV-TTS-Backend'], 'GSV-TTS-Lite-CUDA')
        self.assertEqual(response.headers['X-Voice-ID'], 'xiaofei')
        with wave.open(io.BytesIO(response.content)) as wav:
            self.assertEqual(wav.getframerate(), 32000)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getnframes(), 16000)
        self.assertEqual(self.tts.calls[0]['text_languages'], 'zh')
        self.assertEqual(self.tts.calls[0]['speed'], 1)

    def test_wrong_voice_or_language_is_rejected(self):
        headers = {'Authorization': 'Bearer ' + TOKEN}
        self.assertEqual(self.client.post('/v1/tts', headers=headers,
            json=self.body(preset='yachiyo')).status_code, 422)
        self.assertEqual(self.client.post('/v1/tts', headers=headers,
            json=self.body(language='ko')).status_code, 422)
        self.assertEqual(self.client.post('/v1/tts', headers=headers,
            json=self.body(language='en')).status_code, 422)
        write_json(self.voice_root / 'engine.json', {
            'backend': 'aniflive', 'package': 'yachiyo-win-opt0',
            'fingerprint': FINGERPRINT, 'voice_id': 'yachiyo',
        })
        self.assertEqual(self.client.post('/v1/tts', headers=headers,
            json=self.body()).status_code, 503)
        self.assertEqual(self.tts.calls, [])


if __name__ == '__main__':
    unittest.main()

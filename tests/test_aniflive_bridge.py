"""Adapter contract and real FFmpeg DSP; inference acceptance is recorded separately."""
import io
import json
import math
from pathlib import Path
import shutil
import sys
import struct
import unittest
from unittest.mock import patch
import wave

import httpx
from fastapi.testclient import TestClient

import test_pc_voice as pc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
from pc_voice_engine import load_engine, tempo_wav

FINGERPRINT = 'b' * 24
SPEC = {'backend': 'aniflive', 'fingerprint': FINGERPRINT,
        'model_id': 'cyrene-anif-trial', 'voice_id': 'default'}
TRT_HEADERS = {'X-TensorRT-Backend': 'TensorRT-11', 'X-PyTorch-Fallback': 'false',
               'X-TensorRT-Engine-Count': '9'}


class SelectionTests(unittest.TestCase):
    def test_existing_install_defaults_to_original_engine(self):
        result = load_engine(pc.folder())
        self.assertEqual(result['backend'], 'gpt-sovits')
        self.assertIn('9881', result['command'])

    def test_owner_configuration_cannot_select_arbitrary_path_or_shell(self):
        root = pc.folder()
        for value in ({'backend': 'shell'}, {'backend': 'aniflive', 'package': '../outside'},
                      {'backend': 'gpt-sovits', 'command': ['arbitrary']}):
            (root / 'engine.json').write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_engine(root)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        fixture = pc.AdapterTests()
        fixture.setUp()
        self.fixture = fixture

    def responder(self, request):
        self.fixture.calls.append(request)
        if request.url.path == '/health':
            return httpx.Response(200, json={'ready': True, 'backend': 'TensorRT-11',
                'engine_count': 9, 'engine_fingerprint': FINGERPRINT})
        return httpx.Response(200, content=pc.wav_bytes(), headers=TRT_HEADERS)

    def test_compatible_payload_preserves_once_only_requests_and_cache(self):
        with patch.object(pc.bridge, 'load_engine', return_value=SPEC), \
                TestClient(pc.bridge.make_app(self.fixture.path,
                    transport=httpx.MockTransport(self.responder))) as client:
            health = client.get('/health', headers=self.fixture.headers).json()
            self.assertTrue(health['ready'])
            self.assertEqual(health['speech_control_modes']['speed_factor'], 'ffmpeg-atempo')
            first = client.post('/v1/tts', headers=self.fixture.headers, json=self.fixture.body)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers['X-Voice-Backend'], 'aniflive')
            request = self.fixture.calls[-1]
            self.assertEqual(request.url.path, '/v1/audio/speech')
            value = json.loads(request.content)
            self.assertEqual((value['speed'], value['pause_length'], value['voice']), (1, .3, 'default'))
            self.assertNotIn('ref_audio_path', value)
            self.assertEqual(client.post('/v1/tts', headers=self.fixture.headers,
                json=self.fixture.body).headers['X-Voice-Cache'], 'hit')
            self.assertEqual(sum(r.method == 'POST' for r in self.fixture.calls), 1)

    def test_wrong_engine_is_not_ready(self):
        def wrong(request):
            return httpx.Response(200, json={'ready': True, 'backend': 'TensorRT-11',
                'engine_count': 9, 'engine_fingerprint': 'different'})
        with patch.object(pc.bridge, 'load_engine', return_value=SPEC), \
                TestClient(pc.bridge.make_app(self.fixture.path, transport=httpx.MockTransport(wrong))) as client:
            self.assertFalse(client.get('/health', headers=self.fixture.headers).json()['ready'])

    def test_unverified_fallback_cannot_be_cached_as_success(self):
        def fallback(request):
            self.fixture.calls.append(request)
            return httpx.Response(200, content=pc.wav_bytes(),
                                  headers={**TRT_HEADERS, 'X-PyTorch-Fallback': 'true'})
        with patch.object(pc.bridge, 'load_engine', return_value=SPEC), \
                TestClient(pc.bridge.make_app(self.fixture.path, transport=httpx.MockTransport(fallback))) as client:
            self.assertEqual(client.post('/v1/tts', headers=self.fixture.headers, json=self.fixture.body).status_code, 502)
            self.assertEqual(client.post('/v1/tts', headers=self.fixture.headers, json=self.fixture.body).status_code, 409)
            self.assertEqual(len(self.fixture.calls), 1)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for rate adaptation')
    def test_rate_and_pause_are_applied_without_sending_unsupported_speed_to_model(self):
        with patch.object(pc.bridge, 'load_engine', return_value=SPEC), \
                TestClient(pc.bridge.make_app(self.fixture.path,
                    transport=httpx.MockTransport(self.responder))) as client:
            body = {**self.fixture.body, 'speed_factor': 1.1, 'fragment_interval': .18}
            response = client.post('/v1/tts', headers=self.fixture.headers, json=body)
            self.assertEqual(response.status_code, 200)
            payload = json.loads(self.fixture.calls[-1].content)
            self.assertEqual(payload['speed'], 1.0)
            self.assertAlmostEqual(payload['pause_length'], .198)
            with wave.open(io.BytesIO(response.content)) as audio:
                self.assertLess(audio.getnframes() / audio.getframerate(), 1)


class DspTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg is required for rate adaptation')
    async def test_actual_rate_changes_duration_and_preserves_pitch(self):
        rate = 32000
        samples = [round(math.sin(2 * math.pi * 440 * i / rate) * 10000) for i in range(rate * 2)]
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as audio:
            audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
            audio.writeframes(struct.pack('<' + 'h' * len(samples), *samples))
        result = await tempo_wav(stream.getvalue(), 1.2, 2_000_000)
        with wave.open(io.BytesIO(result)) as audio:
            self.assertAlmostEqual(audio.getnframes() / rate, 2 / 1.2, delta=.07)
            raw = audio.readframes(audio.getnframes())
            pcm = struct.unpack('<' + 'h' * (len(raw) // 2), raw)
        center = pcm[rate // 10:rate + rate // 10]
        crossings = sum(a <= 0 < b for a, b in zip(center, center[1:]))
        self.assertAlmostEqual(crossings, 440, delta=2)
        with self.assertRaises(ValueError):
            await tempo_wav(stream.getvalue(), .7, 4096)


if __name__ == '__main__':
    unittest.main()

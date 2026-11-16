"""Per-message voice controls: preservation, effective parameters, no extra calls."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock

import httpx
from pydantic import ValidationError
from fastapi.testclient import TestClient

from whalegirl_linux.audio_settings import SpeechDelivery, VoicePreset
from whalegirl_linux.audio_provider import AudioProviderClient, AudioError
from whalegirl_linux.audio_media import tune
import test_audio as fixtures
import test_pc_voice as pc


class DynamicsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AudioTests.asyncSetUp
    asyncTearDown = fixtures.AudioTests.asyncTearDown
    configure = fixtures.AudioTests.configure
    local_config = pc.LocalPipelineTests.local_config

    async def test_effective_controls_are_per_job_and_native_speed_is_not_applied_twice(self):
        self.local_config()
        self.configure(ai_voice_dynamics_enabled=True)
        before = self.service.settings.snapshot()
        delivery = {'speed': 1.12, 'pitch': .6, 'gain_db': 1, 'pause_seconds': .18}
        with patch('whalegirl_linux.audio.tune', new=AsyncMock(wraps=tune)) as render:
            job = self.service.enqueue('speech', {'text': '来啦，今天也要开开心心呀！', 'delivery': delivery}, first=self.event)
            await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'ready')
        self.assertEqual(len(self.provider.calls), 1)
        captured = self.provider.calls[0][0]
        self.assertEqual(captured['native_synthesis'], {'speed_factor': 1.12, 'fragment_interval': .18})
        self.assertEqual(captured['preset'].pitch, .6)
        self.assertEqual(render.await_args.args[2].speed, 1)
        applied = json.loads(self.service.get(job['id'])['config_json'])
        self.assertEqual(applied['delivery'], delivery)
        self.assertEqual(applied['preset']['speed'], 1.12)
        self.assertEqual(self.service.settings.snapshot(), before)

    async def test_existing_cloud_models_use_only_existing_audio_protocol(self):
        self.configure(ai_voice_dynamics_enabled=True)
        job = self.service.enqueue('speech', {'text': '早呀', 'delivery': {'speed': 1.1, 'pitch': .5}}, first=self.event)
        with patch('whalegirl_linux.audio.tune', new=AsyncMock(wraps=tune)) as render:
            await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'ready')
        self.assertNotIn('native_synthesis', self.provider.calls[0][0])
        self.assertEqual(render.await_args.args[2].speed, 1.1)

    async def test_disabled_at_enqueue_or_before_generation_is_respected(self):
        with self.assertRaises(ValueError):
            self.service.enqueue('speech', {'text': '早呀', 'delivery': {'speed': 1.1}}, first=self.event)
        self.configure(ai_voice_dynamics_enabled=True)
        job = self.service.enqueue('speech', {'text': '早呀', 'delivery': {'speed': 1.1}}, first=self.event)
        self.configure(ai_voice_dynamics_enabled=False)
        await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'failed')
        self.assertEqual(self.provider.calls, [])

    def test_schema_and_effective_values_follow_admin_base(self):
        self.local_config()
        self.configure(ai_voice_dynamics_enabled=True)
        self.provider.pc_ready = lambda: True
        engine = SimpleNamespace(api_style='openai', stream_enabled=False, chat_with_tools=True)
        schema = self.service.tools.schemas(engine, self.event)[0]['function']['parameters']['properties']
        self.assertIn('pause_seconds', schema['delivery']['properties'])
        value = SpeechDelivery(speed=1.2, pitch=1.5, gain_db=3).apply(
            VoicePreset(speed=1.3, pitch=2, gain_db=5))
        self.assertEqual((value.speed, value.pitch, value.gain_db), (1.4, 3, 6))
        for body in ({'speed': 10}, {'pitch': float('nan')}, {'ref_audio_path': 'arbitrary'}, {'pause_seconds': 5}):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                SpeechDelivery(**body)

    async def test_render_only_reuses_raw_native_speed_and_keeps_original_trace(self):
        self.local_config()
        self.configure(ai_voice_dynamics_enabled=True)
        job = self.service.enqueue('speech', {'text': '来啦！', 'delivery': {'speed': 1.1}}, preview=True)
        await self.service.generate_once()
        reuse = self.service.enqueue('speech', {'text': '来啦！'}, preview=True, render_of=job['id'])
        with patch('whalegirl_linux.audio.tune', new=AsyncMock(wraps=tune)) as render:
            await self.service.generate_once()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.service.get(reuse['id'])['state'], 'ready')
        self.assertAlmostEqual(render.await_args.args[2].speed, 1 / 1.1)


class BridgeControlsTests(unittest.TestCase):
    def test_actual_model_payload_cache_identity_and_default_compatibility(self):
        fixture = pc.AdapterTests()
        fixture.setUp()
        with TestClient(pc.bridge.make_app(fixture.path, transport=fixture.transport)) as client:
            params = {**fixture.body, 'speed_factor': 1.1, 'fragment_interval': .18}
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers, json=params).status_code, 200)
            sent = json.loads(fixture.calls[0].content)
            self.assertEqual((sent['speed_factor'], sent['fragment_interval']), (1.1, .18))
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers, json=params).status_code, 200)
            self.assertEqual(len(fixture.calls), 1)
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers, json=fixture.body).status_code, 409)
            self.assertEqual(client.post('/v1/tts', headers=fixture.headers,
                json={**fixture.body, 'request_id': 'a' * 32, 'speed_factor': 20}).status_code, 422)


class ProviderControlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_controls_only_sent_to_capable_bridge(self):
        for supported in (False, True):
            calls = []
            def respond(request):
                calls.append(request)
                if request.url.path != '/v1/tts':
                    return pc.handshake_response(request,
                        speech_controls=['speed_factor', 'fragment_interval'] if supported else [])
                return httpx.Response(200, content=fixtures.wav_bytes())
            provider = AudioProviderClient(transport=httpx.MockTransport(respond))
            capture = pc.capture()
            capture['native_synthesis'] = {'speed_factor': 1.1, 'fragment_interval': .18}
            try:
                if supported:
                    await provider.generate(capture, 'speech', {'text': '早呀'})
                    self.assertEqual(json.loads(calls[-1].content)['speed_factor'], 1.1)
                else:
                    with self.assertRaises(AudioError):
                        await provider.generate(capture, 'speech', {'text': '早呀'})
                    self.assertFalse(any(r.url.path == '/v1/tts' for r in calls))
            finally:
                await provider.close()


if __name__ == '__main__':
    unittest.main()

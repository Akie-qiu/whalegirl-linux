"""Language routing, unchanged legacy requests, and no unsupported-language POSTs."""
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

import test_audio as fixtures
import test_pc_voice as pc
from whalegirl_linux.audio import SpeechArguments
from whalegirl_linux.audio_provider import AudioProviderClient, AudioError

LANGUAGES = ['auto', 'zh', 'yue', 'en', 'ja', 'ko']


class BridgeLanguageTests(unittest.TestCase):
    def test_auto_default_and_explicit_language_reach_model_without_forcing_chinese(self):
        fixture = pc.AdapterTests()
        fixture.setUp()
        for backend in ('gpt-sovits', 'aniflive'):
            # Independent temporary ledgers for the two local protocols.
            fixture.setUp()
            engine = {'backend': backend, 'model_id': 'voice', 'voice_id': 'default', 'fingerprint': 'a' * 24}
            calls = []
            def respond(request):
                calls.append(request)
                return httpx.Response(200, content=pc.wav_bytes(), headers={
                    'X-TensorRT-Backend': 'TensorRT-11', 'X-PyTorch-Fallback': 'false', 'X-TensorRT-Engine-Count': '9'})
            with patch.object(pc.bridge, 'load_engine', return_value=engine), TestClient(
                    pc.bridge.make_app(fixture.path, transport=httpx.MockTransport(respond))) as client:
                self.assertEqual(client.get('/health', headers=fixture.headers).json()['languages'], LANGUAGES)
                for index, language in enumerate(LANGUAGES):
                    body = {**fixture.body, 'request_id': format(index + 1, '032x')}
                    if language != 'auto':
                        body['language'] = language
                    result = client.post('/v1/tts', headers=fixture.headers, json=body)
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(json.loads(calls[-1].content)['text_lang'], language)
                    self.assertEqual(result.headers['X-Voice-Language'], language)
                before = len(calls)
                body = {**fixture.body, 'request_id': format(2, '032x'), 'language': 'ja'}
                self.assertEqual(client.post('/v1/tts', headers=fixture.headers, json=body).status_code, 409)
                self.assertEqual(len(calls), before)
                self.assertEqual(client.post('/v1/tts', headers=fixture.headers,
                    json={**fixture.body, 'language': 'arbitrary'}).status_code, 422)


class ProviderLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_language_requires_bridge_capability_before_synthesis(self):
        for supported in (False, True):
            calls = []
            def respond(request):
                calls.append(request)
                if request.url.path != '/v1/tts':
                    return pc.handshake_response(request, languages=LANGUAGES if supported else [])
                self.assertEqual(json.loads(request.content)['language'], 'ja')
                return httpx.Response(200, content=pc.wav_bytes(), headers={'X-Voice-Language': 'ja'})
            provider = AudioProviderClient(transport=httpx.MockTransport(respond))
            try:
                if supported:
                    result = await provider.generate(pc.capture(), 'speech', {'text': 'おはよう。', 'language': 'ja'})
                    self.assertEqual(result['language'], 'ja')
                else:
                    with self.assertRaises(AudioError):
                        await provider.generate(pc.capture(), 'speech', {'text': 'おはよう。', 'language': 'ja'})
                    self.assertFalse(any(r.url.path == '/v1/tts' for r in calls))
            finally:
                await provider.close()

    async def test_legacy_bridge_receives_unchanged_default_request(self):
        calls = []
        def respond(request):
            calls.append(request)
            if request.url.path != '/v1/tts':
                return pc.handshake_response(request)
            return httpx.Response(200, content=pc.wav_bytes())
        provider = AudioProviderClient(transport=httpx.MockTransport(respond))
        try:
            await provider.generate(pc.capture(), 'speech', {'text': '早上好'})
            self.assertNotIn('language', json.loads(calls[-1].content))
        finally:
            await provider.close()


class PipelineLanguageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AudioTests.asyncSetUp
    asyncTearDown = fixtures.AudioTests.asyncTearDown
    configure = fixtures.AudioTests.configure
    local_config = pc.LocalPipelineTests.local_config

    async def test_language_is_saved_and_passed_to_provider_without_changing_voice(self):
        self.local_config()
        before = self.service.settings.snapshot()
        job = self.service.enqueue('speech', {'text': '世界', 'language': 'ja'}, first=self.event)
        await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'ready')
        self.assertEqual(self.provider.calls[0][2]['language'], 'ja')
        self.assertEqual(self.service.settings.snapshot(), before)
        self.provider.pc_ready = lambda: True
        engine = SimpleNamespace(api_style='openai', stream_enabled=False, chat_with_tools=True)
        tools = self.service.tools.schemas(engine, self.event)
        voice = next(t['function'] for t in tools if t['function']['name'] == 'send_voice')
        self.assertEqual(voice['parameters']['properties']['language']['enum'], LANGUAGES)

    def test_old_arguments_keep_same_serialized_defaults_and_invalid_languages_fail(self):
        self.assertNotIn('language', json.loads(SpeechArguments(text='早上好').model_dump_json(exclude_none=True)))
        with self.assertRaises(ValidationError):
            SpeechArguments(text='おはよう。', language='path-or-shell')

    def test_cloud_provider_does_not_silently_ignore_explicit_language(self):
        with self.assertRaises(ValueError):
            self.service.enqueue('speech', {'text': 'おはよう。', 'language': 'ja'}, first=self.event)


if __name__ == '__main__':
    unittest.main()

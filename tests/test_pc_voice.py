"""Offline bridge/security regressions; separate from real inference evidence."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from whalegirl_linux.audio_provider import AudioError, AudioProviderClient, GenerationUnknown
from whalegirl_linux.audio_settings import AudioProvider, AudioSettings, VoicePreset
import test_audio as fixtures

folder, wav_bytes = fixtures.folder, fixtures.wav_bytes

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pc_voice_bridge', ROOT / 'deploy/pc_voice_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
TOKEN = 'test-only-' + 'x' * 48
IDENTITY = '1234567890abcdef1234567890abcdef'


def handshake_response(request, **health):
    if request.url.path == '/health':
        return httpx.Response(200, json={'ready': True, 'busy': False,
            'handshake_nonce': request.headers.get('X-Voice-Handshake'),
            'instance_id': IDENTITY, **health})
    if request.url.path == '/v1/handshake/ack':
        body = json.loads(request.content)
        return httpx.Response(200, json={'acknowledged': True,
            'accepted': body['accepted'], 'instance_id': body['instance_id']})
    return None


def capture():
    return {'provider': AudioProvider(kind='speech', backend='pc_bridge',
        api_url='http://127.0.0.1:19881', model='cyrene-v2proplus'),
        'settings': AudioSettings(), 'preset': VoicePreset(voice='cyrene'),
        'key': TOKEN, 'request_id': IDENTITY}


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_local_http_is_only_accepted_by_explicit_speech_backend(self):
        self.assertEqual(capture()['provider'].endpoint_url(), 'http://127.0.0.1:19881/v1/tts')
        for url in ('http://localhost:19881', 'http://127.0.0.1:8092', 'http://[::1]:19881',
                    'https://127.0.0.1:19881', 'http://127.0.0.1:19881/control',
                    'http://user:secret@127.0.0.1:19881', 'http://127.0.0.1:19881?x=1'):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                AudioProvider(kind='speech', backend='pc_bridge', api_url=url, model='local')
        with self.assertRaises(ValidationError):
            AudioProvider(kind='speech', api_url='http://127.0.0.1:19881', model='local')
        with self.assertRaises(ValidationError):
            AudioProvider(kind='music', backend='pc_bridge', api_url='http://127.0.0.1:19881', model='local')

    async def test_bytes_and_job_identity_without_public_download(self):
        requests = []
        def respond(request):
            requests.append(request)
            if request.url.path != '/v1/tts':
                return handshake_response(request)
            return httpx.Response(200, content=wav_bytes())
        client = AudioProviderClient(transport=httpx.MockTransport(respond))
        try:
            value = await client.generate(capture(), 'speech', {'text': '早呀'})
            path = folder() / 'local.wav'
            await client.download(value, path, 100000)
            self.assertEqual(path.read_bytes(), wav_bytes())
            self.assertEqual(json.loads(requests[-1].content), {'request_id': IDENTITY, 'text': '早呀', 'preset': 'cyrene'})
            self.assertEqual(value['charge_state'], 'local_no_api_charge')
            self.assertEqual(value['usage'], {})
        finally:
            await client.close()

    async def test_disconnected_pc_is_definite_failure_with_no_retry(self):
        count = 0
        def disconnected(request):
            nonlocal count
            count += 1
            raise httpx.ConnectError('offline')
        client = AudioProviderClient(transport=httpx.MockTransport(disconnected))
        try:
            with self.assertRaises(AudioError) as error:
                await client.generate(capture(), 'speech', {'text': '早呀'})
            self.assertNotIsInstance(error.exception, GenerationUnknown)
            self.assertEqual(count, 1)
            self.assertFalse((await client.pc_status(capture()))['connected'])
        finally:
            await client.close()

    async def test_interrupted_inference_and_conflict_are_not_resubmitted(self):
        for code in (409, 502, 504):
            calls = []
            def respond(request):
                calls.append(request)
                if request.url.path != '/v1/tts':
                    return handshake_response(request)
                return httpx.Response(code)
            client = AudioProviderClient(transport=httpx.MockTransport(respond))
            try:
                with self.assertRaises(GenerationUnknown):
                    await client.generate(capture(), 'speech', {'text': '早呀'})
                self.assertEqual(len([r for r in calls if r.url.path == '/v1/tts']), 1)
            finally:
                await client.close()

    async def test_busy_and_invalid_audio_fail_without_exposing_response(self):
        for response in (httpx.Response(429, text=TOKEN), httpx.Response(200, text='not WAV')):
            client = AudioProviderClient(transport=httpx.MockTransport(lambda request:
                handshake_response(request) if request.url.path != '/v1/tts' else response))
            try:
                with self.assertRaises(AudioError) as error:
                    await client.generate(capture(), 'speech', {'text': '早呀'})
                self.assertNotIn(TOKEN, str(error.exception))
            finally:
                await client.close()

    async def test_busy_health_means_connected_and_does_not_submit_again(self):
        calls = []
        def respond(request):
            calls.append(request)
            return handshake_response(request, ready=False, busy=True, presets=['cyrene'])
        client = AudioProviderClient(transport=httpx.MockTransport(respond))
        try:
            self.assertTrue((await client.pc_status(capture()))['connected'])
            self.assertTrue(client.pc_ready())
            with self.assertRaises(AudioError):
                await client.generate(capture(), 'speech', {'text': '早呀'})
            self.assertFalse(any(r.url.path == '/v1/tts' for r in calls))
        finally:
            await client.close()

    async def test_only_completed_matching_handshake_opens_voice_tool(self):
        for failure in ('wrong_nonce', 'wrong_instance', 'ack_rejected'):
            calls = []
            def respond(request):
                calls.append(request.url.path)
                response = handshake_response(request)
                value = response.json()
                if failure == 'wrong_nonce' and request.url.path == '/health':
                    value['handshake_nonce'] = '0' * 32
                if failure == 'wrong_instance' and request.url.path.endswith('/ack'):
                    value['instance_id'] = '0' * 32
                if failure == 'ack_rejected' and request.url.path.endswith('/ack'):
                    value['acknowledged'] = False
                return httpx.Response(200, json=value)
            client = AudioProviderClient(transport=httpx.MockTransport(respond))
            try:
                with self.subTest(failure=failure):
                    self.assertFalse((await client.pc_status(capture()))['connected'])
                    self.assertFalse(client.pc_ready())
                    self.assertNotIn('/v1/tts', calls)
            finally:
                await client.close()

    async def test_stop_is_acknowledged_even_if_existing_generation_is_busy(self):
        calls = []
        def respond(request):
            calls.append(request)
            return handshake_response(request, ready=False, busy=True, stopping=True)
        client = AudioProviderClient(transport=httpx.MockTransport(respond))
        try:
            self.assertFalse((await client.pc_status(capture()))['connected'])
            self.assertFalse(json.loads(calls[-1].content)['accepted'])
            self.assertFalse(client.pc_ready())
        finally:
            await client.close()


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = folder()
        self.path = self.directory / 'bridge.json'
        self.path.write_text(json.dumps({'token': TOKEN, 'upstream': 'http://127.0.0.1:9881',
            'timeout': 5, 'max_bytes': 100000, 'presets': {
                'cyrene': {'ref_audio_path': 'D:/fixed-reference.wav', 'prompt_text': '固定参考', 'prompt_lang': 'zh'}}}))
        self.calls = []
        def respond(request):
            self.calls.append(request)
            return httpx.Response(200, content=wav_bytes())
        self.transport = httpx.MockTransport(respond)
        self.headers = {'Authorization': 'Bearer ' + TOKEN}
        self.body = {'request_id': IDENTITY, 'text': '早呀', 'preset': 'cyrene'}

    def test_authentication_and_no_management_routes(self):
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            self.assertEqual(client.get('/health').status_code, 401)
            for path in ('/control', '/set_gpt_weights', '/set_sovits_weights', '/docs', '/openapi.json'):
                self.assertEqual(client.get(path, headers=self.headers).status_code, 404)
            response = client.post('/v1/tts', headers=self.headers, json={**self.body, 'ref_audio_path': 'arbitrary'})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('arbitrary', response.text)
            self.assertEqual(len(self.calls), 0)

    def test_same_id_returns_identical_audio_without_regenerating(self):
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            first = client.post('/v1/tts', headers=self.headers, json=self.body)
            second = client.post('/v1/tts', headers=self.headers, json=self.body)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.content, second.content)
            self.assertEqual(second.headers['X-Voice-Cache'], 'hit')
            self.assertEqual(len(self.calls), 1)
            payload = json.loads(self.calls[0].content)
            self.assertEqual(payload['ref_audio_path'], 'D:/fixed-reference.wav')
            self.assertFalse(payload['parallel_infer'])
            self.assertEqual(client.post('/v1/tts', headers=self.headers,
                json={**self.body, 'text': '换内容'}).status_code, 409)

    def test_restart_does_not_repeat_uncertain_inference(self):
        app = bridge.make_app(self.path, transport=self.transport)
        with TestClient(app):
            with sqlite3.connect(self.directory / 'bridge-cache/requests.sqlite3') as db:
                import hashlib
                digest = hashlib.sha256(json.dumps(['早呀', 'cyrene'], ensure_ascii=False).encode()).hexdigest()
                db.execute('INSERT INTO requests(id,digest,state,created,seconds) VALUES(?,?,?,?,?)',
                           (IDENTITY, digest, 'running', 1, 0))
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            self.assertEqual(client.post('/v1/tts', headers=self.headers, json=self.body).status_code, 409)
            self.assertEqual(len(self.calls), 0)

    def test_unknown_preset_and_long_text_do_not_call_model(self):
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            for changes in ({'preset': 'Leda'}, {'text': '啊' * 601}):
                self.assertEqual(client.post('/v1/tts', headers=self.headers, json={**self.body, **changes}).status_code, 422)
            self.assertEqual(len(self.calls), 0)

    def test_authenticated_handshake_is_single_use_and_bound_to_instance(self):
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            health = client.get('/health', headers={**self.headers, 'X-Voice-Handshake': IDENTITY}).json()
            body = {'nonce': IDENTITY, 'instance_id': health['instance_id'], 'accepted': True}
            self.assertEqual(client.post('/v1/handshake/ack', json=body).status_code, 401)
            self.assertEqual(client.post('/v1/handshake/ack', headers=self.headers,
                json={**body, 'instance_id': '0' * 32}).status_code, 409)
            client.get('/health', headers={**self.headers, 'X-Voice-Handshake': IDENTITY})
            self.assertTrue(client.post('/v1/handshake/ack', headers=self.headers, json=body).json()['accepted'])
            value = client.get('/health', headers=self.headers).json()
            self.assertTrue(value['cloud_accepted'])
            self.assertGreater(value['cloud_ack_at'], 0)
            self.assertEqual(client.post('/v1/handshake/ack', headers=self.headers, json=body).status_code, 409)

    def test_stopping_refuses_new_inference_and_accepts_cloud_stop_ack(self):
        from pc_voice_common import request_control
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            request_control(self.directory, False)
            health = client.get('/health', headers={**self.headers, 'X-Voice-Handshake': IDENTITY}).json()
            self.assertFalse(health['ready'])
            self.assertTrue(health['stopping'])
            self.assertEqual(client.post('/v1/tts', headers=self.headers, json=self.body).status_code, 503)
            self.assertEqual(len(self.calls), 0)
            response = client.post('/v1/handshake/ack', headers=self.headers, json={
                'nonce': IDENTITY, 'instance_id': health['instance_id'], 'accepted': False})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['accepted'])

    def test_handshake_expires_before_acceptance(self):
        with TestClient(bridge.make_app(self.path, transport=self.transport)) as client:
            health = client.get('/health', headers={**self.headers, 'X-Voice-Handshake': IDENTITY}).json()
            with patch.object(bridge.time, 'monotonic', return_value=time.monotonic() + 16):
                response = client.post('/v1/handshake/ack', headers=self.headers, json={
                    'nonce': IDENTITY, 'instance_id': health['instance_id'], 'accepted': True})
            self.assertEqual(response.status_code, 409)

    def test_stop_allows_active_inference_to_finish_but_blocks_new_work(self):
        from pc_voice_common import request_control
        started, release = threading.Event(), threading.Event()
        results = []
        async def respond(request):
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return httpx.Response(200, content=wav_bytes())
        with TestClient(bridge.make_app(self.path, transport=httpx.MockTransport(respond))) as client:
            thread = threading.Thread(target=lambda: results.append(
                client.post('/v1/tts', headers=self.headers, json=self.body)), daemon=True)
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                request_control(self.directory, False)
                status = client.get('/health', headers=self.headers).json()
                self.assertTrue(status['busy'])
                self.assertFalse(status['ready'])
                self.assertEqual(client.post('/v1/tts', headers=self.headers,
                    json={**self.body, 'request_id': 'a' * 32}).status_code, 503)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results[0].status_code, 200)
            self.assertEqual(results[0].content, wav_bytes())


class LocalPipelineTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AudioTests.asyncSetUp
    asyncTearDown = fixtures.AudioTests.asyncTearDown
    configure = fixtures.AudioTests.configure

    def local_config(self):
        current = self.service.settings.snapshot()
        values = current['settings']
        values['providers']['pc'] = capture()['provider'].model_dump()
        values['speech_profile'] = 'pc'
        values['presets']['pc'] = VoicePreset(name='昔涟', voice='cyrene').model_dump()
        values['default_preset'] = 'pc'
        self.service.settings.save(current['revision'], values, {'pc': TOKEN})

    async def test_unavailable_pc_does_not_pause_chat_or_paid_fallback(self):
        self.local_config()
        self.provider.error = AudioError('电脑离线')
        job = self.service.enqueue('speech', {'text': '早呀'}, first=self.event)
        await self.service.generate_once()
        await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'failed')
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.runtime.paused_reason, '')
        self.assertEqual(self.provider.calls[0][0]['provider'].backend, 'pc_bridge')

    async def test_actual_request_id_and_local_usage_are_preserved(self):
        self.local_config()
        original = self.provider.generate
        async def generate(*args):
            result = await original(*args)
            result.update(charge_state='local_no_api_charge', usage={})
            return result
        self.provider.generate = generate
        job = self.service.enqueue('speech', {'text': '早呀'}, first=self.event)
        await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'ready')
        self.assertEqual(self.provider.calls[0][0]['request_id'], job['id'])
        row = self.store.db.execute('SELECT * FROM audio_usage WHERE job_id=?', (job['id'],)).fetchone()
        self.assertEqual(row['charge_state'], 'local_no_api_charge')
        self.assertIsNone(row['total_tokens'])

    async def test_excessive_local_text_rejected_before_job_creation(self):
        self.local_config()
        with self.assertRaises(ValueError):
            self.service.enqueue('speech', {'text': '啊' * 601}, first=self.event)
        self.assertEqual(len(self.provider.calls), 0)

    async def test_offline_tools_are_hidden_without_blocking_chat(self):
        self.local_config()
        from types import SimpleNamespace
        engine = SimpleNamespace(api_style='openai', stream_enabled=False, chat_with_tools=True)
        self.provider.pc_ready = lambda: False
        self.assertNotIn('send_voice', [t['function']['name'] for t in self.service.tools.schemas(engine, self.event)])
        self.provider.pc_ready = lambda: True
        tools = self.service.tools.schemas(engine, self.event)
        voice = next(t for t in tools if t['function']['name'] == 'send_voice')
        self.assertEqual(voice['function']['parameters']['properties']['text']['maxLength'], 600)


if __name__ == '__main__':
    unittest.main()

"""Offline audio acceptance: no paid API, live WeChat, or production data."""
import asyncio
import base64
import io
import json
import math
import shutil
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import wave
import array

import httpx
from fastapi.testclient import TestClient
from loguru import logger

from whalegirl_linux.app import create_app
from whalegirl_linux.audio import AudioService
from whalegirl_linux.audio_gateway import AudioGateway, file_matches
from whalegirl_linux.audio_media import probe, tune, tuning_filter
from whalegirl_linux.audio_provider import AudioProviderClient, AudioError, GenerationUnknown, public_destination, result_audio
from whalegirl_linux.audio_settings import AudioProvider, AudioSettings, VoicePreset
from whalegirl_linux.gateway import AgentGateway, Receipt
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
import test_chat_habits as habits

ROOT = Path(__file__).resolve().parents[1]
logger.remove()


def folder():
    (ROOT / 'test-runs').mkdir(exist_ok=True)
    # Retain test artifacts; do not delete files without the user's deletion review.
    return Path(tempfile.mkdtemp(prefix='audio-', dir=ROOT / 'test-runs'))


def wav_bytes(seconds=.2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48000)
        audio.writeframes(b'\x01\x00' * round(seconds * 48000))
    return output.getvalue()


class MockProvider:
    def __init__(self):
        self.calls = []
        self.error = None

    async def generate(self, capture, kind, arguments):
        self.calls.append((capture, kind, arguments))
        if self.error:
            raise self.error
        return {'base64': base64.b64encode(wav_bytes()).decode(), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'generation_seconds': .01}

    async def download(self, result, path, cap):
        path.write_bytes(base64.b64decode(result['base64']))

    async def close(self):
        pass


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = folder(); self.config = Config(self.path)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=['chat'], safety_enabled=False,
                                  min_send_interval=0, chat_cooldown=0), 'fixture-chat-key')
        self.store = Store(self.path / 'state.sqlite3')
        self.runtime = SimpleNamespace(config=self.config, store=self.store, paused_reason='', processing='', last_send=0,
            gateway=SimpleNamespace(status=AsyncMock(return_value={'status': 'logged_in', 'loggedInUser': 'account'})),
            safety=SafetyGuard(self.store), chat_control=SimpleNamespace(allowed=lambda first: True))
        self.provider = MockProvider()
        self.service = AudioService(self.runtime, provider=self.provider)
        self.event = {'key':'fixture-message', 'account':'account', 'chat':'chat', 'sender':'person', 'sender_id':'person',
            'content':'想听你说句话', 'kind':1, 'is_self':False, 'timestamp':time.time()}
        self.store.insert(self.event)
        self.configure()

    def configure(self, **changes):
        snapshot = self.service.settings.snapshot(); values = snapshot['settings']; values.update(enabled=True, **changes)
        return self.service.settings.save(snapshot['revision'], values,
            {'speech':'fixture-speech-secret', 'music':'fixture-music-secret', 'music-pro':'fixture-pro-secret'})

    async def asyncTearDown(self):
        await self.service.close(); self.store.close()

    def test_default_voice_preset_is_leda(self):
        self.assertEqual(AudioSettings().presets['default'].voice, 'Leda')

    def test_settings_hot_update_keeps_chat_generation_and_separate_keys(self):
        before = self.config.generation
        snapshot = self.service.settings.snapshot(); values = snapshot['settings']
        values['providers']['speech']['api_url'] = 'https://speech.example/v1'
        values['providers']['music']['api_url'] = 'https://music.example/v1/chat/completions'
        values['presets']['default']['pitch'] = 1.2
        self.service.settings.save(snapshot['revision'], values)
        self.assertEqual(self.config.generation, before)
        speech = self.service.settings.capture('speech'); music = self.service.settings.capture('music')
        self.assertEqual(speech['key'], 'fixture-speech-secret'); self.assertEqual(music['key'], 'fixture-music-secret')
        self.assertEqual(speech['provider'].endpoint_url(), 'https://speech.example/v1/chat/completions')
        self.assertEqual(music['provider'].endpoint_url(), 'https://music.example/v1/chat/completions')
        self.assertNotIn('fixture-speech-secret', json.dumps(self.service.snapshot()))
        raw = self.store.db.execute('SELECT secrets_json FROM audio_config').fetchone()[0]
        self.assertNotIn('fixture-speech-secret', raw)
        with self.assertRaisesRegex(ValueError, '另一页面'):
            self.service.settings.save(snapshot['revision'], values)

    def test_profile_switch_and_revision_restore_preserve_keys(self):
        self.configure(music_profile='music-pro')
        self.assertEqual(self.service.settings.capture('music')['key'], 'fixture-pro-secret')
        revision = self.service.settings.snapshot()['revision']
        restored = self.service.settings.restore(revision, 1)
        self.assertGreater(restored['revision'], revision)
        self.assertEqual(self.service.settings.capture('music')['key'], 'fixture-music-secret')

    async def test_queue_is_idempotent_and_records_actual_seconds_not_zero_token_free(self):
        job = self.service.enqueue('speech', {'text':'早呀'}, first=self.event)
        self.assertEqual(job['id'], self.service.enqueue('speech', {'text':'不同重复内容'}, first=self.event)['id'])
        await asyncio.gather(self.service.generate_once(), self.service.generate_once())
        self.assertEqual(len(self.provider.calls), 1)
        job = self.service.get(job['id']); self.assertEqual(job['state'], 'ready')
        self.assertAlmostEqual(job['duration'], .2)
        usage = self.service.snapshot()['usage']
        self.assertEqual(usage['submitted_requests'], 1); self.assertAlmostEqual(usage['speech_seconds'], .2)
        self.assertIn('零Token不代表免费', usage['billing'])

    async def test_new_job_adopts_latest_revision_but_inflight_job_keeps_snapshot(self):
        job = self.service.enqueue('speech', {'text':'新声线'}, first=self.event)
        self.configure(singing_style='new preference')
        expected = self.service.settings.snapshot()['revision']
        started, finish = asyncio.Event(), asyncio.Event()
        generate = self.provider.generate
        async def slow(*args):
            started.set(); await finish.wait(); return await generate(*args)
        self.provider.generate = slow
        task = asyncio.create_task(self.service.generate_once()); await started.wait()
        self.configure(singing_style='changed while running'); finish.set(); await task
        self.assertEqual(json.loads(self.service.get(job['id'])['config_json'])['revision'], expected)
        self.assertEqual(self.provider.calls[0][0]['revision'], expected)

    async def test_ambiguous_paid_request_never_auto_retries_or_pauses_chat(self):
        self.provider.error = GenerationUnknown('synthetic timeout')
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        await self.service.generate_once(); await self.service.generate_once()
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.service.get(job['id'])['state'], 'outcome_unknown')
        self.assertEqual(self.runtime.paused_reason, '')
        with self.assertRaises(ValueError): self.service.action(job['id'], 'retry_send')

    async def test_restart_leaves_paid_submission_unknown_and_never_requeues(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        self.service.change(job['id'], state='generating', submitted=time.time())
        second = AudioService(self.runtime, provider=MockProvider())
        self.assertEqual(second.get(job['id'])['state'], 'outcome_unknown')
        self.assertFalse(await second.generate_once()); await second.close()

    async def test_cancel_during_generation_preserves_cancellation(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        async def cancelled(capture, kind, arguments):
            self.service.action(job['id'], 'cancel')
            return {'url':'https://cdn.example/audio.wav', 'generation_seconds':1}
        self.provider.generate = cancelled
        await self.service.generate_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'cancelled')
        self.assertTrue(self.service.get(job['id'])['result_enc'])

    async def test_send_success_is_durable_and_not_sent_twice(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        output = self.service.safe_path(self.service.get(job['id'])['output_file'])
        self.assertTrue(output.is_file())
        self.service.gateway.voice = AsyncMock(return_value=Receipt(True, 'native-voice-id'))
        self.assertTrue(await self.service.deliver_once()); self.assertFalse(await self.service.deliver_once())
        self.assertEqual(self.service.get(job['id'])['state'], 'sent')
        self.assertFalse(output.exists())
        self.assertEqual(self.service.get(job['id'])['output_file'], '')
        self.assertEqual(self.service.get(job['id'])['source_file'], '')
        self.assertEqual(self.service.get(job['id'])['media_cleanup_pending'], 0)
        self.assertEqual(self.store.get(self.event['key'])['state'], 'sent')
        self.assertEqual(self.service.gateway.voice.await_count, 1)

    async def test_cleanup_failure_does_not_downgrade_receipt_and_is_retried(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        output = self.service.safe_path(self.service.get(job['id'])['output_file'])
        self.service.gateway.voice = AsyncMock(return_value=Receipt(True, 'native-voice-id'))
        with patch.object(Path, 'unlink', side_effect=PermissionError('synthetic locked file')):
            self.assertTrue(await self.service.deliver_once())

        current = self.service.get(job['id'])
        self.assertEqual(current['state'], 'sent')
        self.assertEqual(current['media_cleanup_pending'], 1)
        self.assertTrue(output.is_file())

        await self.service.cleanup_expired_previews()
        current = self.service.get(job['id'])
        self.assertEqual(current['media_cleanup_pending'], 0)
        self.assertFalse(output.exists())

    async def test_audio_delivery_uses_account_caps_but_not_text_only_quota(self):
        settings = self.config.settings; settings.safety_enabled = True
        settings.safety_send_limits_enabled = True; settings.safety_per_minute = 20
        settings.safety_per_hour = 20; settings.safety_chat_per_hour = 20; settings.safety_per_day = 20
        settings.text_per_hour = 1; settings.text_per_day = 1
        self.config.save(settings, 'fixture-chat-key')
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at) "
                "VALUES('prior-text','prior-inbox','account','chat','text','普通文字','sent',?)", (time.time(),))
        job = self.service.enqueue('speech', {'text':'语音内容'}, first=self.event); await self.service.generate_once()
        self.service.gateway.voice = AsyncMock(return_value=Receipt(True, 'verified-voice'))
        self.assertTrue(await self.service.deliver_once())
        counts, _, _ = self.runtime.safety.activity('account', chat='chat')
        self.assertEqual(counts['text_hour'], 1); self.assertEqual(counts['audio_hour'], 1)
        self.assertEqual(counts['hour'], 2)

    async def test_definite_pre_send_failure_can_retry_with_fresh_native_key(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        self.service.gateway.voice = AsyncMock(side_effect=[Receipt(False, error='before submit', definite_failure=True),
                                                              Receipt(True, 'verified-second-attempt')])
        await self.service.deliver_once()
        failed_output = self.service.safe_path(self.service.get(job['id'])['output_file'])
        self.assertTrue(failed_output.is_file())
        self.assertTrue(self.service.public_job(self.service.get(job['id']))['can_retry_send'])
        self.service.action(job['id'], 'retry_send'); await self.service.deliver_once()
        first_key = self.service.gateway.voice.await_args_list[0].args[2]
        second_key = self.service.gateway.voice.await_args_list[1].args[2]
        self.assertNotEqual(first_key, second_key)
        self.assertEqual(self.service.get(job['id'])['state'], 'sent')
        self.assertFalse(failed_output.exists())
        self.assertEqual([action['state'] for action in self.store.actions(self.event['key'])], ['failed','sent'])

    async def test_confirmed_send_survives_history_bookkeeping_failure(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        self.service.gateway.voice = AsyncMock(return_value=Receipt(True, 'verified-voice'))
        with patch.object(self.store, 'remember', side_effect=RuntimeError('synthetic history error')):
            self.assertTrue(await self.service.deliver_once())
        self.assertEqual(self.service.get(job['id'])['state'], 'sent')
        self.assertEqual(self.store.actions(self.event['key'])[0]['state'], 'sent')
        self.assertFalse(await self.service.deliver_once())

    async def test_merged_batch_finishes_and_counts_one_send(self):
        other = {**self.event, 'key':'second-message', 'content':'补充一句'}; self.store.insert(other)
        job = self.service.enqueue('speech', {'text':'听见啦'}, first=self.event)
        self.service.bind_sources(job['id'], [self.event, other]); await self.service.generate_once()
        self.service.gateway.voice = AsyncMock(return_value=Receipt(True, 'verified-voice'))
        await self.service.deliver_once()
        self.assertEqual(self.store.get(self.event['key'])['state'], 'sent')
        self.assertEqual(self.store.get(other['key'])['state'], 'merged')
        self.assertEqual(self.store.get(other['key'])['receipt'], 'verified-voice')
        counts, _, _ = self.runtime.safety.activity('account', chat='chat')
        self.assertEqual(counts['minute'], 1)

    async def test_failed_generation_closes_inbox_and_keeps_usage_missing(self):
        self.provider.error = GenerationUnknown('synthetic timeout')
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        await self.service.generate_once()
        self.assertEqual(self.store.get(self.event['key'])['state'], 'uncertain')
        self.assertEqual(self.service.get(job['id'])['state'], 'outcome_unknown')
        usage = self.service.snapshot()['usage']
        self.assertEqual(usage['token_reported_requests'], 0)
        self.assertEqual(usage['token_missing_requests'], 1)

    async def test_real_quiet_control_uses_original_timestamp_and_trigger(self):
        from whalegirl_linux.chat_control import ChatControl
        self.runtime.chat_control = ChatControl(self.runtime)
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        self.runtime.chat_control.apply('chat', 'quiet', 0)
        self.service.gateway.voice = AsyncMock()
        self.assertFalse(await self.service.deliver_once())
        self.service.gateway.voice.assert_not_awaited()
        self.assertEqual(self.service.get(job['id'])['state'], 'ready')

    async def test_local_tuning_cannot_claim_another_model_voice(self):
        job = self.service.enqueue('speech', {'text':'你好'}, preview=True); await self.service.generate_once()
        self.configure(presets={'default':VoicePreset(voice='AnotherVoice').model_dump()})
        with self.assertRaisesRegex(ValueError, '更换模型音色'):
            self.service.enqueue('speech', {'text':'你好'}, preview=True, render_of=job['id'])
        self.assertEqual(len(self.provider.calls), 1)

    async def test_expired_preview_media_is_removed_but_history_remains(self):
        job = self.service.enqueue('speech', {'text':'试听'}, preview=True)
        await self.service.generate_once()
        generated = self.service.get(job['id'])
        output = self.service.safe_path(generated['output_file'])
        self.assertTrue(output.is_file())
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE audio_jobs SET created=? WHERE id=?',
                (time.time() - self.service.PREVIEW_RETENTION_SECONDS - 1, job['id']))

        await self.service.cleanup_expired_previews()

        expired = self.service.get(job['id'])
        self.assertEqual(expired['state'], 'expired')
        self.assertEqual(expired['output_file'], '')
        self.assertEqual(expired['source_file'], '')
        self.assertFalse(output.exists())
        self.assertTrue(expired['id'])
        self.assertEqual(self.service.snapshot()['usage']['submitted_requests'], 1)

    async def test_preview_expiry_keeps_source_while_render_is_queued(self):
        job = self.service.enqueue('speech', {'text':'试听'}, preview=True)
        await self.service.generate_once()
        source = self.service.safe_path(self.service.get(job['id'])['source_file'])
        self.service.enqueue('speech', {'text':'试听'}, preview=True, render_of=job['id'])
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE audio_jobs SET created=? WHERE id=?',
                (time.time() - self.service.PREVIEW_RETENTION_SECONDS - 1, job['id']))

        await self.service.cleanup_expired_previews()

        self.assertEqual(self.service.get(job['id'])['state'], 'ready')
        self.assertTrue(source.is_file())

    async def test_tuning_queued_voice_change_is_rejected_without_model_call(self):
        job = self.service.enqueue('speech', {'text':'你好'}, preview=True); await self.service.generate_once()
        render = self.service.enqueue('speech', {'text':'你好'}, preview=True, render_of=job['id'])
        self.configure(presets={'default':VoicePreset(voice='AnotherVoice').model_dump()})
        await self.service.generate_once()
        self.assertEqual(self.service.get(render['id'])['state'], 'failed')
        self.assertEqual(len(self.provider.calls), 1)

    def test_preview_request_id_deduplicates_and_rejects_parameter_conflict(self):
        job = self.service.enqueue('speech', {'text':'你好'}, preview=True, request_id='a'*32)
        self.assertEqual(job['id'], self.service.enqueue('speech', {'text':'你好'}, preview=True, request_id='a'*32)['id'])
        with self.assertRaisesRegex(ValueError, '参数发生变化'):
            self.service.enqueue('speech', {'text':'另一句'}, preview=True, request_id='a'*32)

    async def test_native_task_reconciliation_is_read_only_and_never_recreates(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        output = self.service.safe_path(self.service.get(job['id'])['output_file'])
        self.service.change(job['id'], state='needs_review', native_job_id='existing-native-job')
        self.service.gateway.voice_receipt = AsyncMock(return_value=Receipt(True, 'verified-later'))
        self.service.gateway.voice = AsyncMock()
        result = await self.service.reconcile(job['id'])
        self.assertEqual(result['state'], 'sent'); self.service.gateway.voice.assert_not_awaited()
        self.assertEqual(self.store.get(self.event['key'])['receipt'], 'verified-later')
        self.assertFalse(output.exists())
        self.assertEqual(self.service.get(job['id'])['output_file'], '')

    async def test_overlong_voice_can_switch_to_file_without_regeneration(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        self.service.change(job['id'], state='held')
        self.service.action(job['id'], 'send_file')
        self.service.gateway.file = AsyncMock(return_value=Receipt(True, 'verified-file'))
        self.service.gateway.voice = AsyncMock()
        await self.service.deliver_once()
        self.service.gateway.voice.assert_not_awaited()
        self.assertEqual(self.service.get(job['id'])['state'], 'sent')
        self.assertTrue(self.service.gateway.file.call_args.args[2].isascii())
        self.assertEqual(len(self.provider.calls), 1)

    async def test_audio_tools_are_omitted_for_streaming_or_unsupported_protocols(self):
        self.configure(); first=self.event;tools=self.service.tools
        engine=SimpleNamespace(api_style='responses',stream_enabled=False,chat_with_tools=lambda:None)
        self.assertEqual(tools.schemas(engine, first),[])
        engine.api_style='openai';engine.stream_enabled=True
        self.assertEqual(tools.schemas(engine, first),[])

    async def test_uncertain_delivery_is_not_resubmitted(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        output = self.service.safe_path(self.service.get(job['id'])['output_file'])
        self.service.gateway.voice = AsyncMock(return_value=Receipt(False, error='unknown', retryable=False))
        await self.service.deliver_once(); await self.service.deliver_once()
        self.assertEqual(self.service.get(job['id'])['state'], 'needs_review')
        self.assertTrue(output.is_file())
        self.assertEqual(self.service.gateway.voice.await_count, 1)
        with self.assertRaises(ValueError): self.service.action(job['id'], 'retry_send')

    async def test_permission_revocation_keeps_generated_asset_and_never_sends(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event); await self.service.generate_once()
        self.configure(voice_enabled=False); self.service.gateway.voice = AsyncMock()
        self.assertFalse(await self.service.deliver_once()); self.service.gateway.voice.assert_not_awaited()
        self.assertTrue(self.service.get(job['id'])['output_file'])

    async def test_low_disk_defers_without_paid_call(self):
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        with patch('whalegirl_linux.audio.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            self.assertFalse(await self.service.generate_once())
        self.assertEqual(self.provider.calls, []); self.assertEqual(self.service.get(job['id'])['state'], 'queued')
        self.assertIn('磁盘', self.service.get(job['id'])['error'])

    def test_unauthorized_chat_and_preset_cannot_create_jobs(self):
        with self.assertRaises(ValueError): self.service.enqueue('speech', {'text':'private'}, first={**self.event,'chat':'other'})
        with self.assertRaises(ValueError): self.service.enqueue('speech', {'text':'private','voice_preset_id':'default'}, first=self.event)
        self.assertEqual(self.service.snapshot()['jobs'], [])

    def test_safe_path_rejects_escape_and_cross_account(self):
        with self.assertRaises(AudioError): self.service.safe_path('../state.sqlite3')
        job = self.service.enqueue('speech', {'text':'你好'}, first=self.event)
        self.config.settings.account_id = 'another-account'
        self.assertIsNone(self.service.get(job['id']))


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def capture(self, url='https://sapi.nyro.lol'):
        return {'provider': AudioProvider(kind='speech', model='gemini-3.8-flash-tts', api_url=url),
                'settings':AudioSettings(), 'preset':VoicePreset(), 'key':'fixture-provider-key'}

    async def test_three_result_shapes_and_protocol(self):
        shapes = [{'data':[{'url':'https://cdn.example/a.wav'}]},
            {'choices':[{'message':{'content':'[生成音频](https://cdn.example/a.wav)'}}]},
            {'choices':[{'message':{'audio':{'data':base64.b64encode(wav_bytes()).decode()}}}]}]
        for url in ('https://sapi.nyro.lol', 'https://sapi.nyro.lol/v1', 'https://sapi.nyro.lol/v1/chat/completions'):
            requests = []
            def transport(request):
                requests.append(request); return httpx.Response(200, json=shapes[0])
            client = AudioProviderClient(transport=httpx.MockTransport(transport))
            result = await client.generate(self.capture(url), 'speech', {'text':'你好'})
            body=json.loads(requests[0].content)
            self.assertEqual(str(requests[0].url),'https://sapi.nyro.lol/v1/chat/completions')
            self.assertEqual(body['audio'],{'voice':'Leda','format':'wav'}); self.assertEqual(body['modalities'],['audio'])
            self.assertEqual(body['messages'],[{'role':'user','content':'你好'}]);self.assertEqual(result['url'],'https://cdn.example/a.wav')
            await client.close()
        for shape in shapes: self.assertTrue(result_audio(shape))

    async def test_music_vocals_and_zero_tokens(self):
        requests=[]
        def transport(request):
            requests.append(request);return httpx.Response(200,json={'data':[{'url':'https://cdn.example/a.mp3'}], 'usage':{'total_tokens':0}})
        client=AudioProviderClient(transport=httpx.MockTransport(transport));capture=self.capture()
        capture['provider']=AudioProvider(kind='music',model='lyria-3.5')
        result=await client.generate(capture,'music',{'prompt':'温暖小曲','lyrics':'[Verse]\n你好','instrumental':False})
        payload=json.loads(requests[0].content);self.assertFalse(payload['instrumental']);self.assertEqual(payload['lyrics'],'[Verse]\n你好')
        self.assertEqual(payload['n'],1);self.assertFalse(payload['stream']);self.assertEqual(result['usage']['total_tokens'],0)
        await client.close()

    async def test_timeout_is_one_post(self):
        calls=[]
        def transport(request):
            calls.append(request);raise httpx.ReadTimeout('synthetic timeout')
        client=AudioProviderClient(transport=httpx.MockTransport(transport))
        with self.assertRaises(GenerationUnknown): await client.generate(self.capture(),'speech',{'text':'你好'})
        self.assertEqual(len(calls),1);await client.close()

    async def test_download_pins_address_and_never_forwards_bearer(self):
        calls=[]
        def transport(request):
            calls.append(request);return httpx.Response(200,content=wav_bytes())
        client=AudioProviderClient(download_transport=httpx.MockTransport(transport),resolver=AsyncMock(return_value=('cdn.example','8.8.8.8')))
        destination=folder()/'audio.bin';await client.download({'url':'https://cdn.example/signed.wav?private=yes'},destination,100_000)
        self.assertEqual(calls[0].url.host,'8.8.8.8');self.assertEqual(calls[0].headers['host'],'cdn.example')
        self.assertNotIn('authorization',calls[0].headers);self.assertEqual(calls[0].extensions['sni_hostname'],'cdn.example')
        self.assertAlmostEqual((await probe(destination))['duration'],.2);await client.close()

    async def test_redirect_is_revalidated_and_overlarge_body_rejected(self):
        calls=[]
        def transport(request):
            calls.append(request);return httpx.Response(302,headers={'location':'http://127.0.0.1/internal'})
        async def resolver(url):
            if url.startswith('http:'): raise AudioError('blocked')
            return 'cdn.example','8.8.8.8'
        client=AudioProviderClient(download_transport=httpx.MockTransport(transport),resolver=resolver)
        with self.assertRaises(AudioError):await client.download({'url':'https://cdn.example/a'},folder()/'a',1000)
        self.assertEqual(len(calls),1);await client.close()
        client=AudioProviderClient(download_transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*2000)),resolver=AsyncMock(return_value=('cdn.example','8.8.8.8')))
        with self.assertRaises(AudioError):await client.download({'url':'https://cdn.example/a'},folder()/'a',1000)
        await client.close()

    async def test_nonpublic_dns_is_rejected(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(AudioError):await public_destination('https://cdn.example/a')
        for url in ('file:///etc/passwd','http://example.com/a','https://user:password@example.com/a'):
            with self.assertRaises(AudioError):await public_destination(url)

    async def test_invalid_and_truncated_wav_rejected(self):
        for raw in (b'<html>server error</html>',wav_bytes()[:-100]):
            path=folder()/'bad.wav';path.write_bytes(raw)
            with self.assertRaises(AudioError):await probe(path)
        self.assertIn('atempo=',tuning_filter(VoicePreset(pitch=2,speed=1.1)))


class AudioRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    def enable_audio(self):
        audio=self.runtime.audio.settings.snapshot();audio['settings']['enabled']=True
        self.runtime.audio.settings.save(audio['revision'],audio['settings'],{'speech':'fixture-audio-key','music':'fixture-music-key'})

    async def test_ai_native_tool_queues_without_extra_chat_call(self):
        self.enable_audio();first=self.event();self.queue(first);engine=await self.runtime.model()
        engine.api_style='openai';engine.stream_enabled=False
        engine.chat_with_tools=AsyncMock(return_value={'content':'','tool_calls':[{'id':'call_audio','function':{'name':'send_voice','arguments':json.dumps({'text':'早呀'})}}]})
        await self.runtime.process_next()
        self.assertEqual(engine.chat_with_tools.await_count,1);self.assertEqual(engine.calls,0)
        self.assertEqual(self.gateway.sent,[]);self.assertEqual(self.store.get(first['key'])['state'],'audio_queued')
        self.assertEqual(len(self.runtime.audio.snapshot()['jobs']),1)
        tools=engine.chat_with_tools.call_args.args[1];self.assertIn('send_voice',[t['function']['name'] for t in tools])

    async def test_disabled_audio_keeps_ordinary_chat_single_call(self):
        first=self.event();self.queue(first);engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'早呀'}]}]
        await self.runtime.process_next();self.assertEqual(engine.calls,1);self.assertEqual(self.gateway.sent,[('text','早呀')])
        self.assertEqual(self.runtime.audio.snapshot()['jobs'],[])

    async def test_mixed_tool_calls_and_malicious_arguments_do_not_generate(self):
        self.enable_audio();first=self.event();tools=self.runtime.audio.tools
        call={'id':'a','function':{'name':'send_voice','arguments':json.dumps({'text':'hello','api_url':'http://127.0.0.1'})}}
        self.assertFalse(tools.handle({'tool_calls':[call]},first,self.config.generation)['ok'])
        call['function']['arguments']='{"text":"hello"}'
        self.assertFalse(tools.handle({'tool_calls':[call,{'function':{'name':'life_apply'}}]},first,self.config.generation)['ok'])
        self.assertEqual(self.runtime.audio.snapshot()['jobs'],[])


class AudioApiTests(unittest.TestCase):
    def test_auth_revision_conflict_preview_requires_charge_ack_and_media_private(self):
        app=create_app(folder(),gateway=habits.Gateway(),engine_factory=habits.Engine,run_background=False)
        service=app.state.runtime.audio
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/audio').status_code,401)
            headers={'Authorization':'Bearer '+app.state.config.admin_token}
            result=client.get('/api/audio',headers=headers).json();body={'revision':result['revision'],'settings':result['settings'],'keys':{'speech':'fixture-private-key'}}
            saved=client.post('/api/audio/config',headers=headers,json=body)
            self.assertEqual(saved.status_code,200);self.assertNotIn('fixture-private-key',saved.text)
            self.assertEqual(client.post('/api/audio/config',headers=headers,json=body).status_code,409)
            self.assertEqual(client.post('/api/audio/preview',headers=headers,json={'arguments':{'text':'hi'}}).status_code,400)
            accepted=client.post('/api/audio/preview',headers=headers,json={'arguments':{'text':'hi'},'acknowledge_charge':True})
            self.assertEqual(accepted.status_code,200)
            self.assertEqual(client.get('/api/audio/jobs/'+accepted.json()['id']+'/media').status_code,401)
            self.assertEqual(client.get('/audio.js').status_code,200)

    def test_invalid_configuration_never_echoes_submitted_api_key(self):
        app=create_app(folder(),gateway=habits.Gateway(),engine_factory=habits.Engine,run_background=False)
        with TestClient(app) as client:
            headers={'Authorization':'Bearer '+app.state.config.admin_token}
            result=client.get('/api/audio',headers=headers).json()
            body={'revision':result['revision'],'settings':result['settings'],'keys':{'speech':'fixture-new-secret-do-not-echo'}}
            body['settings']['cache_mb']=32
            response=client.post('/api/audio/config',headers=headers,json=body)
            self.assertEqual(response.status_code,422)
            self.assertNotIn('fixture-new-secret-do-not-echo',response.text)
            self.assertNotIn('input',response.json())


class AudioReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_voice_requires_verified_chunk_and_acknowledgement(self):
        path=folder()/'speech.wav';path.write_bytes(wav_bytes());requests=[]
        def transport(request):
            requests.append(request)
            if request.method=='POST':return httpx.Response(202,json={'jobId':'native-fixture'})
            return httpx.Response(200,json={'status':'completed','chunks':[{'status':'verified','messageId':7}]})
        client=httpx.AsyncClient(base_url='http://127.0.0.1:6175',transport=httpx.MockTransport(transport))
        gateway=SimpleNamespace(ui_lock=asyncio.Lock(),client=client,messages=AsyncMock(return_value=[{'localId':7,'serverId':'77','type':34,'isSelf':True}]))
        receipt=await AudioGateway(gateway).voice('chat',path,'stable-id',lambda:True,lambda value:None)
        self.assertTrue(receipt.confirmed);self.assertEqual(requests[0].headers['idempotency-key'],'stable-id')
        gateway.messages.return_value=[{'localId':7,'serverId':'0','type':34,'isSelf':True}]
        receipt=await AudioGateway(gateway).voice('chat',path,'stable-id',lambda:True,lambda value:None)
        self.assertFalse(receipt.confirmed);self.assertFalse(receipt.definite_failure)
        await client.aclose()

    def test_file_receipt_requires_matching_own_attachment(self):
        row={'isSelf':True,'type':49,'content':'<msg><appmsg><title>song.mp3</title><type>6</type><appattach><totallen>12</totallen></appattach></appmsg></msg>'}
        self.assertTrue(file_matches(row,'song.mp3',12));self.assertFalse(file_matches(row,'other.mp3',12))
        self.assertFalse(file_matches({**row,'isSelf':False},'song.mp3',12))
        self.assertTrue(file_matches({'isSelf':True,'type':49,'fileName':'send_file_1793674337_song.mp3','fileSize':12},'song.mp3',12))
        self.assertFalse(file_matches({'isSelf':True,'type':49,'fileName':'send_file_1793674337_song.mp3','fileSize':14},'song.mp3',12))

    async def test_file_upload_uses_installed_protocol_and_prefixed_receipt(self):
        path=folder()/'music.mp3';path.write_bytes(b'synthetic-file');calls=[]
        def transport(request):
            calls.append(request);return httpx.Response(200,json={'success':True})
        client=httpx.AsyncClient(base_url='http://127.0.0.1:6175',transport=httpx.MockTransport(transport))
        rows=[[],[{'localId':10,'serverId':'100','type':49,'isSelf':True,'fileName':'send_file_123_whalegirl-fixture.mp3','fileSize':path.stat().st_size}]]
        gateway=SimpleNamespace(ui_lock=asyncio.Lock(),client=client,messages=AsyncMock(side_effect=rows))
        receipt=await AudioGateway(gateway).file('chat',path,'whalegirl-fixture.mp3',lambda:True)
        self.assertTrue(receipt.confirmed);self.assertEqual(len(calls),1)
        payload=json.loads(calls[0].content)
        self.assertEqual(payload['chatId'],'chat');self.assertEqual(base64.b64decode(payload['file']['data']),path.read_bytes())
        await client.aclose()


class LocalFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_ffmpeg_pitch_speed_and_volume(self):
        binary=next((ROOT/'artifacts/audio-implementation-20261108/test-tools').glob('imageio_ffmpeg/binaries/ffmpeg*.exe'),None)
        binary = binary or shutil.which('ffmpeg')
        if not binary:
            self.skipTest('optional local FFmpeg test binary is unavailable')
        directory=folder();source=directory/'source.wav'
        samples=array.array('h',(round(8000*math.sin(2*math.pi*440*i/48000)) for i in range(96000)))
        with wave.open(str(source),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(48000);output.writeframes(samples.tobytes())
        variants=[VoicePreset(),VoicePreset(pitch=2,speed=1.25,gain_db=-6),VoicePreset(speed=1.3)]
        results=[]
        with patch('whalegirl_linux.audio_media.shutil.which',return_value=str(binary)):
            for index,preset in enumerate(variants):
                output=directory/f'tuned-{index}.wav';meta=await tune(source,output,preset)
                with wave.open(str(output),'rb') as audio:
                    audio.readframes(9600);data=array.array('h',audio.readframes(48000))
                crossings=sum(a<0<=b for a,b in zip(data,data[1:]))
                results.append({'duration':meta['duration'],'frequency':crossings/(len(data)/48000),
                    'rms':math.sqrt(sum(value*value for value in data)/len(data)),'peak':max(map(abs,data))})
        self.assertAlmostEqual(results[1]['duration'],2/1.25,delta=.1)
        self.assertAlmostEqual(results[1]['frequency'],440*2**(2/12),delta=5)
        self.assertAlmostEqual(results[1]['rms']/results[0]['rms'],10**(-6/20),delta=.05)
        self.assertAlmostEqual(results[2]['duration'],2/1.3,delta=.1)
        self.assertAlmostEqual(results[2]['frequency'],440,delta=4)
        self.assertLess(results[1]['peak'],32767*.96)
        (directory/'filter-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')

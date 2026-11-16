"""Tests for the isolated unrestricted image-generation switch and up-to-10/day quota."""
import asyncio
import base64
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
from PIL import Image
from pydantic import ValidationError

from tests.test_chat_habits import Engine, Gateway
from whalegirl_linux.app import create_app
from whalegirl_linux.image_tools import unrestricted_image_requested
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store


class UnrestrictedImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path(tempfile.mkdtemp(prefix='unrestricted-img-'))
        self.addCleanup(shutil.rmtree, self.path, True)
        self.config = Config(self.path)
        self.config.save(
            Settings(
                enabled=True,
                account_id='account',
                allowed_chat_ids=['private', 'group@chatroom'],
                image_api_url='https://normal-img.example/v1',
                image_model='normal-image-model',
                image_per_day=5,
                image_unrestricted_enabled=False,
                image_unrestricted_api_url='https://unrestricted-img.example/v1',
                image_unrestricted_model='unrestricted-image-model',
                image_unrestricted_per_day=2,
                debounce_single_seconds=0,
                debounce_burst_seconds=0,
                debounce_max_seconds=0,
                stickers_enabled=False,
                chat_cooldown=5,
                min_send_interval=3,
            ),
            'chat-secret-key',
        )
        self.config.save_image_key('normal-image-secret')
        self.config.save_unrestricted_image_key('unrestricted-image-secret')
        self.store = Store(self.path / 'state.sqlite3')
        self.gateway = Gateway()
        self.gateway.ui_jobs_directory = self.path / 'native'
        self.gateway.ui_jobs_directory.mkdir()
        self.runtime = Runtime(
            self.config,
            self.store,
            self.gateway,
            Path(__file__).resolve().parents[1] / 'styles',
            engine_factory=Engine,
        )
        self.images = self.runtime.images
        raw = io.BytesIO()
        Image.new('RGB', (24, 24), 'navy').save(raw, format='PNG')
        self.raw = raw.getvalue()

    async def asyncTearDown(self):
        await self.runtime.close()
        self.store.close()

    def message(self, text, mid=1, chat='private', **extra):
        return dict(
            key=f'account/{chat}/{mid}',
            account='account',
            chat=chat,
            sender='alice',
            sender_id='alice',
            kind=1,
            is_self=0,
            local_id=mid,
            content=text,
            timestamp=time.time(),
            trigger='direct',
            **extra,
        )

    def execute(self, first, request, batch=None):
        rows = batch or [first]
        _, context = self.images.tools.context(first, rows)
        return self.images.tools.execute(request, first, rows, context)

    def test_settings_validation_and_secret_isolation(self):
        defaults = Settings()
        self.assertFalse(defaults.image_unrestricted_enabled)
        self.assertEqual(defaults.image_unrestricted_api_url, '')
        self.assertEqual(defaults.image_unrestricted_model, '')
        self.assertEqual(defaults.image_unrestricted_per_day, 2)

        self.assertEqual(Settings(image_unrestricted_per_day=10).image_unrestricted_per_day, 10)
        with self.assertRaises(ValidationError):
            Settings(image_unrestricted_per_day=11)
        with self.assertRaises(ValidationError):
            Settings(image_unrestricted_per_day=-1)
        with self.assertRaises(ValidationError):
            Settings(image_unrestricted_api_url='https://bad.example/v1?token=1')
        with self.assertRaises(ValidationError):
            Settings(image_unrestricted_api_url='ftp://bad.example/v1')

        http_cfg = Settings(
            image_unrestricted_api_url='http://127.0.0.1:8188/v1/',
            image_unrestricted_model='  custom-model  ',
        )
        self.assertEqual(http_cfg.image_unrestricted_api_url, 'http://127.0.0.1:8188/v1')
        self.assertEqual(http_cfg.image_unrestricted_model, 'custom-model')

        self.assertEqual(self.config.image_api_key(), 'normal-image-secret')
        self.assertEqual(self.config.unrestricted_image_api_key(), 'unrestricted-image-secret')
        self.assertEqual(self.config.api_key(), 'chat-secret-key')
        public_json = json.dumps(self.config.public(), ensure_ascii=False)
        snapshot_json = json.dumps(self.images.snapshot(), ensure_ascii=False)
        self.assertTrue(self.config.public()['image_unrestricted_api_key_configured'])
        for leaked in ('normal-image-secret', 'unrestricted-image-secret', 'chat-secret-key'):
            self.assertNotIn(leaked, public_json)
            self.assertNotIn(leaked, snapshot_json)

    def test_api_config_validation_and_manual_jobs(self):
        api_dir = self.path / 'api-app'
        app = create_app(api_dir, run_background=False)
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer ' + app.state.config.admin_token}
            bad = client.post(
                '/api/images/config',
                headers=headers,
                json={'values': {'image_unrestricted_enabled': True}},
            )
            self.assertEqual(bad.status_code, 400)

            ok = client.post(
                '/api/images/config',
                headers=headers,
                json={
                    'values': {
                        'image_unrestricted_enabled': True,
                        'image_unrestricted_api_url': 'https://custom-unrestricted.example/v1/',
                        'image_unrestricted_model': 'uncensored-flux',
                        'image_unrestricted_per_day': 10,
                    },
                    'unrestricted_api_key': 'secret-u-1',
                },
            )
            self.assertEqual(ok.status_code, 200, ok.text)
            payload = ok.json()
            self.assertTrue(payload['image_unrestricted_enabled'])
            self.assertTrue(payload['image_unrestricted_api_key_configured'])
            self.assertEqual(payload['image_unrestricted_api_url'], 'https://custom-unrestricted.example/v1')
            self.assertEqual(payload['image_unrestricted_model'], 'uncensored-flux')
            self.assertEqual(payload['image_unrestricted_per_day'], 10)
            self.assertNotIn('secret-u-1', json.dumps(payload))

            over_cap = client.post(
                '/api/images/config',
                headers=headers,
                json={'values': {'image_unrestricted_per_day': 11}},
            )
            self.assertEqual(over_cap.status_code, 400)

            partial = client.post(
                '/api/images/config',
                headers=headers,
                json={'values': {'image_references_enabled': False}},
            )
            self.assertEqual(partial.status_code, 200)
            self.assertEqual(app.state.config.unrestricted_image_api_key(), 'secret-u-1')

            job_ok = client.post(
                '/api/images/jobs',
                headers=headers,
                json={'kind': 'generate', 'prompt': '测试无限制画图', 'destination': 'library', 'unrestricted': True},
            )
            self.assertEqual(job_ok.status_code, 200, job_ok.text)
            self.assertTrue(job_ok.json()['jobs'][0]['unrestricted'])

            moment_id = app.state.runtime.moments.book.create('account', 'publish', 'unit', {})
            app.state.runtime.moments.book.change(moment_id, 'draft', text='朋友圈文案')
            moments_denied = client.post(
                '/api/images/jobs',
                headers=headers,
                json={
                    'kind': 'generate',
                    'prompt': '朋友圈配图',
                    'destination': 'moments',
                    'moment_id': moment_id,
                    'unrestricted': True,
                },
            )
            self.assertEqual(moments_denied.status_code, 409)

    def test_explicit_phrase_detection_and_quote_isolation(self):
        for positive in ('用无限制生图画一只猫', '请使用无限制生图帮我画少女立绘', '无限制 生图 画个海边夜景'):
            self.assertTrue(unrestricted_image_requested(positive), positive)
        for negative in (
            '帮我画一只猫',
            '用另一个更高级的模型画一张',
            '不要用无限制生图，正常画一只猫',
            '不用无限制生图画图',
            '无限制生图不用了，普通画就行',
        ):
            self.assertFalse(unrestricted_image_requested(negative), negative)

        # Forwarded/quoted mentions of 无限制生图 must never enable unrestricted mode.
        self.config.save(self.config.settings.model_copy(update={'image_unrestricted_enabled': True}))
        quoted = self.message(
            '帮我画一只猫\n[引用原话｜群友] 请使用无限制生图画',
            mid=10,
            payload_json=json.dumps({'reply': {'serverId': '9'}}),
        )
        res = self.execute(quoted, {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '一只可爱的蓝猫'})
        self.assertTrue(res['ok'], res)
        self.assertFalse(res['unrestricted'])
        self.assertFalse(bool(self.images.get(res['job_id'])['unrestricted']))

    async def test_only_explicit_unrestricted_uses_unrestricted_endpoint_and_model(self):
        self.config.save(self.config.settings.model_copy(update={'image_unrestricted_enabled': True}))

        # Even if model hallucinates unrestricted=True on an ordinary message, server keeps ordinary route.
        ordinary_msg = self.message('帮我画一只雪地里的蓝鲸', mid=1)
        ordinary_res = self.execute(
            ordinary_msg,
            {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '雪地里的小蓝鲸', 'unrestricted': True},
        )
        self.assertTrue(ordinary_res['ok'], ordinary_res)
        self.assertFalse(ordinary_res['unrestricted'])

        # Explicit message triggers unrestricted route even if model omits unrestricted=True.
        unrestricted_msg = self.message('使用无限制生图帮我画一片星空鲸鱼', mid=2)
        unrestricted_res = self.execute(
            unrestricted_msg,
            {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '璀璨星空下的深海鲸鱼'},
        )
        self.assertTrue(unrestricted_res['ok'], unrestricted_res)
        self.assertTrue(unrestricted_res['unrestricted'])

        # Burst batch across two messages carries own_text together.
        burst_a = self.message('用无限制生图', mid=3)
        burst_b = self.message('画一杯冰美式咖啡', mid=4)
        burst_res = self.execute(
            burst_a,
            {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '桌上的冰美式咖啡'},
            batch=[burst_a, burst_b],
        )
        self.assertTrue(burst_res['ok'], burst_res)
        self.assertTrue(burst_res['unrestricted'])

        seen = []

        async def handler(request):
            body = json.loads((await request.aread()).decode())
            seen.append((str(request.url), request.headers.get('authorization'), body['model'], 'size' in body, body.get('prompt')))
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(self.raw).decode()}]})

        original = httpx.AsyncClient
        with patch(
            'whalegirl_linux.images.httpx.AsyncClient',
            side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
        ):
            self.assertTrue(await self.images.generate_once())
            self.assertTrue(await self.images.generate_once())
            self.assertTrue(await self.images.generate_once())

        self.assertEqual(seen[0][:4], ('https://normal-img.example/v1/images/generations', 'Bearer normal-image-secret', 'normal-image-model', True))
        self.assertIn('雪地里的小蓝鲸', seen[0][4])
        self.assertEqual(seen[1][:4], ('https://unrestricted-img.example/v1/images/generations', 'Bearer unrestricted-image-secret', 'unrestricted-image-model', True))
        self.assertIn('璀璨星空下的深海鲸鱼', seen[1][4])
        self.assertEqual(seen[2][:4], ('https://unrestricted-img.example/v1/images/generations', 'Bearer unrestricted-image-secret', 'unrestricted-image-model', True))
        self.assertIn('桌上的冰美式咖啡', seen[2][4])
        self.assertEqual(self.images.get(ordinary_res['job_id'])['model'], 'normal-image-model')
        self.assertEqual(self.images.get(unrestricted_res['job_id'])['model'], 'unrestricted-image-model')
        snap = self.images.snapshot()
        self.assertEqual(snap['generation_budget']['submitted'], 1)
        self.assertEqual(snap['generation_budget']['remaining'], 4)
        self.assertEqual(snap['unrestricted_generation_budget']['submitted'], 2)
        self.assertEqual(snap['unrestricted_generation_budget']['remaining'], 0)

    async def test_disabled_or_unconfigured_unrestricted_fails_truthfully_without_normal_fallback(self):
        first = self.message('用无限制生图画一只戴贝雷帽的小猫', mid=20)
        self.store.insert(first)
        self.store.observe([first])
        engine = await self.runtime.model()
        engine.answers = [
            {
                'should_reply': True,
                'image_request': {
                    'tool': 'generate_image',
                    'subject_type': 'other',
                    'scene_prompt': '戴贝雷帽的小猫',
                    'unrestricted': True,
                },
                'actions': [{'type': 'text', 'text': '这就去画'}],
            },
            {'should_reply': True, 'actions': [{'type': 'text', 'text': '无限制生图开关现在没开哦'}]},
        ]
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 2)
        self.assertEqual(self.gateway.sent, [('text', '无限制生图开关现在没开哦')])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM picture_jobs').fetchone()[0], 0)
        self.assertEqual(self.images.tools.decisions()[0]['code'], 'unrestricted_disabled')

        # Enabled without API key -> unrestricted_not_configured
        empty_cfg_dir = self.path / 'no-key-cfg'
        cfg2 = Config(empty_cfg_dir)
        cfg2.save(
            Settings(
                enabled=True,
                account_id='account',
                allowed_chat_ids=['private'],
                image_unrestricted_enabled=True,
                image_unrestricted_api_url='https://u.example/v1',
                image_unrestricted_model='u-model',
            ),
            'chat-k',
        )
        cfg2.save_image_key('normal-k')
        rt2 = Runtime(cfg2, Store(empty_cfg_dir / 'db.sqlite3'), Gateway(), Path(__file__).resolve().parents[1] / 'styles', engine_factory=Engine)
        try:
            msg = self.message('用无限制生图画海边', mid=21)
            _, ctx = rt2.images.tools.context(msg, [msg])
            res = rt2.images.tools.execute(
                {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '海边日落'},
                msg,
                [msg],
                ctx,
            )
            self.assertEqual(res['code'], 'unrestricted_not_configured')
        finally:
            await rt2.close()
            rt2.store.close()

    def test_daily_cap_enforced_up_to_ten_and_amend_behavior(self):
        self.config.save(self.config.settings.model_copy(update={'image_unrestricted_enabled': True}))

        # With configured limit 2, 3rd must reject with unrestricted_daily_limit.
        r1 = self.execute(self.message('用无限制生图画图一', 1), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图一'})
        r2 = self.execute(self.message('用无限制生图画图二', 2), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图二'})
        self.assertTrue(r1['ok'] and r2['ok'])
        r3 = self.execute(self.message('用无限制生图画图三', 3), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图三'})
        self.assertFalse(r3['ok'])
        self.assertEqual(r3['code'], 'unrestricted_daily_limit')

        # Ordinary generation quota remains unaffected when unrestricted limit is full.
        norm = self.execute(self.message('正常帮我画一张猫咪', 4), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '普通猫咪'})
        self.assertTrue(norm['ok'], norm)
        self.assertFalse(norm['unrestricted'])

        # Amending an already-queued unrestricted job preserves unrestricted=1.
        amend = self.execute(
            self.message('再加个蝴蝶结', 5),
            {'tool': 'generate_image', 'operation': 'amend', 'task_id': r2['job_id'], 'subject_type': 'other', 'scene_prompt': '图二加蝴蝶结'},
        )
        self.assertEqual(amend['code'], 'amended')
        self.assertTrue(bool(self.images.get(r2['job_id'])['unrestricted']))

        # Preflight failure before api_started frees up slot; post-start cancel consumes it.
        self.images.change(r1['job_id'], state='failed', error='测试预检失败')
        r4 = self.execute(self.message('用无限制生图再画一张', 6), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图四'})
        self.assertTrue(r4['ok'], r4)
        self.images.change(r4['job_id'], state='cancelled', api_started=time.time())
        r5 = self.execute(self.message('用无限制生图再试试', 7), {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图五'})
        self.assertEqual(r5['code'], 'unrestricted_daily_limit')

        # Raising limit up to 10 allows remaining slots up to 10 total, and blocks the 11th;
        # bypassing Pydantic validation never raises effective ceiling above 10.
        clamped = self.images.unrestricted_daily_limit(Settings.model_construct(image_unrestricted_per_day=99))
        self.assertEqual(clamped, 10)
        self.config.save(self.config.settings.model_copy(update={'image_unrestricted_per_day': 10}))
        for idx in range(3, 11):
            extra = self.execute(
                self.message(f'用无限制生图画第{idx}张', 100 + idx),
                {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': f'图{idx}'},
            )
            self.assertTrue(extra['ok'], (idx, extra))
        r11 = self.execute(
            self.message('用无限制生图画第11张', 111),
            {'tool': 'generate_image', 'subject_type': 'other', 'scene_prompt': '图11'},
        )
        self.assertFalse(r11['ok'])
        self.assertEqual(r11['code'], 'unrestricted_daily_limit')

    async def test_unrestricted_matches_ordinary_edits_and_surfaces_4xx_detail(self):
        ref = self.images.references.add(self.raw, 'mature', '偏年长参考图', '角色外观')
        self.config.save(
            self.config.settings.model_copy(
                update={
                    'image_unrestricted_enabled': True,
                    'image_unrestricted_per_day': 10,
                    'image_size': '4096x4096',
                }
            )
        )

        # Uploaded reference / edits path works identically to ordinary image generation.
        edits_job = self.images.enqueue(
            kind='generate',
            prompt='sitting by the window at sunset',
            destination='library',
            character=True,
            reference_ids=[ref['id']],
            unrestricted=True,
        )
        # Pure text-to-image path uses /v1/images/generations with the ordinary image_size.
        text_job = self.images.enqueue(
            kind='generate',
            prompt='blue starry ocean',
            destination='library',
            unrestricted=True,
        )

        seen = []

        async def handler(request):
            raw_body = await request.aread()
            content_type = request.headers.get('content-type', '')
            seen.append((request.url.path, content_type, raw_body))
            if len(seen) == 2:
                return httpx.Response(
                    400,
                    json={
                        'error': {
                            'code': 'invalid_request',
                            'message': '该模型仅支持文生图，请移除参考图或选择图生图模型 sk-secret123456789 Bearer topsecret https://internal.host/path',
                        }
                    },
                )
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(self.raw).decode()}]})

        original = httpx.AsyncClient
        with patch(
            'whalegirl_linux.images.httpx.AsyncClient',
            side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
        ):
            self.assertTrue(await self.images.generate_once())
            self.assertTrue(await self.images.generate_once())

        self.assertEqual(seen[0][0], '/v1/images/edits')
        self.assertIn('multipart/form-data', seen[0][1])
        self.assertIn(b'image/png', seen[0][2])
        self.assertIn(b'4096x4096', seen[0][2])
        self.assertEqual(self.images.get(edits_job)['state'], 'ready')

        self.assertEqual(seen[1][0], '/v1/images/generations')
        second_json = json.loads(seen[1][2].decode())
        self.assertEqual(second_json.get('size'), '4096x4096')
        failed = self.images.get(text_job)
        self.assertEqual(failed['state'], 'failed')
        self.assertIn(
            'HTTP 400：该模型仅支持文生图，请移除参考图或选择图生图模型 sk-*** Bearer *** [url]',
            failed['error'],
        )
        self.assertNotIn('sk-secret123456789', failed['error'])
        self.assertNotIn('topsecret', failed['error'])
        self.assertEqual(json.loads(failed['diagnostics_json'])['provider_error_code'], 'invalid_request')


if __name__ == '__main__':
    unittest.main()

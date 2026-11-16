"""8K canvas settings, API requests and generated originals stay consistent."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import test_images as image_fixture
import test_moments as moments_fixture
from whalegirl_linux.image_canvas import MAX_EDGE, MAX_GENERATED_PIXELS, RATIOS, canvas
from whalegirl_linux.images import normalize_image
from whalegirl_linux.settings import Config, Settings


class CanvasTests(unittest.TestCase):
    def test_8k_canvas_preserves_orientation_and_existing_sizes(self):
        self.assertEqual(canvas('8192x8192', '1:1')['size'], '8192x8192')
        self.assertEqual(canvas('8192x8192', '16:9')['size'], '8192x4608')
        self.assertEqual(canvas('8192x8192', '9:16')['size'], '4608x8192')
        self.assertEqual(canvas('8192x8192', '3:4')['size'], '6144x8192')
        for ratio in RATIOS:
            width, height = map(int, canvas('8192x8192', ratio)['size'].split('x'))
            self.assertLessEqual(max(width, height), MAX_EDGE)
            self.assertFalse(width % 8 or height % 8)
        self.assertEqual(canvas('4096x4096')['size'], '4096x4096')
        self.assertEqual(canvas('1536x1024')['size'], '1536x1024')
        self.assertEqual(canvas('auto')['size'], '1024x1024')
        with self.assertRaises(ValueError):
            canvas('4096x4096', '100000:1')

    def test_settings_accept_8k_and_reject_larger_sizes(self):
        self.assertEqual(Settings(image_size='8192x8192').image_size, '8192x8192')
        with self.assertRaisesRegex(ValueError, '不得超过 8192px'):
            Settings(image_size='9999x9999')
        self.assertEqual(canvas('9999x9999')['size'], '8192x8192')

    def test_saved_4k_setting_is_preserved_on_load(self):
        with tempfile.TemporaryDirectory(prefix='legacy-image-size-') as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'image_size': '4096x4096'}), encoding='utf-8')
            config = Config(Path(directory))
            self.assertEqual(config.settings.image_size, '4096x4096')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['image_size'], '4096x4096')

    def test_config_save_persists_8k_and_rejects_larger_values(self):
        with tempfile.TemporaryDirectory(prefix='save-image-size-') as directory:
            config = Config(Path(directory))
            config.save(config.settings.model_copy(update={'image_size': '8192x8192'}))
            self.assertEqual(config.settings.image_size, '8192x8192')
            self.assertEqual(json.loads(config.path.read_text(encoding='utf-8'))['image_size'], '8192x8192')
            with self.assertRaises(ValueError):
                config.save(config.settings.model_copy(update={'image_size': '9999x9999'}))


class ImageCanvasTests(unittest.IsolatedAsyncioTestCase):
    tearDown = image_fixture.ImageTests.tearDown

    def setUp(self):
        (image_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        image_fixture.ImageTests.setUp(self)
        self.config.save(self.config.settings.model_copy(update={'image_size': '8192x8192', 'image_per_day': 5}))

    async def test_json_request_and_saved_original_keep_8k_pixels(self):
        self.config.save(self.config.settings.model_copy(update={'image_references_enabled': False}))
        job_id = self.service.enqueue(kind='generate', prompt='横向海景插画', aspect_ratio='16:9')
        job = self.service.get(job_id)
        plan = json.loads(job['reference_json'])
        plan['canvas'] = {'base_size': '9999x9999', 'aspect_ratio': '16:9', 'size': '9999x5624'}
        with self.store.lock:
            self.store.db.execute('UPDATE picture_jobs SET reference_json=? WHERE id=?',
                                  (json.dumps(plan), job_id))
            self.store.db.commit()
        self.config.save(self.config.settings.model_copy(update={'image_size': '512x512'}))
        out = io.BytesIO()
        Image.new('RGB', (8192, 4608), 'navy').save(out, format='PNG')
        async def handler(request):
            params = json.loads(request.content)
            self.assertEqual(params['size'], '8192x4608')
            self.assertEqual(request.url.path, '/v1/images/generations')
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(out.getvalue()).decode()}]})
        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.service.generate_once()
        job = self.service.get(job_id)
        self.assertEqual(job['state'], 'ready', job['error'])
        asset = self.service.asset(job['asset_id'])
        self.assertEqual((asset['width'], asset['height']), (8192, 4608))
        with Image.open(self.service.path(asset)) as image:
            self.assertEqual(image.size, (8192, 4608))
        stage = self.runtime.gateway.ui_jobs_directory / 'moments-assets' / (asset['id'] + '.png')
        with Image.open(stage) as image:
            self.assertEqual(image.size, (8192, 4608))

    async def test_character_edit_multipart_uses_vertical_8k_canvas_without_upscaling_output(self):
        self.service.references.add(self.raw, 'girl', '本人图', '角色外观')
        job_id = self.service.enqueue(kind='generate', prompt='站立的鲸鱼少女', character=True, aspect_ratio='3:4')
        async def handler(request):
            body = await request.aread()
            self.assertEqual(request.url.path, '/v1/images/edits')
            self.assertIn(b'6144x8192', body)
            self.assertIn(b'image[]', body)
            self.assertIn(b'\x89PNG\r\n\x1a\n', body)
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(self.raw).decode()}]})
        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.service.generate_once()
        job = self.service.get(job_id)
        self.assertEqual(job['state'], 'ready', job['error'])
        asset = self.service.asset(job['asset_id'])
        self.assertEqual((asset['width'], asset['height']), (80, 60))  # Never upscale a smaller provider result.

    async def test_moments_uses_the_same_8k_original_storage(self):
        self.service.references.add(self.raw, 'girl', '本人图', '角色外观')
        job_id = self.service.enqueue(kind='generate', prompt='海边日常', destination='moments', aspect_ratio='16:9')
        out = io.BytesIO()
        Image.new('RGB', (8192, 4608), 'teal').save(out, format='PNG')
        async def handler(request):
            if request.method == 'GET':
                return httpx.Response(404)
            self.assertIn(b'8192x4608', await request.aread())
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(out.getvalue()).decode()}]})
        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.service.generate_once()
        job = self.service.get(job_id)
        self.assertEqual(job['state'], 'ready', job['error'])
        asset = self.service.asset(job['asset_id'])
        self.assertEqual((asset['width'], asset['height']), (8192, 4608))


class GeneratedPixelLimitTests(unittest.TestCase):
    def test_real_8k_square_is_accepted_but_normal_upload_limit_is_retained(self):
        out = io.BytesIO()
        Image.new('RGB', (8192, 8192), 'navy').save(out, format='PNG')
        raw = out.getvalue()
        with self.assertRaises(ValueError):
            normalize_image(raw)
        data, width, height = normalize_image(raw, max_edge=MAX_EDGE, max_pixels=MAX_GENERATED_PIXELS)
        self.assertEqual((width, height), (8192, 8192))
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (8192, 8192))

    def test_rgb_and_exif_rotation_survive_normalization(self):
        out = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (80, 40), 'red').save(out, format='JPEG', exif=exif)
        _, width, height = normalize_image(out.getvalue())
        self.assertEqual((width, height), (40, 80))


class MomentsCanvasTests(unittest.IsolatedAsyncioTestCase):
    generation_fixture = moments_fixture.MomentsTests.generation_fixture
    tearDown = moments_fixture.MomentsTests.tearDown

    def setUp(self):
        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        moments_fixture.MomentsTests.setUp(self)

    async def test_model_ratio_is_saved_and_forwarded_to_the_original_image_task(self):
        self.generation_fixture({'participate': True, 'text': '今天画了个小冒险', 'media_mode': 'generate',
            'image_prompt': '纵向三格冒险小漫画', 'image_aspect_ratio': '9:16'}, image_size='1024x1024')
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'awaiting_image')
        self.assertEqual(json.loads(row['payload'])['image_aspect_ratio'], '9:16')
        self.assertEqual(self.jobs[0]['aspect_ratio'], '9:16')
        self.assertIn('image_aspect_ratio', self.model_inputs[0][1]['system_prompt'])
        self.assertEqual(len(self.model_inputs), 1)
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()

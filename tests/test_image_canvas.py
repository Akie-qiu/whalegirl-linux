"""Canvas selection reaches generation and 4K pixels survive library storage."""
import base64
import io
import json
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import test_images as image_fixture
import test_moments as moments_fixture
from whalegirl_linux.image_canvas import RATIOS, canvas


class CanvasTests(unittest.TestCase):
    def test_4k_dimensions_preserve_long_edge_and_orientation(self):
        self.assertEqual(canvas('4096x4096', '1:1')['size'], '4096x4096')
        self.assertEqual(canvas('4096x4096', '16:9')['size'], '4096x2304')
        self.assertEqual(canvas('4096x4096', '9:16')['size'], '2304x4096')
        self.assertEqual(canvas('4096x4096', '3:4')['size'], '3072x4096')
        for ratio in RATIOS:
            width, height = map(int, canvas('4096x4096', ratio)['size'].split('x'))
            self.assertEqual(max(width, height), 4096)
            self.assertFalse(width % 8 or height % 8)
        self.assertEqual(canvas('1536x1024')['size'], '1536x1024')
        with self.assertRaises(ValueError):
            canvas('4096x4096', '100000:1')


class ImageCanvasTests(unittest.IsolatedAsyncioTestCase):
    tearDown = image_fixture.ImageTests.tearDown

    def setUp(self):
        (image_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        image_fixture.ImageTests.setUp(self)
        self.config.save(self.config.settings.model_copy(update={'image_size': '4096x4096', 'image_per_day': 5}))

    async def test_json_generation_keeps_selected_ratio_and_4k_pixels_with_references_disabled(self):
        self.config.save(self.config.settings.model_copy(update={'image_references_enabled': False}))
        job_id = self.service.enqueue(kind='generate', prompt='横向海景插画', aspect_ratio='16:9')
        self.config.save(self.config.settings.model_copy(update={'image_size': '1024x1024'}))
        out = io.BytesIO()
        Image.new('RGB', (4096, 2304), 'navy').save(out, format='PNG')
        async def handler(request):
            params = json.loads(request.content)
            self.assertEqual(params['size'], '4096x2304')
            self.assertEqual(request.url.path, '/v1/images/generations')
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(out.getvalue()).decode()}]})
        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.service.generate_once()
        job = self.service.get(job_id)
        self.assertEqual(job['state'], 'ready', job['error'])
        asset = self.service.asset(job['asset_id'])
        self.assertEqual((asset['width'], asset['height']), (4096, 2304))
        with Image.open(self.service.path(asset)) as image:
            self.assertEqual(image.size, (4096, 2304))
        stage = self.runtime.gateway.ui_jobs_directory / 'moments-assets' / (asset['id'] + '.png')
        with Image.open(stage) as image:
            self.assertEqual(image.size, (4096, 2304))

    async def test_character_edit_multipart_has_vertical_4k_size_and_native_reference_pixels(self):
        self.service.references.add(self.raw, 'girl', '本人图', '角色外观')
        job_id = self.service.enqueue(kind='generate', prompt='站立的鲸鱼少女', character=True, aspect_ratio='3:4')
        async def handler(request):
            body = await request.aread()
            self.assertEqual(request.url.path, '/v1/images/edits')
            self.assertIn(b'3072x4096', body)
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


class MomentsCanvasTests(unittest.IsolatedAsyncioTestCase):
    generation_fixture = moments_fixture.MomentsTests.generation_fixture
    tearDown = moments_fixture.MomentsTests.tearDown

    def setUp(self):
        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        moments_fixture.MomentsTests.setUp(self)

    async def test_model_ratio_is_saved_and_forwarded_to_the_original_image_task(self):
        self.generation_fixture({'participate': True, 'text': '今天画了个小冒险', 'media_mode': 'generate',
            'image_prompt': '纵向三格冒险小漫画', 'image_aspect_ratio': '9:16'}, image_size='4096x4096')
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'awaiting_image')
        self.assertEqual(json.loads(row['payload'])['image_aspect_ratio'], '9:16')
        self.assertEqual(self.jobs[0]['aspect_ratio'], '9:16')
        self.assertIn('image_aspect_ratio', self.model_inputs[0][1]['system_prompt'])
        self.assertEqual(len(self.model_inputs), 1)
        self.assertFalse(self.calls)


if __name__ == '__main__':
    unittest.main()

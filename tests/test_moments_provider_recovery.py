"""Provider failures defer generation, without replaying sends or hiding evidence."""
import time
import base64
import json
import unittest
from unittest.mock import patch
from io import BytesIO

import httpx
from PIL import Image

import test_images as pictures
import test_moments as moments
from whalegirl_linux.moments import MomentsService


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    setUp = pictures.ImageTests.setUp
    tearDown = pictures.ImageTests.tearDown

    async def test_moments_preserves_provider_resolution_within_8k_cap(self):
        output = BytesIO()
        Image.new('RGB', (1254, 1254), 'blue').save(output, format='PNG')
        moment = self.book.create('account', 'publish', 'oversized-provider', {})
        self.book.change(moment, 'awaiting_image', text='测试草稿')
        self.service.references.add(self.raw, 'girl', '测试角色图', '测试外观')
        identity = self.service.enqueue(kind='generate', prompt='蓝色小鱼', destination='moments', moment_id=moment)
        seen = []
        def respond(request):
            seen.append(request.method)
            if request.method == 'GET':
                return httpx.Response(200, json={'data': [{'id': self.config.settings.image_model}]})
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(output.getvalue()).decode()}]})
        real_client = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient',
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)):
            await self.service.generate_once()
        job = self.service.get(identity)
        self.assertEqual(job['state'], 'ready', job['error'])
        asset = self.service.asset(job['asset_id'])
        self.assertEqual((asset['width'], asset['height']), (1254, 1254))
        staged = self.runtime.gateway.ui_jobs_directory / 'moments-assets' / (asset['id'] + '.png')
        with Image.open(staged) as image:
            self.assertEqual(image.size, (1254, 1254))
        self.assertEqual(seen, ['GET', 'POST'])
        self.assertEqual(self.book.get('account', moment)['state'], 'awaiting_image')

    async def test_missing_model_is_cached_and_config_changes_are_rechecked(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'data': [{'id': 'available-1k'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, '不在接口可用列表'):
                    await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key-a', 'missing')
            self.assertEqual(len(requests), 1)
            await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key-a', 'available-1k')
            await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key-a', 'available-1k')
            self.assertEqual(len(requests), 2)
            await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key-b', 'available-1k')
            self.assertEqual(len(requests), 3)
        self.assertTrue(all(r.method == 'GET' for r in requests))

    async def test_explicit_authorization_or_rate_failure_prevents_generation(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                        lambda request: httpx.Response(status))) as client:
                    with self.assertRaisesRegex(ValueError, 'HTTP ' + str(status)):
                        await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key', str(status))

    async def test_unsupported_metadata_keeps_existing_protocol(self):
        for response in (httpx.Response(404), httpx.Response(200, text='not JSON'),
                         httpx.Response(200, json={'data': [{'id': 'other'}], 'has_more': True})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                await self.service.check_moments_model(client, 'https://provider.invalid/v1', 'key', str(id(response)))

    async def test_missing_model_never_submits_or_claims_a_paid_request(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={'data': [{'id': 'other-1k'}]})
        moment = self.book.create('account', 'publish', 'catalog-failure', {})
        self.book.change(moment, 'awaiting_image', text='测试文案保留')
        self.service.references.add(self.raw, 'girl', '测试角色图', '测试外观')
        identity = self.service.enqueue(kind='generate', prompt='蓝色小鱼', destination='moments', moment_id=moment)
        real_client = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient',
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs)):
            await self.service.generate_once()
        self.assertEqual(self.service.get(identity)['state'], 'failed')
        self.assertFalse(self.service.get(identity)['api_started'])
        self.assertTrue(requests)
        self.assertTrue(all(r.method == 'GET' for r in requests))
        row = self.book.get('account', moment)
        self.assertEqual(row['text'], '测试文案保留')
        self.assertIn('不在接口可用列表', row['error'])


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = moments.MomentsTests.setUp
    tearDown = moments.MomentsTests.tearDown
    generation_fixture = moments.MomentsTests.generation_fixture
    cached_comments = moments.MomentsTests.cached_comments
    draft = moments.MomentsTests.draft

    async def test_real_generation_failure_records_retry_and_single_attempt(self):
        self.generation_fixture({})
        options, calls = [], []
        class Engine:
            last_error = 'HTTP 429（接口限流）'
            async def chat(self, *args, **kwargs):
                calls.append(True)
                return ''
            async def close(self):
                pass
        def factory(**kwargs):
            options.append(kwargs)
            return Engine()
        self.runtime.engine_factory = factory
        with self.assertRaisesRegex(ValueError, '429'):
            await self.service.generate('publish')
        self.assertEqual(len(calls), 1)
        self.assertEqual((options[0]['timeout'], options[0]['max_retries']), (90, 1))
        recovery = self.service.generation_health(self.runtime.config.settings)
        self.assertEqual(recovery['category'], 'rate_limit')
        self.assertGreater(recovery['retry_at'], time.time() + 290)
        self.assertIn('429', self.service.book.recent('account')[0]['error'])

    async def test_cooldown_survives_restart_but_reads_continue_and_retry_recovers(self):
        self.cached_comments()
        settings = self.runtime.config.settings
        self.service.record_generation_health(settings, 'HTTP 429')
        self.service = MomentsService(self.runtime)
        self.cached_comments()
        await self.service.tick()
        self.assertEqual(self.cache_reads, ['/moments'])
        self.assertFalse(self.calls)
        self.assertIsNone(self.service.book.find_source('account', 'reply_own', 'post/1'))
        health = self.service.generation_health(settings)
        health['retry_at'] = time.time() - 1
        self.service.book.save_state('account/generation-health', health)
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.book.find_source('account', 'reply_own', 'post/1')['state'], 'sent')
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_already_generated_reply_is_not_blocked_by_generation_cooldown(self):
        post = self.cached_comments()
        identity = self.service.book.create('account', 'reply_own', 'post/1',
            {'post': post, 'comment': post['comments'][0]})
        self.service.book.change(identity, 'draft', text='已生成的测试回复')
        self.service.record_generation_health(self.runtime.config.settings, 'HTTP 429')
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.book.get('account', identity)['state'], 'sent')

    def test_backoff_is_bounded_config_change_resets_it_and_success_clears(self):
        settings = self.runtime.config.settings
        for _ in range(10):
            self.service.record_generation_health(settings, 'HTTP 429')
        recovery = self.service.generation_health(settings)
        self.assertEqual(recovery['failures'], 6)
        self.assertLessEqual(recovery['retry_at'] - time.time(), 1800)
        self.assertNotIn('retry_at', self.service.generation_health(settings.model_copy(update={'model': 'other'})))
        self.service.record_generation_health(settings)
        self.assertEqual(self.service.generation_health(settings)['retry_at'], 0)
        self.assertEqual(self.service.generation_health(settings)['failures'], 0)
        self.assertIn('last_success', self.service.generation_health(settings))


if __name__ == '__main__':
    unittest.main()

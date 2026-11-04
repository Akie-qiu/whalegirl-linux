"""Paid POST stays single; task reads and empty/error responses stay truthful."""
import base64
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests import test_images as image_fixture
from whalegirl_linux.images import GenerationUncertain, image_response_shape


class ResultProtocolTests(unittest.IsolatedAsyncioTestCase):
    setUp = image_fixture.ImageTests.setUp
    tearDown = image_fixture.ImageTests.tearDown

    def body(self):
        return {'data': [{'b64_json': base64.b64encode(self.raw).decode()}], 'usage': {'input_tokens': 9}}

    async def run_job(self, responses):
        identity = self.service.enqueue(kind='generate', prompt='画一只小蓝鲸')
        seen = []
        async def handler(request):
            seen.append((request.method, request.url.host, request.url.path))
            status, body = responses[len(seen)-1]
            return httpx.Response(status, json=body)
        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw:
                original(transport=httpx.MockTransport(handler), **kw)), \
                patch('whalegirl_linux.images.asyncio.sleep', new=AsyncMock()):
            self.assertTrue(await self.service.generate_once())
        self.assertEqual(sum(v[0] == 'POST' for v in seen), 1)
        return self.service.get(identity), seen

    async def test_http_200_empty_data_is_diagnosed_without_resubmitting(self):
        job, seen = await self.run_job([(200, {'created': 1, 'data': [], 'usage': {'output_tokens': 2}})])
        self.assertEqual(job['state'], 'uncertain')
        diag = json.loads(job['diagnostics_json'])
        self.assertEqual(diag['response_shape']['data_items'], 0)
        self.assertIn('没有可用图片', job['error'])
        self.assertNotIn('需要供应商的查询文档', job['error'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(json.loads(job['usage_json'])['output_tokens'], 2)

    async def test_http_200_explicit_rejection_keeps_secrets_out_of_diagnostics(self):
        job, _ = await self.run_job([(200, {'error': {'code': 'invalid_api_key',
            'message': 'sk-fake-secret https://invalid.example/?token=fake', 'prompt': 'private request'}})])
        self.assertEqual(job['state'], 'failed')
        diag = json.loads(job['diagnostics_json'])
        self.assertEqual(diag['provider_error_code'], 'invalid_api_key')
        for value in ('sk-fake-secret', 'token=fake', 'private request'):
            self.assertNotIn(value, job['diagnostics_json']+job['error'])

    async def test_upstream_error_keeps_billing_uncertain_without_a_second_post(self):
        job, _ = await self.run_job([(200, {'error': {'type': 'upstream_error', 'message': 'not an image'}})])
        self.assertEqual(job['state'], 'uncertain')
        self.assertEqual(json.loads(job['diagnostics_json'])['provider_error_code'], 'upstream_error')

    async def test_usable_image_is_not_overridden_by_optional_error_or_empty_first_item(self):
        body = self.body()
        body['error'] = {'type': 'api_error', 'message': 'optional warning'}
        body['data'].insert(0, {'revised_prompt': 'no image in this item'})
        job, _ = await self.run_job([(200, body)])
        self.assertEqual(job['state'], 'ready')
        self.assertTrue(job['asset_id'])
        self.assertEqual(job['error'], '')
        self.assertEqual(json.loads(job['usage_json'])['input_tokens'], 9)

    async def test_completed_task_unwraps_result_without_extra_requests(self):
        job, seen = await self.run_job([(200, {'object': 'image.generation.task', 'status': 'completed',
            'task_id': 'imgtask_test1234', 'result': self.body()})])
        self.assertEqual(job['state'], 'ready')
        self.assertEqual(len(seen), 1)

    async def test_processing_task_only_polls_original_origin_then_saves_result(self):
        job, seen = await self.run_job([
            (202, {'object': 'image.generation.task', 'task_id': 'imgtask_test1234', 'status': 'processing',
                   'poll_url': 'https://untrusted.example/task'}),
            (200, {'object': 'image.generation.task', 'task_id': 'imgtask_test1234', 'status': 'completed',
                   'result': self.body()}),
        ])
        self.assertEqual(job['state'], 'ready')
        self.assertEqual(seen[1], ('GET', seen[0][1], '/v1/images/tasks/imgtask_test1234'))
        diag = json.loads(job['diagnostics_json'])
        self.assertEqual(diag['poll_attempts'], 1)
        self.assertEqual(diag['provider_status'], 'completed')

    async def test_failed_task_and_failed_status_read_do_not_trigger_generation_retry(self):
        job, seen = await self.run_job([
            (202, {'task_id': 'imgtask_test1234', 'status': 'processing'}),
            (404, {'error': {'type': 'invalid_request_error'}}),
        ])
        self.assertEqual(job['state'], 'uncertain')
        self.assertIn('查询未确认', job['error'])
        self.assertEqual(len(seen), 2)
        self.assertEqual(json.loads(job['diagnostics_json'])['provider_task_id'], 'imgtask_test1234')

    async def test_explicit_failed_task_is_not_left_waiting_for_a_picture(self):
        job, seen = await self.run_job([(200, {'object': 'image.generation.task', 'task_id': 'imgtask_test1234',
            'status': 'failed', 'error': {'type': 'upstream_error', 'message': 'no output'}})])
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(len(seen), 1)

    async def test_malformed_data_remains_uncertain_instead_of_raising_secondary_errors(self):
        job, _ = await self.run_job([(200, {'data': 23, 'private_key': 'never retained'})])
        self.assertEqual(job['state'], 'uncertain')
        self.assertEqual(json.loads(job['diagnostics_json'])['response_shape']['data_type'], 'int')
        self.assertNotIn('private_key', job['diagnostics_json'])

    async def test_task_deadline_does_not_resubmit_or_read_after_expiry(self):
        identity = self.service.enqueue(kind='generate', prompt='画小蓝鲸')
        job = self.service.get(identity)
        client = AsyncMock()
        response = httpx.Response(202, json={'task_id': 'imgtask_test1234', 'status': 'processing'})
        with self.assertRaises(GenerationUncertain):
            await self.service.generation_result(client, response, job,
                'https://image.example/v1/images/generations', 'secret', time.monotonic()-1)
        client.post.assert_not_awaited()
        client.get.assert_not_awaited()

    def test_response_metadata_contains_only_known_field_names_and_shapes(self):
        value = {'data': [{'b64_json': 'image-bytes', 'url': 'https://signed.example/?secret=x',
                          'revised_prompt': 'private request'}], 'sk-fake-secret': 'do not record'}
        meta = json.dumps(image_response_shape(value))
        for secret in ('image-bytes', 'signed.example', 'private request', 'sk-fake-secret'):
            self.assertNotIn(secret, meta)


if __name__ == '__main__':
    unittest.main()

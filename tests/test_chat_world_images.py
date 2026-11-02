"""Chat calls must attach the configured partners, not anonymous stand-ins."""
import base64
import io
import json
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

import test_image_tools as fixture
from test_moments_world import configure


class ChatWorldImageTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ImageToolTests.asyncTearDown
    message = fixture.ImageToolTests.message
    execute = fixture.ImageToolTests.execute

    async def asyncSetUp(self):
        (fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        await fixture.ImageToolTests.asyncSetUp(self)

    def partners(self):
        identities = {}
        for actor, color in (('gpt', 'white'), ('claude', 'orange'), ('gemini', 'purple')):
            data = io.BytesIO()
            Image.new('RGB', (20, 20), color).save(data, 'PNG')
            identities[actor] = self.images.references.store(data.getvalue())['id']
        configure(self.config, identities)
        return identities

    def request(self, cast, operation='create', **extra):
        return {'tool': 'generate_image', 'subject_type': 'self' if 'whalegirl' in cast else 'other',
            'scene_prompt': '大肥鱼和生活伙伴一起做晚饭，人物各自分工',
            'cast_ids': cast, 'operation': operation, 'aspect_ratio': '4:3', **extra}

    async def test_chat_tool_carries_all_four_original_pixels_to_edits_and_does_not_tag_as_self(self):
        ids = self.partners()
        first = self.message('画一张你们几个AI一起生活的图片')
        prompt, context = self.images.tools.context(first, [first])
        for actor in ('whalegirl', 'gpt', 'claude', 'gemini'):
            self.assertIn(actor + '=', prompt)
        self.assertIn('默认四位都出场', prompt)
        result = self.images.tools.execute(self.request(['whalegirl', 'gpt', 'claude', 'gemini']), first, [first], context)
        self.assertTrue(result['ok'], result)
        job = self.images.get(result['job_id'])
        plan = json.loads(job['reference_json'])
        self.assertEqual(result['reference_count'], 4)
        self.assertEqual({r['character_id'] for r in plan['inputs'] if r['source'] == 'story_character'}, set(ids))
        original = httpx.AsyncClient
        async def handler(request):
            body = await request.aread()
            self.assertEqual(request.url.path, '/v1/images/edits')
            for ref in plan['inputs']:
                self.assertIn(self.images.references.path(ref['id']).read_bytes(), body)
            for name in ('小龙娘', '小克', '小星'):
                self.assertIn((name + '角色设定图').encode(), body)
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(self.raw).decode()}]})
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.images.generate_once()
        saved = self.images.get(job['id'])
        self.assertEqual(saved['state'], 'ready', saved['error'])
        self.assertEqual(self.images.asset(saved['asset_id'])['character_id'], '')
        self.assertFalse(self.gateway.sent)

    async def test_amend_rebinds_cast_and_partner_only_image_has_no_self(self):
        self.partners()
        first = self.message('画你和小星')
        result = self.execute(first, self.request(['whalegirl', 'gemini']))
        followup = self.message('改成只画小龙娘和小克', 2)
        amended = self.execute(followup, self.request(['gpt', 'claude'], operation='amend', task_id=result['job_id']))
        self.assertTrue(amended['ok'], amended)
        plan = json.loads(self.images.get(result['job_id'])['reference_json'])
        self.assertFalse(plan['character'])
        self.assertEqual(plan['cast_ids'], ['gpt', 'claude'])
        self.assertEqual({r['character_id'] for r in plan['inputs']}, {'gpt', 'claude'})
        self.assertEqual(self.images.generation_count(), 1)

    async def test_disabled_or_unknown_cast_is_rejected_before_a_paid_request(self):
        self.partners()
        invalid = self.execute(self.message('画一个人'), self.request(['not-a-character']))
        self.assertFalse(invalid['ok'])
        self.assertEqual(invalid['code'], 'invalid_cast')
        self.runtime.moments.world.save(False)
        self.assertNotIn('聊天生图可用的生活伙伴', self.images.tools.static_prompt())
        disabled = self.execute(self.message('画小克', 2), self.request(['claude']))
        self.assertFalse(disabled['ok'])
        self.assertEqual(self.images.generation_count(), 0)

    async def test_character_directory_is_stable_across_task_and_world_event_changes(self):
        self.partners()
        prompt = self.images.tools.static_prompt(include_catalog=False)
        self.execute(self.message('画你和小克'), self.request(['whalegirl', 'claude']))
        self.assertEqual(self.images.tools.static_prompt(include_catalog=False), prompt)


if __name__ == '__main__':
    unittest.main()

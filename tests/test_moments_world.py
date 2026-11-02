"""World continuity and correctly bound multi-character images on the real paths."""
import asyncio
from itertools import combinations
import json
from pathlib import Path
import tempfile
import unittest

import test_moments as moments_fixture
import test_moments_references as references_fixture
from whalegirl_linux.moments import MomentsService
from whalegirl_linux.moments_world import CAST, CAST_IDS, MomentsWorld, config_path


def configure(config, ids=None):
    path = config_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'enabled': True, 'premise': '云端城公寓',
        'characters': [{**CAST[c], 'reference_id': (ids or {}).get(c, 'a' * 64)}
                       for c in CAST_IDS if c != 'whalegirl']}, ensure_ascii=False), encoding='utf-8')


def answer():
    return {'participate': True, 'text': '今天跟小星去修望远镜\n修好以后才发现 镜头盖还没取下来',
        'media_mode': 'generate', 'image_prompt': '天台上修望远镜，轻松的两格漫画',
        'world_design': {'premise': '大家住在同一栋公寓，各有自己的小目标',
            'relationships': [{'a': a, 'b': b, 'dynamic': '会互相吐槽，也会主动帮忙', 'stage': '熟悉'}
                              for a, b in combinations(CAST_IDS, 2)],
            'goals': [{'character_id': c, 'goal': '完成自己的新作品'} for c in CAST_IDS]},
        'world_episode': {'summary': '和小星修好望远镜，发现忘记取镜头盖', 'location': '天台',
            'cast_ids': ['whalegirl', 'gemini'],
            'panels': [{'cast_ids': ['whalegirl', 'gemini'], 'shot': '全景', 'action': '两人分工修理望远镜'},
                       {'cast_ids': ['gemini'], 'shot': '近景', 'action': '小星拿下镜头盖，有点心虚'}],
            'relationship_updates': [{'a': 'whalegirl', 'b': 'gemini', 'dynamic': '约好下次先检查镜头盖', 'stage': '信任'}],
            'goal_updates': [{'character_id': 'gemini', 'goal': '认真做一次观测前检查'}]}}


class WorldGenerationTests(unittest.IsolatedAsyncioTestCase):
    generation_fixture = moments_fixture.MomentsTests.generation_fixture
    tearDown = moments_fixture.MomentsTests.tearDown

    def setUp(self):
        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        moments_fixture.MomentsTests.setUp(self)
        db_path = self.store.db.execute('PRAGMA database_list').fetchone()[2]
        self.runtime.config.directory = Path(db_path).parent
        configure(self.runtime.config)

    async def create_world_draft(self):
        self.generation_fixture(answer())
        return await self.service.generate('publish')

    async def test_first_person_multi_cast_generation_uses_one_call_and_keeps_draft_out_of_life_events(self):
        row = await self.create_world_draft()
        self.assertEqual(row['state'], 'awaiting_image')
        self.assertEqual(len(self.model_inputs), 1)
        data, options = self.model_inputs[0]
        self.assertFalse(data['life_world']['established'])
        self.assertIn('第一人称', options['system_prompt'])
        self.assertNotIn('借来的剧情可成为假想', options['system_prompt'])
        self.assertEqual(self.service.world.snapshot()['recent_events'], [])
        self.assertEqual(len(self.service.world.snapshot()['design']['relationships']), 6)
        self.assertFalse(self.calls)

    async def test_uncertain_cancelled_and_failed_posts_do_not_advance_but_confirmed_is_idempotent(self):
        row = await self.create_world_draft()
        for state in ('cancelled', 'image_failed', 'uncertain'):
            self.service.book.change(row['id'], state)
            self.assertFalse(self.service.world.confirm(row['id']))
            self.assertEqual(self.service.world.snapshot()['recent_events'], [])
        self.service.book.change(row['id'], 'sent', receipt='native-receipt')
        self.assertTrue(self.service.world.confirm(row['id']))
        self.assertFalse(self.service.world.confirm(row['id']))
        snap = self.service.world.snapshot()
        self.assertEqual(len(snap['recent_events']), 1)
        self.assertEqual(snap['version'], 2)
        self.assertTrue(any(r['dynamic'] == '约好下次先检查镜头盖' for r in snap['design']['relationships']))

    async def test_native_send_confirmation_advances_world_once(self):
        row = await self.create_world_draft()
        self.service.book.change(row['id'], 'draft')
        async def sent(*args, **kwargs):
            return {'confirmed': True, 'receipt': 'native'}
        self.runtime.gateway.moments = sent
        delivered = await self.service.send(row['id'])
        self.assertEqual(delivered['state'], 'sent')
        self.assertEqual(len(self.service.world.snapshot()['recent_events']), 1)

    async def test_world_is_role_and_account_scoped_and_restart_reconciles_confirmed_events(self):
        row = await self.create_world_draft()
        self.service.book.change(row['id'], 'sent', receipt='manual-confirmed')
        self.service = MomentsService(self.runtime)
        context = self.service.world.prepare()
        self.assertEqual(len(context['recent_events_newest_first']), 1)
        self.assertNotIn('panels', context['recent_events_newest_first'][0])
        self.runtime.config.settings = self.runtime.config.settings.model_copy(update={'active_persona_id': 'new-role'})
        self.assertFalse(self.service.world.prepare()['established'])
        self.runtime.config.settings = self.runtime.config.settings.model_copy(update={'account_id': 'other'})
        self.assertIsNone(self.service.world.prepare())

    async def test_switch_off_keeps_world_but_reverts_generation_to_single_character_rules(self):
        await self.create_world_draft()
        self.service.world.save(False)
        self.generation_fixture({'participate': True, 'text': '今天研究了一种新配色', 'media_mode': 'none'})
        await self.service.generate('publish')
        data, options = self.model_inputs[0]
        self.assertNotIn('life_world', data)
        self.assertIn('不是亲历记忆', options['system_prompt'])
        self.assertTrue(self.service.world.snapshot()['established'])

    async def test_comments_on_a_published_world_post_keep_the_cast_without_importing_other_stories(self):
        row = await self.create_world_draft()
        self.service.book.change(row['id'], 'sent', receipt='native-story-id')
        self.service.world.confirm(row['id'])
        self.generation_fixture({'participate': True, 'text': '小星刚才也笑了半天'}, global_memory=False)
        await self.service.generate('reply_own', {
            'post': {'id': 'native-story-id', 'own': True, 'author': 'account', 'text': '和小星修望远镜'},
            'comment': {'id': '1', 'author': 'friend', 'text': '小星怎么这么迷糊'}})
        data, options = self.model_inputs[0]
        self.assertEqual([c['id'] for c in data['life_world_post']['characters']], ['whalegirl', 'gemini'])
        self.assertIn('不是现实评论者', options['system_prompt'])
        self.assertNotIn('life_world', data)
        self.assertIsNone(self.service.world.post_context('some-other-post'))

    async def test_unknown_panel_character_fails_before_generation(self):
        value = answer()
        value['world_episode']['panels'][1]['cast_ids'] = ['unconfigured-person']
        self.generation_fixture(value)
        with self.assertRaisesRegex(ValueError, '分镜角色'):
            await self.service.generate('publish')
        self.assertFalse(self.jobs)
        self.assertFalse(self.service.world.snapshot()['established'])

    async def test_admin_fixed_images_remain_authorized_with_world_enabled(self):
        value = answer()
        value['media_mode'] = 'library'
        self.generation_fixture(value, moments_asset_ids=['fixed-admin-image'])
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'draft')
        self.assertEqual(json.loads(row['payload'])['assets'], ['fixed-admin-image'])
        self.assertFalse(self.jobs)


class WorldReferenceTests(unittest.IsolatedAsyncioTestCase):
    setUp = references_fixture.MomentsReferenceTests.setUp
    tearDown = references_fixture.MomentsReferenceTests.tearDown
    make_image = references_fixture.MomentsReferenceTests.make_image

    def world_scene(self, cast=('whalegirl', 'gpt', 'claude', 'gemini')):
        self.service.references.add(self.make_image('blue'), 'girl', '本人图', '大肥鱼本人外观')
        ids = {c: self.service.references.store(self.make_image(color))['id']
               for c, color in (('gpt', 'white'), ('claude', 'orange'), ('gemini', 'purple'))}
        configure(self.config, ids)
        self.runtime.moments.world = MomentsWorld(self.runtime)
        episode = answer()['world_episode']
        episode['cast_ids'] = list(cast)
        episode['panels'] = [{'cast_ids': list(cast), 'shot': '中景', 'action': '一起装饰新书架', 'dialogue': ''}]
        moment = self.book.create('account', 'publish', 'world-scene', {'world_episode': episode})
        return moment, ids

    async def test_all_cast_reference_pixels_and_labels_reach_the_actual_multipart_preparation(self):
        moment, ids = self.world_scene()
        job_id = self.service.enqueue(kind='generate', prompt='四位伙伴合作装饰书架', destination='moments', moment_id=moment)
        plan = json.loads(self.service.get(job_id)['reference_json'])
        self.assertEqual(len(plan['inputs']), 4)
        refs = await self.service.references.materialize(plan, self.store, self.runtime.gateway, 'account', '')
        parts = self.service.references.upload_parts(refs)
        self.assertEqual(len(parts), 4)
        self.assertEqual({p[1][1] for p in parts}, {self.service.references.path(v['id']).read_bytes() for v in refs})
        prompt = self.service.references.request_prompt('四位伙伴合作装饰书架', plan, refs)
        for name in ('小龙娘', '小克', '小星'):
            self.assertIn(name + '角色设定图', prompt)
        self.assertIn('第1格', prompt)
        self.assertIn('一起装饰新书架', prompt)
        self.assertNotIn('聊天图片按用户明确要求', prompt)

    async def test_only_cast_on_stage_is_attached_and_self_does_not_take_over_partner_only_scene(self):
        moment, ids = self.world_scene(('gpt', 'claude'))
        job_id = self.service.enqueue(kind='generate', prompt='小龙娘与小克整理书架', destination='moments', moment_id=moment)
        plan = json.loads(self.service.get(job_id)['reference_json'])
        self.assertFalse(plan['character'])
        self.assertEqual({v['character_id'] for v in plan['inputs']}, {'gpt', 'claude'})
        self.assertEqual(len(plan['inputs']), 2)


class WorldApiTests(unittest.TestCase):
    def test_console_api_shows_cast_and_switch_without_changing_chat_settings(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from test_proactive import Gateway
        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='world-api-', dir=moments_fixture.ROOT / 'test-runs'))
        gateway = Gateway()
        gateway.ui_lock, gateway.inflight_sends = asyncio.Lock(), set()
        app = create_app(folder, gateway=gateway, run_background=False)
        runtime, config = app.state.runtime, app.state.config
        config.save(config.settings.model_copy(update={'account_id': 'account'}), 'fixture-key')
        configure(config)
        before = config.settings.model_dump()
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + config.admin_token}
        response = client.get('/api/moments/world', headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['characters']), 4)
        response = client.post('/api/moments/world', headers=headers, json={'enabled': False, 'premise': '新的公寓'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['enabled'])
        self.assertEqual(config.settings.model_dump(), before)
        asyncio.run(runtime.close())
        runtime.store.close()


if __name__ == '__main__':
    unittest.main()

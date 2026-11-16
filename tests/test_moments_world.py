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

    async def test_slapstick_themes_enter_avoid_recent_themes(self):
        row = await self.create_world_draft()
        self.service.book.change(row['id'], 'draft')
        self.store.db.execute(
            'INSERT INTO moments_world_events(account,role_id,moment_id,episode,created) VALUES(?,?,?,?,?)',
            ('account', self.service.world.scope()[1], row['id'],
             json.dumps({'summary': '转身时尾巴一扫把沙发角的东西全扫塌了', 'location': '客厅', 'cast_ids': ['whalegirl']}, ensure_ascii=False),
             row['created'] + 10),
        )
        self.store.db.commit()
        ctx = self.service.world.prepare()
        self.assertIn('尾巴一扫', ctx['avoid_recent_themes'])
        self.assertIn('沙发角', ctx['avoid_recent_themes'])

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

    async def test_world_image_prompt_reconciles_conflicting_panel_count_and_bad_closeup(self):
        value = answer()
        value['image_prompt'] = '单幅横向构图，前景手部特写，背景轻微虚化，客厅煮红糖姜水'
        value['world_episode']['panels'] = [
            {'cast_ids': ['whalegirl'], 'shot': '中景', 'action': '端着马克杯'},
            {'cast_ids': ['gemini'], 'shot': '中景', 'action': '探头看桌面'},
            {'cast_ids': ['whalegirl', 'gemini'], 'shot': '中景', 'action': '一起捧杯喝热饮'},
        ]
        self.generation_fixture(value)
        row = await self.service.generate('publish')
        payload = json.loads(row['payload'])
        self.assertTrue(payload['image_prompt'].startswith('【3格漫画分镜】'))
        self.assertNotIn('单幅横向构图', payload['image_prompt'])
        self.assertNotIn('背景轻微虚化', payload['image_prompt'])
        self.assertEqual(self.jobs[0]['prompt'], payload['image_prompt'])

        # Cancel row so the next publish re-enters life_world cadence and verify panel-number rewrite ("四格漫画" -> "3格漫画")
        self.service.book.change(row['id'], 'cancelled')
        value2 = answer()
        value2['text'] = '晚上蹲客厅煮红糖姜水顺便帮小星校准相机焦距，忙活半天结果甜度整反了'
        value2['image_prompt'] = '四格漫画，第1格拆防尘罩，第2格擦镜片，第3格一起试镜'
        value2['world_episode']['panels'] = value['world_episode']['panels']
        self.generation_fixture(value2)
        row2 = await self.service.generate('publish')
        payload2 = json.loads(row2['payload'])
        self.assertIn('3格漫画', payload2['image_prompt'])
        self.assertNotIn('四格漫画', payload2['image_prompt'])
        self.assertIn('第1格', payload2['image_prompt'])
        self.assertIn('第2格', payload2['image_prompt'])
        self.assertNotIn('第3格漫画', payload2['image_prompt'])

    async def test_moments_generate_honors_valid_character_variant_with_safe_fallback(self):
        class StubRefs:
            def __init__(self, enabled):
                self.enabled = set(enabled)
            def selected(self, variant):
                return [{'id': variant}] if variant in self.enabled else []
            def self_prompt(self):
                return ''

        # Valid & configured variant ('chibi') is honored and recorded in payload & job
        value = answer()
        value['character_variant'] = 'chibi'
        self.generation_fixture(value)
        self.runtime.images.references = StubRefs({'girl', 'chibi'})
        row = await self.service.generate('publish')
        self.assertEqual(json.loads(row['payload'])['character_variant'], 'chibi')
        self.assertEqual(self.jobs[-1]['character_variant'], 'chibi')

        # Configured default fallback when requested variant has no uploaded refs
        value2 = answer()
        value2['text'] = '把整碗夜宵拌面全吸溜光以后才想起来本来想先拍张照片'
        value2['image_prompt'] = '厨房桌前吃干净的空碗，Q版神情懊恼'
        value2['character_variant'] = 'chibi'
        self.generation_fixture(value2, image_character_variant='girl')
        self.runtime.images.references = StubRefs({'girl'})
        row2 = await self.service.generate('publish')
        self.assertEqual(json.loads(row2['payload'])['character_variant'], 'girl')
        self.assertEqual(self.jobs[-1]['character_variant'], 'girl')


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

    async def test_world_image_story_includes_panel_layout_tail_ownership_and_finger_safety(self):
        moment, ids = self.world_scene(('whalegirl', 'gpt', 'gemini'))
        ep_row = self.store.db.execute('SELECT payload FROM moments_actions WHERE id=?', (moment,)).fetchone()
        payload = json.loads(ep_row['payload'])
        payload['world_episode']['panels'] = [
            {'cast_ids': ['whalegirl'], 'shot': '中景', 'action': '穿蓝白居家卫衣捧着玻璃杯', 'dialogue': ''},
            {'cast_ids': ['gpt', 'gemini'], 'shot': '中景', 'action': '趴在沙发看小鲸鱼', 'dialogue': '真慢'},
            {'cast_ids': ['whalegirl', 'gemini'], 'shot': '中景', 'action': '一起捧碗喝姜汤', 'dialogue': ''},
        ]
        self.store.db.execute('UPDATE moments_actions SET payload=? WHERE id=?',
                              (json.dumps(payload, ensure_ascii=False), moment))
        self.store.db.commit()
        job_id = self.service.enqueue(kind='generate', prompt='【3格漫画分镜】客厅喝热饮', destination='moments', moment_id=moment)
        plan = json.loads(self.service.get(job_id)['reference_json'])
        refs = await self.service.references.materialize(plan, self.store, self.runtime.gateway, 'account', '')
        prompt = self.service.references.request_prompt('【3格漫画分镜】客厅喝热饮', plan, refs)
        self.assertIn('共3格漫画分镜', prompt)
        self.assertIn('大肥鱼的蓝色鲸尾必须从她自己腰后', prompt)
        self.assertIn('五指自然清晰比例', prompt)
        self.assertIn('气泡尖角精确指向说话角色', prompt)
        self.assertIn('不要把三视图复制成三个分身', prompt)

    async def test_comic_example_attaches_only_for_matching_comic_cast_and_isolates_prompt(self):
        import base64
        import io
        from unittest.mock import patch
        import httpx
        from PIL import Image

        example = self.service.references.add(
            self.make_image('yellow'),
            'comic_example',
            'Q版三格漫画排版示例',
            '仅示范三格竖排黑线分格与白底椭圆对白气泡指向',
        )
        moment, _ = self.world_scene(('whalegirl', 'claude'))
        ep_row = self.store.db.execute('SELECT payload FROM moments_actions WHERE id=?', (moment,)).fetchone()
        payload = json.loads(ep_row['payload'])
        payload['world_episode']['panels'] = [
            {'cast_ids': ['whalegirl', 'claude'], 'shot': '中景', 'action': '卧床前指着桌边蓝掌机确认是否存档', 'dialogue': '大肥鱼：等下，存档了吗？'},
            {'cast_ids': ['claude'], 'shot': '近景', 'action': '伸手点下绿色确认完成存档', 'dialogue': '小克：存好了。'},
        ]
        self.store.db.execute(
            'UPDATE moments_actions SET payload=? WHERE id=?',
            (json.dumps(payload, ensure_ascii=False), moment),
        )
        self.store.db.commit()

        prompt_text = '【2格漫画分镜】睡前检查游戏存档'
        job_id = self.service.enqueue(kind='generate', prompt=prompt_text, destination='moments', moment_id=moment)
        plan = json.loads(self.service.get(job_id)['reference_json'])
        self.assertEqual([v['id'] for v in plan['inputs'] if v.get('source') == 'comic_example'], [example['id']])

        bound = self.service.bind_cast(
            {'destination': 'moments', 'world_episode': payload['world_episode']},
            ['whalegirl', 'claude'],
            variant='girl',
            prompt=prompt_text,
        )
        self.assertEqual([v['id'] for v in bound['inputs'] if v.get('source') == 'comic_example'], [example['id']])

        uploaded_body = {}
        out = io.BytesIO()
        Image.new('RGB', (1024, 1024), 'navy').save(out, format='PNG')

        async def handler(request):
            if request.url.path.endswith('/models'):
                return httpx.Response(200, json={'data': [{'id': self.config.settings.image_model}]})
            uploaded_body['raw'] = request.content
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(out.getvalue()).decode()}]})

        original = httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await self.service.generate_once()

        raw_text = uploaded_body['raw'].decode('utf-8', errors='ignore')
        self.assertIn('【漫画分镜示例隔离铁律】', raw_text)
        idx_char = raw_text.find('图1（本人图）')
        idx_claude = raw_text.find('图2（小克角色设定图）')
        idx_example = raw_text.find('图3（Q版三格漫画排版示例）')
        self.assertGreater(idx_char, -1)
        self.assertGreater(idx_claude, idx_char)
        self.assertGreater(idx_example, idx_claude)

        episode2 = answer()['world_episode']
        episode2['cast_ids'] = ['gpt', 'gemini']
        episode2['panels'] = [
            {'cast_ids': ['gpt', 'gemini'], 'shot': '中景', 'action': '厨房看菜单', 'dialogue': '小龙娘：今晚吃面。'},
            {'cast_ids': ['gemini'], 'shot': '近景', 'action': '记下步骤', 'dialogue': '小星：收到。'},
        ]
        moment2 = self.book.create('account', 'publish', 'world-scene-non-matching', {'world_episode': episode2})
        job2 = self.service.enqueue(kind='generate', prompt='【2格漫画分镜】厨房讨论夜宵', destination='moments', moment_id=moment2)
        plan2 = json.loads(self.service.get(job2)['reference_json'])
        self.assertFalse(any(v.get('source') == 'comic_example' for v in plan2['inputs']))


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

    def test_comic_example_upload_edit_and_world_snapshot_exposure(self):
        import base64
        import io
        from PIL import Image
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from test_proactive import Gateway

        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='world-example-api-', dir=moments_fixture.ROOT / 'test-runs'))
        gateway = Gateway()
        gateway.ui_lock, gateway.inflight_sends = asyncio.Lock(), set()
        app = create_app(folder, gateway=gateway, run_background=False)
        runtime, config = app.state.runtime, app.state.config
        config.save(config.settings.model_copy(update={'account_id': 'account'}), 'fixture-key')
        configure(config)
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + config.admin_token}

        buf = io.BytesIO()
        Image.new('RGB', (120, 180), 'orange').save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')

        up = client.post('/api/images/references/upload', headers=headers, json={
            'data': b64,
            'name': '三格竖排漫画示例',
            'variant': 'comic_example',
            'role': '示范黑线分格与白底椭圆气泡',
        })
        self.assertEqual(up.status_code, 200, up.text)
        identity = up.json()['id']

        ed = client.post('/api/images/references/' + identity, headers=headers, json={
            'variant': 'comic_example',
            'name': '更新后漫画示例',
            'role': '仅学分镜与气泡指向',
            'enabled': True,
        })
        self.assertEqual(ed.status_code, 200, ed.text)
        self.assertIn('comic_example', ed.json()['examples'])

        imgs = client.get('/api/images', headers=headers)
        self.assertEqual(imgs.status_code, 200, imgs.text)
        refs_public = imgs.json()['references']
        self.assertIn('comic_example', refs_public['examples'])
        self.assertTrue(any(v['id'] == identity and v['name'] == '更新后漫画示例' for v in refs_public['items']))

        world_resp = client.get('/api/moments/world', headers=headers)
        self.assertEqual(world_resp.status_code, 200, world_resp.text)
        self.assertEqual([v['id'] for v in world_resp.json()['comic_examples']], [identity])

        preview = client.get('/api/images/references/' + identity + '/preview', headers=headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.headers['content-type'], 'image/jpeg')

        asyncio.run(runtime.close())
        runtime.store.close()
class CaptionAndIsolationRegressionTests(unittest.IsolatedAsyncioTestCase):
    setUp = WorldGenerationTests.setUp
    tearDown = WorldGenerationTests.tearDown
    generation_fixture = WorldGenerationTests.generation_fixture

    async def test_publish_prompts_enforce_de_ai_caption_rules_with_and_without_world(self):
        self.generation_fixture(answer())
        await self.service.generate('publish')
        _, world_options = self.model_inputs[0]
        world_prompt = world_options['system_prompt']
        for phrase in ('第一人称', '去AI味铁律', 'AI写段子三件套', '行，我信了', '不能文案换了'):
            self.assertIn(phrase, world_prompt)
        self.assertNotIn('18～55', world_prompt)
        self.assertNotIn('点明今天跟谁发生了什么小事', world_prompt)

        self.service.world.save(False)
        self.generation_fixture({'participate': True, 'text': '风好大吹得睁不开眼', 'media_mode': 'none'})
        await self.service.generate('publish')
        _, solo_options = self.model_inputs[0]
        solo_prompt = solo_options['system_prompt']
        for phrase in ('不是亲历记忆', 'AI写段子三件套', '起承转合+金句反转', '不能文案换了'):
            self.assertIn(phrase, solo_prompt)
        self.assertNotIn('18～55', solo_prompt)

    def test_multi_cast_request_prompt_forbids_cross_character_trait_bleed(self):
        from whalegirl_linux.image_references import ReferenceLibrary

        plan = {
            'variant': 'girl',
            'cast_ids': ['whalegirl', 'gemini'],
            'world_episode': {
                'summary': '天台看云',
                'location': '天台',
                'cast_ids': ['whalegirl', 'gemini'],
                'panels': [
                    {'cast_ids': ['whalegirl'], 'shot': '中景', 'action': '趴在栏杆边打哈欠', 'dialogue': ''},
                    {'cast_ids': ['gemini'], 'shot': '中景', 'action': '指着云彩喊好看', 'dialogue': '你看那边'},
                ],
            },
        }
        prompt = ReferenceLibrary(self.runtime.config).request_prompt('两格漫画，天台风大', plan, [])
        self.assertIn('【出场名单】大肥鱼、小星', prompt)
        self.assertIn('不互相串用异色瞳、发饰、兽耳、龙角或鲸尾等特征', prompt)


if __name__ == '__main__':
    unittest.main()

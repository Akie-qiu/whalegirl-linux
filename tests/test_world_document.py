"""Editable source must reach every world consumer without per-chat compiling."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

import test_image_tools as fixture
from test_moments_world import answer, configure
from whalegirl_linux.app import create_app
from whalegirl_linux.moments_world import CAST, CAST_IDS, effective_cast
from whalegirl_linux.world_document import revision, validate_canon


def canon():
    cards = [deepcopy(CAST[c]) for c in CAST_IDS]
    for card in cards:
        card.setdefault('details', '喜欢米饭与朋友，开心时鲸尾轻拍地面，不希望只被当作吉祥物')
        if card['id'] == 'claude':
            card['personality'] = '冷娇、优雅嘴硬的大小姐，规矩多但心软，明确暗恋大肥鱼'
        if card['id'] == 'whalegirl':
            card['personality'] = '灵巧直率的小鲸鱼，明确暗恋小克，认真时很可靠'
    relations = answer()['world_design']['relationships']
    for relation in relations:
        if set((relation['a'], relation['b'])) == {'claude', 'whalegirl'}:
            relation.update(dynamic='互相喜欢、尚未告白；嘴硬和直球间慢慢靠近，尊重彼此自由', stage='双向暗恋')
    return {'title': 'Claude与DeepSeek双向暗恋V3', 'premise': '云端城公寓，四位成年室友各有工作与生活',
        'characters': cards, 'relationships': relations, 'relationship_summary': '小克和大肥鱼双向暗恋，尚未交往；其他人默认友情',
        'rules': '保留独立目标，不强迫告白；旧快捷卡中的友情描述由V3主线替代', 'story_hooks': ['夜雨归来的两杯茶', '四人修理公寓门铃']}


class Compiler:
    calls = []
    result = None

    def __init__(self, **options):
        self.usage_callback = None

    async def chat(self, messages, **options):
        self.calls.append((messages, options))
        return json.dumps(self.result if self.result is not None else canon(), ensure_ascii=False)

    async def close(self):
        pass


class WorldDocumentTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ImageToolTests.asyncTearDown

    async def asyncSetUp(self):
        (fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        await fixture.ImageToolTests.asyncSetUp(self)
        identity = self.images.references.store(self.raw)['id']
        configure(self.config, {c: identity for c in ('gpt', 'claude', 'gemini')})
        self.world = self.runtime.moments.world
        self.runtime.engine_factory = Compiler
        Compiler.calls, Compiler.result = [], None
        self.source = '新版V3\nClaude与DeepSeek双向暗恋，尚未正式交往\n旧快捷卡仅写友情\n【原文系统模板】你是多角色叙事引擎'

    async def test_source_preserved_and_compiled_into_chat_images_and_existing_world_without_losing_history(self):
        original = self.world.configuration()
        self.world.establish(answer(), self.world.prepare())
        with self.store.db:
            self.store.db.execute('INSERT INTO moments_world_events VALUES(?,?,?,?,?)',
                ('old-scene', 'account', 'default', json.dumps({'summary': '之前一起看星星', 'location': '天台', 'cast_ids': ['whalegirl', 'gemini']}), 1))
        await self.world.import_document(self.source, '')
        self.assertEqual(len(Compiler.calls), 1)
        self.assertEqual(Compiler.calls[0][0][0]['content'], self.source)
        self.assertNotIn(self.source, Compiler.calls[0][1]['system_prompt'])
        self.assertEqual(self.world.document()['text'], self.source)
        self.assertEqual(self.world.document()['revision'], revision(self.source))
        self.assertEqual(self.world.configuration()['characters'], original['characters'])
        self.assertTrue(list((self.path / 'moments-world').rglob('history/*.json')))
        context = self.world.prepare()
        self.assertNotIn(self.source, json.dumps(context, ensure_ascii=False))
        self.assertIn('大小姐', next(c for c in context['characters'] if c['id'] == 'claude')['personality'])
        self.assertEqual(len(context['recent_events_newest_first']), 1)
        self.assertIn('双向暗恋', next(r for r in context['design']['relationships'] if {r['a'], r['b']} == {'claude', 'whalegirl'})['stage'])
        static = self.images.tools.static_prompt(include_catalog=False)
        self.assertIn('双向暗恋', static)
        self.assertIn('大小姐', static)
        job_id = self.images.enqueue(kind='generate', prompt='小克和大肥鱼在雨中共撑一把伞',
            chat='private', destination='chat', cast_ids=['whalegirl', 'claude'])
        plan = json.loads(self.images.get(job_id)['reference_json'])
        refs = await self.images.references.materialize(plan, self.store, self.gateway, 'account', 'private')
        prompt = self.images.references.request_prompt('两人在雨中撑伞', plan, refs)
        self.assertIn('大小姐', prompt)
        self.assertIn('双向暗恋', prompt)
        self.assertNotIn(self.source, prompt)
        await self.world.import_document(self.source, revision(self.source))
        self.assertEqual(len(Compiler.calls), 1)
        self.assertEqual(self.images.tools.static_prompt(include_catalog=False), static)

    async def test_bad_compile_and_stale_edit_leave_current_settings_intact(self):
        old = self.world.configuration()
        Compiler.result = {'characters': []}
        with self.assertRaises(ValueError):
            await self.world.import_document(self.source, '')
        self.assertEqual(self.world.configuration(), old)
        self.assertFalse(self.world.document()['text'])
        Compiler.result = None
        await self.world.import_document(self.source, '')
        with self.assertRaisesRegex(ValueError, '其他页面更新'):
            await self.world.import_document(self.source + '\n另一个版本', '')
        self.assertEqual(self.world.document()['text'], self.source)
        self.assertEqual(len(Compiler.calls), 2)


class WorldDocumentApiTests(unittest.TestCase):
    def test_editor_read_save_and_regular_snapshot_use_the_same_applied_revision(self):
        from test_proactive import Gateway
        (fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='world-document-api-', dir=fixture.ROOT / 'test-runs'))
        gateway = Gateway()
        gateway.ui_lock, gateway.inflight_sends = asyncio.Lock(), set()
        app = create_app(folder, gateway=gateway, run_background=False)
        runtime = app.state.runtime
        app.state.config.save(app.state.config.settings.model_copy(update={'account_id': 'account'}), 'fixture-key')
        configure(app.state.config)
        runtime.engine_factory = Compiler
        Compiler.calls, Compiler.result = [], None
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + app.state.config.admin_token}
        before = app.state.config.settings.model_dump()
        response = client.post('/api/moments/world', headers=headers,
            json={'enabled': True, 'setting_text': '新版V3设定原文完整保存', 'expected_setting_revision': ''})
        self.assertEqual(response.status_code, 200, response.text)
        world = response.json()
        self.assertEqual(world['document']['characters'], len('新版V3设定原文完整保存'))
        self.assertNotIn('setting_text', world)
        self.assertIn('大小姐', next(c for c in world['characters'] if c['id'] == 'claude')['personality'])
        source = client.get('/api/moments/world/document', headers=headers).json()
        self.assertEqual(source['text'], '新版V3设定原文完整保存')
        self.assertEqual(source['revision'], world['document']['revision'])
        self.assertEqual(app.state.config.settings.model_dump(), before)
        response = client.post('/api/moments/world', headers=headers,
            json={'enabled': True, 'setting_text': '另一个版本', 'expected_setting_revision': ''})
        self.assertEqual(response.status_code, 409)
        asyncio.run(runtime.close())
        runtime.store.close()


if __name__ == '__main__':
    unittest.main()

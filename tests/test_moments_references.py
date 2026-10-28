"""Moments generation with character references attachment."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image
from whalegirl_linux.images import ImageService
from whalegirl_linux.moments import MomentsBook, MomentsService
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class MomentsReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT/'test-runs').mkdir(exist_ok=True)
        self.path = Path(tempfile.mkdtemp(prefix='moments-ref-', dir=ROOT/'test-runs'))
        self.config = Config(self.path)
        self.config.save(Settings(
            account_id='account', enabled=True, allowed_chat_ids=['allowed'],
            image_per_day=10, image_generation_enabled=True,
            image_references_enabled=True, image_character_variant='girl',
            moments_publish_per_day=10, moments_image_generate_enabled=True,
            moments_image_generate_eagerness=75), 'test-key')
        self.config.save_image_key('test-image-key')
        self.store = Store(self.path/'state.sqlite3')
        native = self.path/'native'; native.mkdir()
        self.book = MomentsBook(self.store)
        self.runtime = SimpleNamespace(
            store=self.store, config=self.config,
            gateway=SimpleNamespace(ui_jobs_directory=native),
            moments=SimpleNamespace(book=self.book, lock=asyncio.Lock()))
        self.service = ImageService(self.runtime)
        self._color_counter = 0

    def make_image(self, color='blue'):
        """Create a unique image with given color to avoid hash collision."""
        self._color_counter += 1
        out = io.BytesIO()
        # Make each image unique by varying size
        Image.new('RGB', (80 + self._color_counter, 60), color).save(out, format='PNG')
        return out.getvalue()

    def tearDown(self):
        self.store.close()

    def test_detect_variant_extracts_from_prompt_and_falls_back_when_missing(self):
        """detect_variant should recognize Q版/少女版/偏年长版 and fallback to available variants."""
        # Add only girl variant references
        self.service.references.add(self.make_image('blue'), 'girl', '少女图1', '参考')
        self.service.references.add(self.make_image('red'), 'girl', '少女图2', '参考')

        # Default should return 'girl' when available
        self.assertEqual(self.service.references.detect_variant('普通提示词'), 'girl')

        # Explicit request for 'chibi' should fall back to 'girl' when chibi is missing
        self.assertEqual(self.service.references.detect_variant('画一个Q版的'), 'girl')

        # Add chibi references
        self.service.references.add(self.make_image('green'), 'chibi', 'Q版图', '参考')
        self.assertEqual(self.service.references.detect_variant('画一个二头身Q版'), 'chibi')
        self.assertEqual(self.service.references.detect_variant('来个三头身的'), 'chibi')

        # Add mature references
        self.service.references.add(self.make_image('yellow'), 'mature', '年长图', '参考')
        self.assertEqual(self.service.references.detect_variant('偏年长版'), 'mature')
        self.assertEqual(self.service.references.detect_variant('年长版的样子'), 'mature')

        # Explicit 'girl' request should work
        self.assertEqual(self.service.references.detect_variant('少女版'), 'girl')

    def test_moments_generation_auto_attaches_character_references(self):
        """Moments generation jobs should automatically get character=True when references are configured."""
        # Setup: add girl variant references
        self.service.references.add(self.make_image('blue'), 'girl', '少女图1', '外观参考')
        self.service.references.add(self.make_image('red'), 'girl', '少女图2', '外观参考')

        # Enqueue a moments generation task
        identity = self.service.enqueue(
            kind='generate',
            prompt='窗边看雨的少女',
            destination='moments',
            moment_id='test-moment'
        )

        # Check the task's reference_json
        job = self.service.get(identity)
        plan = json.loads(job['reference_json'])

        self.assertTrue(plan['character'], 'Moments generation should auto-enable character references')
        self.assertEqual(plan['variant'], 'girl')
        self.assertEqual(len(plan['inputs']), 2, 'Should include 2 girl references')
        self.assertTrue(all(v['source'] == 'character' for v in plan['inputs']))

    def test_moments_generation_requires_character_references(self):
        with self.assertRaisesRegex(ValueError,'角色参考图'):
            self.service.enqueue(kind='generate',prompt='一张风景图',destination='moments',moment_id='test-moment')

    def test_chat_generation_does_not_auto_attach_character(self):
        """Chat generation should not auto-attach character references (requires explicit request)."""
        self.service.references.add(self.make_image(), 'girl', '少女图', '参考')

        identity = self.service.enqueue(
            kind='generate',
            prompt='画一张图',
            destination='chat',
            chat='allowed'
        )

        job = self.service.get(identity)
        plan = json.loads(job['reference_json'])

        self.assertFalse(plan.get('character', False), 'Chat should not auto-enable character')

    def test_chat_character_generation_uses_default_variant_and_keeps_one_job(self):
        self.service.references.add(self.make_image(), 'girl', '少女图', '角色参考')
        request = dict(kind='generate', prompt='画一张你自己的少女版',
                       destination='chat', chat='allowed', character=True,
                       source_key='account/allowed/1')
        identity = self.service.enqueue(**request)
        plan = json.loads(self.service.get(identity)['reference_json'])
        self.assertTrue(plan['character'])
        self.assertEqual(plan['variant'], 'girl')
        self.assertEqual(len(plan['inputs']), 1)
        self.assertEqual(plan['inputs'][0]['source'], 'character')
        self.assertEqual(self.service.enqueue(**request), identity)
        self.assertEqual(self.service.generation_count(), 1)

    def test_variant_detection_in_moments_enqueue(self):
        """Moments enqueue should detect variant from prompt and use the right references."""
        # Add all three variants
        self.service.references.add(self.make_image('cyan'), 'chibi', 'Q版图', '参考')
        self.service.references.add(self.make_image('magenta'), 'girl', '少女图', '参考')
        self.service.references.add(self.make_image('yellow'), 'mature', '年长图', '参考')

        # Test chibi detection
        identity_chibi = self.service.enqueue(
            kind='generate',
            prompt='画一个可爱的Q版形象',
            destination='moments',
            moment_id='m1'
        )
        plan_chibi = json.loads(self.service.get(identity_chibi)['reference_json'])
        self.assertEqual(plan_chibi['variant'], 'chibi')

        # Test mature detection
        identity_mature = self.service.enqueue(
            kind='generate',
            prompt='偏年长版的优雅姿态',
            destination='moments',
            moment_id='m2'
        )
        plan_mature = json.loads(self.service.get(identity_mature)['reference_json'])
        self.assertEqual(plan_mature['variant'], 'mature')

        # Test default girl
        identity_girl = self.service.enqueue(
            kind='generate',
            prompt='随便画一张图',
            destination='moments',
            moment_id='m3'
        )
        plan_girl = json.loads(self.service.get(identity_girl)['reference_json'])
        self.assertEqual(plan_girl['variant'], 'girl', 'Should default to configured variant')

    def test_explicit_character_variant_overrides_detection(self):
        """Explicit character_variant parameter should override auto-detection."""
        self.service.references.add(self.make_image('cyan'), 'chibi', 'Q版图', '参考')
        self.service.references.add(self.make_image('magenta'), 'girl', '少女图', '参考')

        # Prompt says Q版 but we explicitly pass 'girl'
        identity = self.service.enqueue(
            kind='generate',
            prompt='画一个Q版的',
            destination='moments',
            moment_id='test',
            character_variant='girl'
        )

        plan = json.loads(self.service.get(identity)['reference_json'])
        self.assertEqual(plan['variant'], 'girl', 'Explicit variant should override detection')

    def test_references_disabled_prevents_character_attachment(self):
        """When image_references_enabled=False, moments should not attach character references."""
        self.service.references.add(self.make_image(), 'girl', '少女图', '参考')
        self.config.save(self.config.settings.model_copy(update={'image_references_enabled': False}))

        with self.assertRaisesRegex(ValueError,'角色参考图'):
            self.service.enqueue(kind='generate',prompt='窗边看雨',destination='moments',moment_id='test')


if __name__ == '__main__':
    unittest.main()

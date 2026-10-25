"""Retention tests only remove generated fixtures beneath test-runs/."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from whalegirl_linux.images import ImageService
from whalegirl_linux.moments import MomentsBook
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix='retention-', dir=ROOT/'test-runs'))
        self.config = Config(self.path)
        self.config.save(Settings(account_id='fixture', enabled=True, allowed_chat_ids=['chat']), 'fixture-key')
        self.store = Store(self.path/'state.sqlite3')
        self.native = self.path/'native'
        self.native.mkdir()
        self.runtime = SimpleNamespace(config=self.config, store=self.store,
            gateway=SimpleNamespace(ui_jobs_directory=self.native),
            moments=SimpleNamespace(book=MomentsBook(self.store), lock=asyncio.Lock()))
        self.images = ImageService(self.runtime)
        out = io.BytesIO()
        Image.new('RGB', (24, 24), 'blue').save(out, format='PNG')
        self.raw = out.getvalue()
        self.now = time.time()

    def tearDown(self):
        self.store.close()

    def asset(self, source='uploaded', **kw):
        return self.images.save_asset(self.raw, source=source, scope='global', **kw)

    def age(self, asset, days=30):
        self.store.db.execute('UPDATE picture_assets SET created=? WHERE id=?',
                              (self.now-days*86400, asset['id']))
        self.store.db.commit()

    def configure(self, **values):
        self.config.save(self.config.settings.model_copy(update=values))

    def test_default_retains_all_sources_and_reference_library(self):
        for source in ('uploaded', 'generated', 'tieba'):
            self.age(self.asset(source))
        reference = self.images.references.add(self.raw, 'girl', '角色参考')
        result = self.images.retention.sweep(self.now+400*86400)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(len(self.images.assets()), 3)
        self.assertTrue(self.images.references.path(reference['id']).exists())

    def test_manual_removes_both_files_hides_asset_and_updates_authority(self):
        asset = self.asset()
        reference = self.images.references.add(self.raw, 'girl')
        # A separate archived original is deliberately outside the deletion plan.
        archive = self.path/'tieba-archives'/'original.png'
        archive.parent.mkdir()
        archive.write_bytes(self.raw)
        plan = self.images.retention.plan(asset['id'])
        self.assertEqual(len(plan['files']), 2)
        result = self.images.retention.delete(asset['id'])
        self.assertEqual(result['state'], 'deleted')
        self.assertEqual(result['bytes_removed'], plan['bytes'])
        self.assertFalse(self.images.path(asset).exists())
        self.assertFalse((self.native/'moments-assets'/(asset['id']+'.png')).exists())
        self.assertIsNone(self.images.asset(asset['id']))
        self.assertFalse(self.images.assets())
        self.assertFalse(self.images.candidates())
        authority = json.loads((self.native/'image-assets.json').read_text())
        self.assertNotIn(asset['id'], authority['assets'])
        self.assertTrue(archive.exists())
        self.assertTrue(self.images.references.path(reference['id']).exists())
        with self.assertRaises(ValueError):
            self.images.stage(asset['id'])

    def test_expiration_is_per_source_and_keep_flag_is_persisted(self):
        uploaded, generated, tieba = self.asset(), self.asset('generated'), self.asset('tieba')
        for asset in (uploaded, generated, tieba):
            self.age(asset)
        self.images.edit_asset(generated['id'], {'retention_exempt': True})
        self.configure(image_retention_uploaded_days=30, image_retention_generated_days=1,
                       image_retention_tieba_days=31)
        result = self.images.retention.sweep(self.now)
        self.assertEqual(result['deleted'], 1)  # Exact cutoff included.
        self.assertIsNone(self.images.asset(uploaded['id']))
        self.assertTrue(self.images.asset(generated['id'])['retention_exempt'])
        self.assertIsNotNone(self.images.asset(tieba['id']))
        # Permanent retention excludes expiry, but the administrator can still delete.
        self.images.retention.delete(generated['id'])
        self.assertIsNone(self.images.asset(generated['id']))

    def test_inflight_uncertain_tagging_fixed_assets_and_drafts_are_protected(self):
        asset = self.asset()
        job = self.images.enqueue(kind='send', asset_id=asset['id'])
        for state in ('queued', 'ready', 'sending', 'uncertain'):
            self.images.change(job, state=state)
            with self.assertRaisesRegex(ValueError, '任务'):
                self.images.retention.delete(asset['id'])
        self.images.change(job, state='cancelled')
        self.store.db.execute("UPDATE picture_assets SET state='tagging' WHERE id=?", (asset['id'],))
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, 'AI整理'):
            self.images.retention.delete(asset['id'])
        self.images.edit_asset(asset['id'], {})
        self.configure(moments_asset_ids=[asset['id']])
        with self.assertRaisesRegex(ValueError, '固定配图'):
            self.images.retention.delete(asset['id'])
        self.configure(moments_asset_ids=[])
        moment = self.runtime.moments.book.create('fixture', 'publish', 'fixture-post', {'assets': [asset['id']]})
        self.runtime.moments.book.change(moment, 'draft')
        with self.assertRaisesRegex(ValueError, '草稿'):
            self.images.retention.delete(asset['id'])
        self.runtime.moments.book.change(moment, 'cancelled')
        self.images.retention.delete(asset['id'])

    def test_tombstone_blocks_legacy_import_and_tieba_recollection(self):
        asset = self.asset('tieba')
        self.images.retention.delete(asset['id'])
        duplicate = self.asset('tieba')
        self.assertGreater(duplicate['deleted_at'], 0)
        self.assertFalse(self.images.path(asset).exists())
        legacy = self.path/'moments-assets'
        legacy.mkdir()
        (legacy/(asset['id']+'.json')).write_text(json.dumps({'width': 24, 'height': 24}))
        (self.native/'moments-assets'/(asset['id']+'.png')).write_bytes(self.raw)
        self.images.import_legacy()
        self.assertIsNone(self.images.asset(asset['id']))
        self.assertFalse((self.images.root/'uploaded'/(asset['id']+'.png')).exists())
        self.assertEqual(self.images.retention.snapshot()['recent'][0]['reason'], 'admin')

    def test_failed_unlink_retries_and_explicit_reupload_is_never_erased(self):
        asset = self.asset()
        original = Path.unlink
        def denied(path, *args, **kw):
            if path == self.images.path(asset):
                raise PermissionError('fixture')
            return original(path, *args, **kw)
        with patch.object(Path, 'unlink', denied):
            result = self.images.retention.delete(asset['id'])
        self.assertEqual(result['state'], 'pending')
        self.images.retention.sweep(self.now)
        self.assertFalse(self.images.path(asset).exists())
        restored = self.asset()
        self.assertFalse(restored['deleted_at'])
        with patch.object(Path, 'unlink', denied):
            self.images.retention.delete(restored['id'])
        old_audit = self.store.db.execute('SELECT max(id) FROM picture_deletions').fetchone()[0]
        restored = self.asset()
        self.images.retention.finish(old_audit, restored)
        self.images.retention.sweep(self.now)
        self.assertTrue(self.images.path(restored).exists())
        self.assertTrue(self.images.asset(restored['id'])['enabled'])

    def test_account_and_path_boundaries(self):
        asset = self.asset()
        self.configure(account_id='other')
        with self.assertRaises(ValueError):
            self.images.retention.delete(asset['id'])
        self.configure(image_retention_uploaded_days=1)
        self.assertEqual(self.images.retention.sweep(self.now+90*86400)['deleted'], 0)
        self.assertTrue(self.images.path(asset).exists())
        with self.assertRaises(ValueError):
            self.images.retention.files({**asset, 'source': '../references'})
        with self.assertRaises(ValueError):
            self.images.retention.files({**asset, 'id': '../../outside'})

    def test_api_requires_admin_and_exact_confirmation_and_hides_deleted_preview(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        app = create_app(self.path/'api', gateway=self.runtime.gateway, run_background=False)
        service = app.state.runtime.images
        config = app.state.config
        config.save(config.settings.model_copy(update={'account_id': 'fixture'}))
        asset = service.save_asset(self.raw)
        client = TestClient(app)
        headers = {'Authorization': 'Bearer '+config.admin_token}
        route = '/api/images/assets/'+asset['id']
        try:
            self.assertEqual(client.post(route+'/delete', json={'confirm_id': asset['id']}).status_code, 401)
            self.assertEqual(client.get(route+'/deletion', headers=headers).status_code, 200)
            self.assertEqual(client.post(route+'/delete', headers=headers, json={'confirm_id': '0'*64}).status_code, 400)
            response = client.post(route+'/delete', headers=headers, json={'confirm_id': asset['id']})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['state'], 'deleted')
            self.assertEqual(client.get(route+'/preview', headers=headers).status_code, 404)
            self.assertEqual(client.get('/api/images', headers=headers).json()['assets'], [])
        finally:
            client.close()
            app.state.runtime.store.close()


if __name__ == '__main__':
    unittest.main()

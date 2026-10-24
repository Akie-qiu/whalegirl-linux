"""Original source preservation, image byte fidelity and archive recovery."""
import hashlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from PIL import Image

from whalegirl_linux.app import create_app
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.tieba import TiebaReader, screening_excerpt
from whalegirl_linux.tieba_archive import TiebaArchive
from test_chat_habits import Gateway
from test_tieba import directory, Engine, ROOT


TEXT = '  原文开头\n\n不是摘要\t@某人 [滑稽]  '+('保留这段正文与换行\n' * 350)+'原文结尾  '


def source(images=0):
    pictures = [{'id': f'{i:024x}', 'pid': 11, 'url': 'https://tiebapic.baidu.com/forum/pic/item/'+str(i)+'.gif',
                 'post_url': 'https://tieba.baidu.com/p/123?pid=11', 'original_available': True} for i in range(images)]
    return {'tid': '123', 'forum': '绝区零内鬼', 'title': '完整图文'+('长标题' * 100),
            'url': 'https://tieba.baidu.com/p/123', 'text': TEXT, 'source_version': 2,
            'posts': [{'pid': '11', 'floor': 1, 'author': '公开作者', 'text': TEXT, 'url': 'https://tieba.baidu.com/p/123?pid=11',
                       'contents': [{'type': 'FragText', 'text': TEXT}], 'comments': []}],
            'images': pictures, 'coverage': {'main_floor_present': True, 'posts_read': 1, 'has_more': True}}


def gif():
    out = io.BytesIO()
    a, b = Image.new('RGB', (12, 12), 'red'), Image.new('RGB', (12, 12), 'blue')
    a.save(out, format='GIF', save_all=True, append_images=[b], duration=100, loop=0)
    return out.getvalue()


class OriginalArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = directory()
        self.config = Config(self.root)
        self.config.save(Settings(account_id='account', tieba_enabled=True, tieba_share_enabled=False,
                                  tieba_images_enabled=False, tieba_images_per_day=0), 'test-key')
        self.store = Store(self.root / 'state.sqlite3')
        self.r = Runtime(self.config, self.store, Gateway(), ROOT/'styles', engine_factory=lambda **kw: Engine())
        self.a = self.r.tieba.archives

    async def asyncTearDown(self):
        self.store.close()

    async def test_reader_preserves_full_text_all_images_and_content_order(self):
        image_parts = [SimpleNamespace(origin_src='https://tiebapic.baidu.com/a'+str(i)+'.jpg', big_src='', src='', text='') for i in range(12)]
        post = SimpleNamespace(tid=123, pid=11, floor=1, text=TEXT, contents=[SimpleNamespace(text=TEXT), *image_parts],
                               comments=[], user=SimpleNamespace(user_name='作者'), author_id=42, sign='', reply_num=0)
        class Rows(list):
            err = False
            thread = SimpleNamespace(title='长标题' * 100)
            page = SimpleNamespace(total_page=3, has_more=True)
        reader = TiebaReader()
        reader.client = SimpleNamespace(get_posts=AsyncMock(return_value=Rows([post])))
        result = await reader.detail(source())
        self.assertEqual(result['text'], TEXT)
        self.assertEqual(result['posts'][0]['text'], TEXT)
        self.assertEqual(len(result['images']), 12)
        self.assertEqual(result['posts'][0]['contents'][0]['text'], TEXT)
        self.assertEqual(result['title'], '长标题' * 100)
        self.assertTrue(result['coverage']['has_more'])
        excerpt = screening_excerpt(result)
        self.assertLessEqual(len(excerpt['text']), 1400)
        self.assertLessEqual(len(excerpt['posts'][0]['text']), 1100)
        self.assertEqual(len(excerpt['images']), 8)
        self.assertEqual(result['text'], TEXT)

    async def test_all_selected_originals_saved_without_material_quota_or_reencoding(self):
        raw, original = gif(), source(9)
        self.a.download = AsyncMock(return_value=(raw, 'image/gif'))
        identity = self.a.add(original, '感兴趣的完整图文')
        self.assertTrue(await self.a.process(identity))
        self.assertEqual(self.a.download.await_count, 9)
        document = self.a.document(identity)
        self.assertEqual(document['state'], 'complete')
        self.assertEqual(document['source'], original)
        self.assertIn(TEXT, document['original_text'])
        self.assertEqual(len(document['images']), 9)
        for image in document['images']:
            path, mime = self.a.image_file(identity, image['image_id'])
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(image['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(mime, 'image/gif')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM picture_assets').fetchone()[0], 0)
        self.assertEqual(self.a.add(original, '重复收藏'), identity)
        self.assertFalse(await self.a.process(identity))
        self.assertEqual(self.a.download.await_count, 9)

    async def test_partial_download_recovers_without_fetching_saved_images_again(self):
        identity = self.a.add(source(2), '图文')
        self.a.download = AsyncMock(side_effect=[(gif(), 'image/gif'), ValueError('临时下载失败')])
        await self.a.process(identity)
        self.assertEqual(self.a.document(identity)['state'], 'partial')
        restored = TiebaArchive(self.r.tieba)
        restored.download = AsyncMock(return_value=(gif(), 'image/gif'))
        restored.retry(identity)
        await restored.resume_once()
        self.assertEqual(restored.document(identity)['state'], 'complete')
        self.assertEqual(restored.download.await_count, 1)

    async def test_legacy_truncated_preview_is_refetched_instead_of_claimed_as_original(self):
        preview = {**source(), 'text': '旧截断摘要', 'posts': []}
        preview.pop('source_version')
        reader = SimpleNamespace(detail=AsyncMock(return_value=source()))
        class Manager:
            async def __aenter__(self): return reader
            async def __aexit__(self, *args): pass
        self.r.tieba.reader_factory = lambda credentials: Manager()
        identity = self.a.add(preview, '手动收藏旧帖')
        self.assertEqual(self.a.document(identity)['original_text'], '')
        await self.a.process(identity)
        self.assertEqual(reader.detail.await_count, 1)
        self.assertEqual(self.a.document(identity)['source']['text'], TEXT)
        self.assertEqual(self.a.document(identity)['state'], 'complete')

    async def test_interesting_post_archived_independently_of_sharing_and_image_switch(self):
        class Reader:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def threads(self, forum): return [source()]
            async def detail(self, thread): return thread
        self.r.tieba.reader_factory = lambda credentials: Reader()
        await self.r.tieba.scan()
        self.assertEqual(self.a.stats()['count'], 1)
        await self.a.resume_once()
        self.assertEqual(self.a.summaries()['items'][0]['state'], 'complete')
        self.assertEqual(self.r.tieba.snapshot()['shares'], [])

    async def test_full_archive_capacity_does_not_report_success_or_remove_existing_data(self):
        identity = self.a.add(source(1), '图文')
        self.a.download = AsyncMock(side_effect=ValueError('原文收藏空间达到上限'))
        await self.a.process(identity)
        document = self.a.document(identity)
        self.assertEqual(document['state'], 'partial')
        self.assertIn(TEXT, document['original_text'])
        self.assertEqual(document['images'][0]['state'], 'failed')
        self.assertTrue((self.a.directory(self.a.row(identity))/'source.json').is_file())


class ArchiveApiTests(unittest.TestCase):
    def test_original_exports_image_bytes_and_account_access(self):
        app = create_app(directory(), run_background=False)
        with TestClient(app) as client:
            config, runtime = app.state.config, app.state.runtime
            config.settings.account_id = 'account'
            archive = runtime.tieba.archives
            identity = archive.add(source(1), '原文')
            archive.persist_source(archive.row(identity), source(1))
            raw, image_id = gif(), f'{0:024x}'
            (archive.directory(archive.row(identity))/(image_id+'.bin')).write_bytes(raw)
            archive.execute("UPDATE tieba_archive_images SET state='saved',mime='image/gif',bytes=? WHERE archive_id=?", (len(raw), identity))
            headers = {'Authorization': 'Bearer '+config.admin_token}
            base = '/api/tieba/archives/'+identity
            self.assertEqual(client.get(base).status_code, 401)
            text = client.get(base+'/export?kind=text', headers=headers)
            self.assertEqual(text.status_code, 200)
            self.assertIn(TEXT, text.text)
            exported = client.get(base+'/export?kind=json', headers=headers)
            self.assertEqual(exported.json()['source']['text'], TEXT)
            response = client.get(base+'/images/'+image_id, headers=headers)
            self.assertEqual(response.content, raw)
            self.assertEqual(response.headers['content-type'], 'image/gif')
            self.assertEqual(client.get('/api/tieba/archives', headers=headers).json()['total'], 1)
            config.settings.account_id = 'other'
            self.assertEqual(client.get(base, headers=headers).status_code, 404)
            self.assertEqual(client.get(base+'/images/'+image_id, headers=headers).status_code, 404)
            self.assertEqual(client.get('/api/tieba/archives', headers=headers).json()['total'], 0)


if __name__ == '__main__':
    unittest.main()

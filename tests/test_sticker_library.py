"""Native identity, reversible retirement and asynchronous collection regressions."""
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from PIL import Image
from whalegirl_linux.app import create_app
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Settings
from whalegirl_linux.sticker_library import StickerLibrary
from whalegirl_linux.stickers import StickerCatalog
from whalegirl_linux.store import Store

ROOT=Path(__file__).resolve().parents[1]


class StickerLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='sticker-library-',dir=folder))
        self.store=Store(self.folder/'state.sqlite3');self.catalog=StickerCatalog(self.folder)
        self.catalog.available=[f'{i:032x}' for i in range(1,13)]
        self.catalog.entries={m:'开心' for m in self.catalog.available}
        self.library=StickerLibrary(self.store,self.catalog)
        self.settings=Settings(enabled=True,account_id='account',allowed_chat_ids=['group@chatroom'])

    def tearDown(self):self.store.close()

    def sticker(self,mid=1,md5='f'*32,kind=47):
        event=Runtime.event('account','group@chatroom',dict(serverId=mid,localId=mid,sender='alice',senderName='甲',
            type=kind,isSelf=False,content='',timestamp=time.time(),sticker={'md5':md5}))
        self.store.observe([event]);return event

    async def test_received_native_sticker_keeps_identity_and_candidates_deduplicate(self):
        event=self.sticker();self.assertEqual(json.loads(event['payload_json'])['sticker']['md5'],'f'*32)
        image=self.sticker(2,kind=3)
        self.library.offer('account',[event,event,image]);self.library.offer('account',[event])
        rows=self.library.inventory('account',self.settings.allowed_chat_ids)['candidates']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source_key'],event['key'])

    async def test_retirement_keeps_native_favorites_and_can_be_restored_after_restart(self):
        md5=self.catalog.available[0];before=list(self.catalog.available)
        value=self.library.change('account',md5,'disable','容易误用')
        self.assertNotIn(md5,self.catalog.choices());self.assertEqual(self.catalog.available,before)
        restarted=StickerLibrary(self.store,self.catalog);restarted.sync('account')
        self.assertNotIn(md5,self.catalog.choices())
        revision=self.library.inventory('account',[])['revisions'][0]
        self.library.undo('account',revision['id']);self.assertIn(md5,self.catalog.choices())
        self.assertEqual(self.catalog.available,before)

    async def test_protected_stickers_and_daily_retirement_limit_are_enforced(self):
        md5=self.catalog.available[0];self.library.change('account',md5,'pin')
        with self.assertRaises(ValueError):self.library.change('account',md5,'disable',actor='model',settings=self.settings)
        self.settings.sticker_disable_per_day=1
        self.library.change('account',self.catalog.available[1],'disable',actor='model',settings=self.settings)
        with self.assertRaises(ValueError):self.library.change('account',self.catalog.available[2],'disable',actor='model',settings=self.settings)

    async def test_model_collection_requires_real_current_native_source(self):
        event=self.sticker();image=self.sticker(2,kind=3)
        first={'account':'account','chat':'group@chatroom'}
        results=self.library.model_ops(first,[{'key':image['key']}],[dict(op='collect',source_key=image['key'])],{},self.settings)
        self.assertFalse(results[0]['ok'])
        results=self.library.model_ops(first,[{'key':event['key']}],[dict(op='collect',source_key=event['key'])],{},self.settings)
        self.assertTrue(results[0]['ok'])
        self.assertEqual(self.library.inventory('account',self.settings.allowed_chat_ids)['candidates'][0]['state'],'selected')

    async def test_background_collection_tags_once_and_confirms_native_readback(self):
        event=self.sticker();self.library.offer('account',[event])
        stream=BytesIO();Image.new('RGB',(32,32),'red').save(stream,'PNG');data=base64.b64encode(stream.getvalue()).decode()
        class Gateway:
            def __init__(self,existing):self.favorites=list(existing);self.collections=0
            async def media(self,*args,**kwargs):return {'data':data}
            async def collect_sticker(self,chat,local_id,server_id,md5):
                self.collections+=1;self.favorites.insert(0,md5);return {'state':'collected'}
            async def native_request(self,route):return {'stickers':[{'md5':m} for m in self.favorites]}
        class Engine:
            calls=0
            async def chat(self,*args,**kwargs):
                self.calls+=1;return json.dumps({'keep':True,'reason':'补充尴尬情绪','profile':{'meaning':'尴尬挠头','emotion':'尴尬'}})
        gateway=Gateway(self.catalog.available);engine=Engine()
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.assertEqual((gateway.collections,engine.calls),(1,1))
        self.assertIn('f'*32,self.catalog.choices());self.assertEqual(self.catalog.entries['f'*32],'尴尬挠头')
        self.assertEqual(self.library.used_today('account','collect'),1)

    async def test_disabled_source_is_not_automatically_readded(self):
        event=self.sticker(md5=self.catalog.available[0])
        self.library.change('account',self.catalog.available[0],'disable')
        self.library.offer('account',[event]);self.assertFalse(self.library.inventory('account',[])['candidates'])


class LibraryApiTests(unittest.TestCase):
    def test_admin_auth_retire_restore_and_frontend(self):
        folder=ROOT/'test-runs';folder.mkdir(exist_ok=True)
        app=create_app(Path(tempfile.mkdtemp(prefix='sticker-api-',dir=folder)),run_background=False)
        runtime=app.state.runtime;runtime.stickers.available=['a'*32];runtime.stickers.entries={'a'*32:'微笑'}
        headers={'Authorization':'Bearer '+app.state.config.admin_token}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/sticker-library').status_code,401)
            original=client.get('/api/sticker-library',headers=headers).json();self.assertEqual(original['active'],1)
            response=client.post('/api/sticker-library/manage',headers=headers,json={'id':'a'*32,'op':'disable','version':0})
            self.assertEqual(response.status_code,200);self.assertEqual(runtime.stickers.available,['a'*32])
            response=client.post('/api/sticker-library/manage',headers=headers,json={'id':'a'*32,'op':'enable','version':1})
            self.assertEqual(response.status_code,200);self.assertEqual(client.get('/sticker-library.js').status_code,200)

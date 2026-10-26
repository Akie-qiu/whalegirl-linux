"""Meaningful source, cache lifecycle, quota and scoped-management checks."""
import base64
import hashlib
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
from whalegirl_linux.stickers import StickerCatalog, parse_json
from whalegirl_linux.store import Store


class StickerStudioTests(unittest.IsolatedAsyncioTestCase):
    def test_model_json_parser_recovers_object_from_short_preamble_and_trailer(self):
        self.assertEqual(parse_json('Here is the response: {"ok":true,"items":[]} done'),
                         {'ok':True,'items':[]})
        with self.assertRaises(json.JSONDecodeError):
            parse_json('{"broken":true,"nested":{"salvage":1} trailing')

    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'test-runs'
        root.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='sticker-studio-',dir=root))
        self.store=Store(self.folder/'state.sqlite3')
        self.catalog=StickerCatalog(self.folder)
        self.catalog.available=[f'{i:032x}' for i in range(1,13)]
        self.catalog.entries={m:'开心' for m in self.catalog.available}
        self.library=StickerLibrary(self.store,self.catalog)
        self.settings=Settings(enabled=True,account_id='account',allowed_chat_ids=['one@chatroom','two@chatroom'])
        self.settings.sticker_chat_permissions={'one@chatroom':__import__('whalegirl_linux.settings',fromlist=['StickerChatPermission']).StickerChatPermission(auto_collect=False,command_collect=False,command_disable=False)}

    def tearDown(self):
        self.store.close()

    def event(self,mid=1,chat='two@chatroom',color='red'):
        stream=BytesIO();Image.new('RGB',(32,32),color).save(stream,'PNG')
        raw=stream.getvalue();md5=hashlib.md5(raw).hexdigest()
        row=Runtime.event('account',chat,dict(serverId=mid,localId=mid,sender='alice',senderName='A',type=47,isSelf=False,content='',timestamp=time.time(),sticker={'md5':md5}))
        self.store.observe([row])
        return row,md5,base64.b64encode(raw).decode()

    def dependencies(self,data,keep=True):
        class Gateway:
            collections=0
            reads=0
            favorites=list(self.catalog.available)
            async def media(self,*args,**kwargs):self.reads+=1;return {'data':data}
            async def collect_sticker(self,chat,local_id,server_id,md5):
                self.collections+=1;self.favorites.append(md5);return {'state':'collected'}
            async def native_request(self,route):return {'stickers':[{'md5':m} for m in self.favorites]}
        class Engine:
            calls=0
            async def chat(self,*args,**kwargs):
                self.calls+=1
                return json.dumps({'keep':keep,'profile':{'meaning':'开心挥手','use_when':'打招呼'}})
        return Gateway(),Engine()

    async def test_source_permission_blocks_both_discovery_and_model_commands(self):
        row,md5,_=self.event(chat='one@chatroom')
        self.library.offer('account',[row],self.settings)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sticker_candidates').fetchone()[0],0)
        result=self.library.model_ops(row,[row],[{'op':'collect','source_key':row['key']}],{},self.settings)
        self.assertFalse(result[0]['ok'])

    async def test_forbidden_old_candidates_do_not_starve_authorized_new_ones(self):
        first,_,_=self.event(chat='one@chatroom',color='blue')
        self.library.offer('account',[first])
        row,md5,data=self.event(mid=2)
        self.library.offer('account',[row],self.settings)
        gateway,engine=self.dependencies(data)
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.assertEqual(gateway.collections,1)
        self.assertIn(md5,self.catalog.choices())

    async def test_recognition_proceeds_with_full_daily_quota_without_blocking_chat(self):
        self.settings.sticker_collect_per_day=0
        row,md5,data=self.event()
        self.library.offer('account',[row],self.settings)
        gateway,engine=self.dependencies(data)
        await self.library.work('account',gateway,engine,self.settings,lambda:False)
        await self.library.work('account',gateway,engine,self.settings,lambda:False)
        self.assertEqual((engine.calls,gateway.collections),(1,0))
        self.assertTrue(self.library.assets.preview_data(md5))
        self.assertFalse((self.library.assets.work/(md5+'.media')).exists())
        item=self.library.inventory('account',self.settings.allowed_chat_ids,self.settings)['candidates'][0]
        self.assertIn('额度',item['wait_reason'])
        self.settings.sticker_collect_per_day=6
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.assertEqual((engine.calls,gateway.collections),(1,1))

    async def test_chat_disable_is_isolated_reversible_and_protected(self):
        md5=self.catalog.available[0]
        first={'account':'account','chat':'two@chatroom'}
        result=self.library.model_ops(first,[],[{'op':'disable','id':md5}],{},self.settings)
        self.assertTrue(result[0]['ok'])
        self.assertIn(md5,self.library.excluded('account','two@chatroom'))
        self.assertNotIn(md5,self.library.excluded('account','one@chatroom'))
        self.assertIn(md5,self.catalog.choices())
        result=self.library.model_ops(first,[],[{'op':'enable','id':md5}],{},self.settings)
        self.assertTrue(result[0]['ok'])
        self.library.change('account',md5,'pin')
        result=self.library.model_ops(first,[],[{'op':'disable','id':md5}],{},self.settings)
        self.assertFalse(result[0]['ok'])

    async def test_admin_profile_lock_conflict_and_revision_restore(self):
        md5=self.catalog.available[0]
        profile=self.library.edit_profile('account',md5,{'meaning':'管理员定义','locked':True,'version':0})
        self.catalog.edit(md5,{'meaning':'自动识别覆盖'},actor='vision')
        self.assertEqual(self.catalog.entries[md5],'管理员定义')
        with self.assertRaises(ValueError):
            self.library.edit_profile('account',md5,{'meaning':'陈旧网页提交','version':0})
        self.library.edit_profile('account',md5,{'meaning':'第二次修改','version':profile['version']})
        revision=self.library.inventory('account',[])['revisions'][0]
        self.library.undo('account',revision['id'])
        self.assertEqual(self.catalog.entries[md5],'管理员定义')

    async def test_skipped_interpretation_reused_and_no_native_collection(self):
        row,md5,data=self.event()
        self.library.offer('account',[row],self.settings)
        gateway,engine=self.dependencies(data,keep=False)
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.library.offer('account',[row],self.settings)
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.assertEqual((engine.calls,gateway.collections),(1,0))
        self.assertEqual(self.catalog.profiles[md5]['meaning'],'开心挥手')

    async def test_cache_cleanup_preserves_other_files_and_permanent_preview(self):
        _,md5,data=self.event()
        self.library.assets.remember(md5,data)
        other=self.library.assets.work/'keep.txt'
        other.write_text('keep')
        self.library.assets.discard(md5)
        self.assertTrue(other.exists())
        self.assertTrue(self.library.assets.preview_data(md5))
        with self.assertRaises(ValueError):self.library.assets.discard('../not-owned')

    async def test_revoked_source_is_rechecked_after_model_generation(self):
        row,md5,data=self.event()
        self.library.offer('account',[row],self.settings)
        gateway,engine=self.dependencies(data)
        original=engine.chat
        async def chat(*args,**kwargs):
            result=await original(*args,**kwargs)
            self.settings.sticker_chat_permissions['two@chatroom']=self.settings.sticker_chat_permissions['one@chatroom']
            return result
        engine.chat=chat
        await self.library.work('account',gateway,engine,self.settings,lambda:True)
        self.assertEqual(gateway.collections,0)


class StickerStudioApiTests(unittest.TestCase):
    def test_authenticated_endpoints_and_scope_validation(self):
        root=Path(__file__).resolve().parents[1]/'test-runs';root.mkdir(exist_ok=True)
        app=create_app(Path(tempfile.mkdtemp(prefix='sticker-studio-api-',dir=root)),run_background=False)
        runtime=app.state.runtime
        runtime.stickers.available=['a'*32];runtime.stickers.entries={'a'*32:'笑'}
        headers={'Authorization':'Bearer '+app.state.config.admin_token}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/sticker-library/preview/'+'a'*32).status_code,401)
            self.assertEqual(client.get('/sticker-library.css').status_code,200)
            self.assertEqual(client.post('/api/sticker-library/manage',headers=headers,json={'op':'disable','id':'a'*32,'chat':'unapproved'}).status_code,409)
            self.assertEqual(client.post('/api/stickers/'+'a'*32,headers=headers,json={'meaning':'手改','locked':True,'version':0}).status_code,200)
            response=client.post('/api/sticker-library/batch',headers=headers,json={'op':'retry','ids':['../outside']})
            self.assertFalse(response.json()['results'][0]['ok'])


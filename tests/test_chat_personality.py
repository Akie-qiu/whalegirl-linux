"""Group callback boundaries, expiry, config persistence and one-call replies."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from test_chat_habits import Engine, Gateway

ROOT=Path(__file__).resolve().parents[1]


def directory():
    runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='personality-',dir=runs))


class GroupJokeTests(unittest.TestCase):
    def setUp(self):
        self.root=directory();self.store=Store(self.root/'state.sqlite3')
        self.book=MemoryBook(self.store);self.allowed=['a@chatroom','b@chatroom','alice']

    def tearDown(self):self.store.close()

    def joke(self,chat='a@chatroom',**extra):
        return self.book.change('account',self.allowed,{'chat':chat,'subject':'alice','kind':'joke',
            'scope':'chat','content':'摸鱼是物种优势；双方互相调侃工作时可以接，认真疲惫时不提','source_key':'',**extra})

    def test_callbacks_stay_in_original_account_and_group(self):
        joke=self.joke()
        rows=self.book.recall_jokes('account',self.allowed,'a@chatroom','又在摸鱼','normal')
        self.assertEqual([r['id'] for r in rows],[joke['id']])
        for account,chat in [('other','a@chatroom'),('account','b@chatroom'),('account','alice')]:
            self.assertEqual(self.book.recall_jokes(account,self.allowed,chat,'又在摸鱼','high'),[])
        self.assertEqual(self.book.recall_jokes('account',self.allowed,'a@chatroom','又在摸鱼','off'),[])
        self.assertEqual(self.book.recall_jokes('account',self.allowed,'a@chatroom','晚饭吃面条','high'),[])
        with self.assertRaises(ValueError):self.joke(scope='profile')
        with self.assertRaises(ValueError):self.joke(chat='alice')

    def test_expiry_stops_recall_and_model_get_without_erasing_admin_records(self):
        joke=self.joke(ttl_days=1,source_key='account/a@chatroom/expired-source')
        self.assertAlmostEqual(joke['expires_at']-time.time(),86400,delta=3)
        self.store.db.execute('UPDATE memories SET expires_at=? WHERE id=?',(time.time()-1,joke['id']))
        self.store.db.commit()
        self.assertEqual(self.book.recall_jokes('account',self.allowed,'a@chatroom','摸鱼','high'),[])
        self.assertEqual(self.book.recall('account',self.allowed,'alice','摸鱼',chat='a@chatroom'),[])
        result=self.book.model_ops('account',self.allowed,[{'op':'get','id':joke['id']}],[],True,
            chat='a@chatroom',subjects=['alice'])
        self.assertIsNone(result[0]['result'])
        self.assertEqual(self.book.search('account',self.allowed)[0]['id'],joke['id'])
        self.assertTrue(self.book.revisions('account',self.allowed))
        self.assertIn(joke['source_key'],self.book.suppressed_sources('account',self.allowed))

    def test_existing_memory_schema_and_revisions_survive_additive_migration(self):
        store=Store(self.root/'legacy.sqlite3')
        try:
            store.db.executescript('''CREATE TABLE memories(id INTEGER PRIMARY KEY,account TEXT NOT NULL,
                chat TEXT NOT NULL,subject TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,
                source_key TEXT NOT NULL,actor TEXT NOT NULL,version INTEGER NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL);
                INSERT INTO memories VALUES(1,'account','a@chatroom','alice','fact','现有个人记录','','admin',1,0,1,1);''')
            book=MemoryBook(store);row=book.get('account',1,self.allowed)
            self.assertEqual((row['content'],row['scope'],row['expires_at']),('现有个人记录','chat',0))
            self.assertEqual(book.recall('account',self.allowed,'alice','个人记录',chat='a@chatroom')[0]['id'],1)
        finally:store.close()

    def test_content_edits_preserve_expiry_and_undo_restores_renewed_deadline(self):
        joke=self.joke();deadline=joke['expires_at']
        edited=self.book.change('account',self.allowed,{'op':'update','id':joke['id'],
            'version':joke['version'],'content':'摸鱼梗只适合双方在开玩笑时'})
        self.assertEqual(edited['expires_at'],deadline)
        renewed=self.book.change('account',self.allowed,{'op':'update','id':joke['id'],
            'version':edited['version'],'ttl_days':60})
        self.assertGreater(renewed['expires_at'],deadline)
        revision=self.book.revisions('account',self.allowed)[0]
        self.assertEqual(self.book.undo('account',self.allowed,revision['id'])['expires_at'],deadline)
        self.assertEqual(len(list((self.root/'memory-archive'/'chats').glob('*/memories.json'))),1)

    def test_model_can_save_real_group_joke_with_reply_and_cannot_import_private_source(self):
        source=Runtime.event('account','a@chatroom',{'serverId':1,'localId':1,'sender':'alice',
            'senderName':'Alice','type':1,'content':'摸鱼是物种优势 哈哈哈','isSelf':False,'timestamp':time.time()})
        self.store.observe([source])
        data={'chat':source['chat'],'subject':'alice','kind':'joke','scope':'chat',
            'content':'摸鱼是物种优势，这是群里互相调侃的梗','source_key':source['key']}
        row=self.book.change('account',self.allowed,data,actor='model',allowed_sources=[source['key']])
        self.assertGreater(row['expires_at'],time.time()+29*86400)
        with self.assertRaises(ValueError):
            self.book.change('account',self.allowed,{**data,'chat':'b@chatroom'},actor='model',allowed_sources=[source['key']])
        with self.assertRaises(ValueError):self.joke(ttl_days=91)


class PersonalityConfigTests(unittest.TestCase):
    def test_new_controls_and_joke_deadline_roundtrip_without_overwriting_existing_choices(self):
        root=directory();app=create_app(root,gateway=Gateway(),engine_factory=Engine,run_background=False)
        c=app.state.config;c.save(Settings(account_id='account',allowed_chat_ids=['a@chatroom'],
            image_character_variant='chibi',trigger_only_chat_ids=['a@chatroom'],system_prompt_override='当前管理员角色'))
        with TestClient(app) as client:
            h={'Authorization':'Bearer '+c.admin_token}
            values={'banter_level':'high','play_along':'high','callback_frequency':'low','assertiveness':'normal'}
            response=client.post('/api/config',headers=h,json={'settings':values})
            self.assertEqual(response.status_code,200,response.text)
            saved=client.get('/api/config',headers=h).json()
            self.assertEqual({k:saved[k] for k in values},values)
            self.assertEqual((saved['image_character_variant'],saved['system_prompt_override']),('chibi','当前管理员角色'))
            self.assertEqual(saved['trigger_only_chat_ids'],['a@chatroom'])
            data={'chat':'a@chatroom','subject':'alice','kind':'joke','scope':'chat','content':'摸鱼是物种优势','ttl_days':7}
            response=client.post('/api/memory',headers=h,json=data)
            self.assertEqual(response.status_code,200,response.text)
            self.assertAlmostEqual(response.json()['expires_at']-time.time(),7*86400,delta=3)
            self.assertEqual(client.post('/api/memory',headers=h,json={**data,'ttl_days':91}).status_code,422)
            self.assertEqual(Config(root).settings.callback_frequency,'low')


class PersonalityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinary_callback_reply_uses_one_request_and_keeps_other_group_out(self):
        root=directory();config=Config(root)
        config.save(Settings(enabled=True,account_id='account',allowed_chat_ids=['a@chatroom','b@chatroom'],
            debounce_single_seconds=0,debounce_burst_seconds=0,debounce_max_seconds=0),'test-key')
        store=Store(root/'state.sqlite3');gateway=Gateway();runtime=Runtime(config,store,gateway,ROOT/'styles',engine_factory=Engine)
        try:
            for chat,content in [('a@chatroom','摸鱼是物种优势，群里调侃时可以回调'),('b@chatroom','摸鱼秘密竞赛属于另一个群')]:
                runtime.memory_book.change('account',config.settings.allowed_chat_ids,{'chat':chat,'subject':'alice',
                    'kind':'joke','scope':'chat','content':content,'source_key':''})
            event=Runtime.event('account','a@chatroom',{'serverId':11,'localId':11,'sender':'alice',
                'senderName':'Alice','type':1,'content':'又在摸鱼是吧','isSelf':False,'timestamp':time.time()})
            event['trigger']='direct';store.observe([event]);store.insert(event)
            engine=await runtime.model();captured=[]
            async def chat(messages,**kwargs):
                engine.calls+=1;captured.append(kwargs['system_prompt'])
                return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'物种优势 这回真不能怪我'}]},ensure_ascii=False)
            engine.chat=chat
            await runtime.process_next()
            self.assertEqual(engine.calls,1)
            self.assertIn('摸鱼是物种优势，群里调侃时可以回调',captured[0])
            self.assertNotIn('摸鱼秘密竞赛',captured[0])
            self.assertIn('多轮节奏示例',captured[0])
            metrics=json.loads(store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(event['key'],)).fetchone()[0])
            self.assertEqual(len(metrics['long_term_memory']['joke_ids']),1)
            self.assertEqual(gateway.sent,[('text','物种优势 这回真不能怪我')])
        finally:await runtime.close()


if __name__=='__main__':unittest.main()

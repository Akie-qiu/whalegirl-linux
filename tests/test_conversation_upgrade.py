"""Focused regressions for observed omissions, quoted media and scope boundaries."""
import ast
import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
from PIL import Image
from whalegirl_linux.store import Store
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.context import build_context
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.topic_relevance import is_followup, related_topic, select_topic_history
from whalegirl_linux.visible_media import locate_image, token
from whalegirl_linux.media_locator import locate_image as native_locate_image

ROOT=Path(__file__).resolve().parents[1]


class UpgradeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='conversation-v2-',dir=ROOT/'test-runs'))
        self.store=Store(self.root/'state.sqlite3');self.book=MemoryBook(self.store)

    def tearDown(self):self.store.close()

    def message(self,id,chat='a@chatroom',person='alice',kind=1,content='你好',**extra):
        raw={'serverId':id,'localId':id,'sender':person,'senderName':person,'type':kind,
             'content':content,'isSelf':False,'timestamp':time.time()-100+id,**extra}
        e=Runtime.event('account',chat,raw);self.store.observe([e]);return e

    def remember(self,source,scope,content,kind='fact'):
        return self.book.change('account',['a@chatroom','b@chatroom'],{'chat':source['chat'],'subject':source['sender_id'],
            'kind':kind,'scope':scope,'content':content,'source_key':source['key']},actor='model',allowed_sources=[source['key']])

    async def test_shared_profiles_do_not_share_other_group_events_or_other_people(self):
        a=self.message(1,content='我喜欢猫');b=self.message(2,'b@chatroom',content='我喜欢猫')
        profile=self.remember(b,'profile','喜欢猫','preference')
        event=self.remember(b,'chat','昨天本群举办比赛','event')
        stranger=self.message(3,'b@chatroom',person='bob',content='我喜欢跑步');self.remember(stranger,'profile','喜欢跑步','preference')
        rows=self.book.recall('account',['a@chatroom','b@chatroom'],'alice','比赛 跑步 猫',chat=a['chat'],subjects=['alice'])
        self.assertEqual([r['id'] for r in rows],[profile['id']])
        result=self.book.model_ops('account',['a@chatroom','b@chatroom'],[{'op':'get','id':event['id']}],[],True,chat=a['chat'],subjects=['alice'])
        self.assertIsNone(result[0]['result'])
        self.assertTrue(list((self.root/'memory-archive'/'people').glob('*/memories.json')))

    async def test_temporary_event_cannot_be_saved_as_shared_personal_fact(self):
        e=self.message(1,content='我今天在群里输了游戏')
        with self.assertRaises(ValueError):self.remember(e,'profile','今天输游戏')

    async def test_inbox_includes_baseline_ignored_and_archive_with_stable_pagination(self):
        for i in range(1,43):
            e=self.message(i);self.store.insert(e,'ignored' if i%2 else 'baseline')
        first=self.store.received('account',['a@chatroom'],limit=35)
        second=self.store.received('account',['a@chatroom'],limit=35,**first['next'])
        self.assertEqual((len(first['messages']),len(second['messages'])),(35,7))
        self.assertFalse({m['key'] for m in first['messages']}&{m['key'] for m in second['messages']})

    async def test_inbox_deduplicates_provisional_and_final_message_ids(self):
        old=self.message(1);self.store.insert(old,'sent')
        stable={**old,'key':old['key'].rsplit('/',1)[0]+'/local:1','content':'旧占位'}
        self.store.observe([stable])
        rows=self.store.received('account',['a@chatroom'],limit=10)['messages']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['state'],'sent')
        self.assertEqual(rows[0]['key'],old['key'])

    def image(self,row):
        stream=BytesIO();Image.new('RGB',(64,64),'red').save(stream,'PNG')
        self.store.cache_image(row['key'],{'data':base64.b64encode(stream.getvalue()).decode(),'format':'png','quality':'native_cache'})

    async def test_quoted_image_uses_referenced_message_not_latest_image(self):
        first=self.message(1,kind=3);self.image(first)
        latest=self.message(2,person='bob',kind=3);self.image(latest)
        quoted=self.message(3,kind=49,content='看看这个',reply={'type':3,'serverId':'1','senderId':'alice','content':'[图片]'})
        _,keys,_=await build_context(self.store,None,Settings(),quoted,[quoted],{})
        self.assertEqual(keys,[first['key']])

    async def test_unavailable_quoted_image_does_not_pick_somebody_elses_image(self):
        photo=self.message(1,person='bob',kind=3);self.image(photo)
        quoted=self.message(2,kind=49,content='看看这个',reply={'type':3,'serverId':'999','content':'[图片]'})
        messages,keys,_=await build_context(self.store,None,Settings(),quoted,[quoted],{})
        self.assertFalse(keys);self.assertIn('原图尚未同步',messages[0]['content'])

    async def test_queued_image_survives_busy_group_context_window(self):
        photo=self.message(1,kind=3);self.image(photo)
        for i in range(2,18):self.message(i)
        _,keys,_=await build_context(self.store,None,Settings(context_messages=10),photo,[photo],{})
        self.assertEqual(keys,[photo['key']])

    async def test_native_sticker_is_not_loaded_as_an_image_unless_asked_about(self):
        sticker=self.message(80,kind=47,content='',sticker={'md5':'a'*32})
        sticker['content']='[原生表情包] 表达：无语、疑惑'
        self.store.observe([sticker]);self.store.insert(sticker);self.image(sticker)
        _,keys,_=await build_context(self.store,None,Settings(),sticker,[self.store.get(sticker['key'])],{})
        self.assertEqual(keys,[])
        current=self.message(81,kind=1,content='这个表情包是什么意思')
        current['trigger']='direct';self.store.insert(current)
        _,keys,_=await build_context(self.store,None,Settings(),current,[self.store.get(current['key'])],{})
        self.assertEqual(keys,[sticker['key']])

    async def test_complete_recent_history_keeps_other_topic_without_injecting_old_digest(self):
        old=self.message(1,content='昨天贴吧猫猫梗又被顶上来了')
        self.store.save_digest('account',old['chat'],'贴吧最近在聊猫猫梗，之前的帖子还挺有趣',old)
        current=self.message(2,content='鲸鱼娘帮我看看服务器磁盘空间')
        current['trigger']='direct'
        self.store.insert(current)
        messages,_,counts=await build_context(
            self.store,None,Settings(vision_enabled=False),current,[self.store.get(current['key'])],{}
        )
        prompt=messages[0]['content']
        self.assertIn('服务器磁盘空间',prompt)
        self.assertNotIn('贴吧最近在聊猫猫梗',prompt)
        self.assertIn('昨天贴吧猫猫梗',prompt)
        self.assertFalse(counts['digest_included'])
        self.assertEqual(counts['selected_history_messages'],1)
        self.assertEqual(counts['context_mode'],'complete_recent')

    async def test_related_current_topic_keeps_matching_history_and_digest(self):
        old=self.message(1,content='缓存命中率昨天掉到一半了')
        bot={**old,'key':'account/a@chatroom/2','local_id':2,'sender':'bot','sender_id':'bot',
             'senderName':'机器人','is_self':True,'content':'我记得缓存命中率下降了，先看看磁盘读写'}
        self.store.observe([bot])
        self.store.save_digest('account',old['chat'],'缓存命中率下降，正在排查磁盘读写',old)
        current=self.message(3,content='那缓存命中呢')
        current['trigger']='direct'
        self.store.insert(current)
        messages,_,counts=await build_context(
            self.store,None,Settings(vision_enabled=False),current,[self.store.get(current['key'])],{}
        )
        prompt=messages[0]['content']
        self.assertIn('缓存命中率昨天掉到一半了',prompt)
        self.assertIn('缓存命中率下降',prompt)
        self.assertIn('正在排查磁盘读写',prompt)
        self.assertGreaterEqual(counts['selected_history_messages'],1)
        self.assertTrue(counts['digest_included'])

    async def test_context_history_obeys_configured_message_limit(self):
        for i in range(1,116):
            self.message(i,content=f'当前讨论话题第{i}句')
        current=self.message(116,content='鲸鱼娘继续说一下')
        current['trigger']='direct';self.store.insert(current)
        _,_,counts=await build_context(self.store,None,Settings(context_messages=100,vision_enabled=False),
                                       current,[self.store.get(current['key'])],{})
        self.assertEqual(counts['current_messages'],100)

    def test_saved_context_limit_is_clamped_to_supported_range(self):
        folder=self.root/'legacy-settings';folder.mkdir()
        data=Settings().model_dump();data['context_messages']=128
        (folder/'settings.json').write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(Config(folder).settings.context_messages,100)
        self.assertEqual(Settings(context_messages=28).context_messages,28)
        with self.assertRaises(ValueError):
            Settings(context_messages=101)

    def test_topic_helpers_require_content_overlap_or_an_explicit_followup(self):
        self.assertTrue(related_topic('服务器磁盘空间', '服务器磁盘占用上涨'))
        self.assertFalse(related_topic('服务器磁盘空间', '贴吧猫猫梗又出现了'))
        self.assertTrue(is_followup('那缓存呢'))
        self.assertFalse(is_followup('今天问一下服务器'))
        now=time.time()
        rows=[{'key':'old','timestamp':now-30,'local_id':1,'content':'贴吧猫猫梗','is_self':0},
              {'key':'recent','timestamp':now-5,'local_id':2,'content':'服务器磁盘读写','is_self':0}]
        fresh={'key':'new','content':'鲸鱼娘 服务器磁盘占用','timestamp':now,'trigger':'direct'}
        chosen,mode=select_topic_history(rows,[fresh],'direct',now=now,wake_words=['鲸鱼娘'])
        self.assertEqual([row['key'] for row in chosen],['recent'])
        self.assertEqual(mode,'topic_match')

    async def test_group_image_alignment_supports_quoted_bubbles_and_unique_sequences(self):
        raw=[{'localId':1,'serverId':11,'type':1,'content':'看看'}, {'localId':2,'serverId':12,'type':3},
             {'localId':3,'serverId':13,'type':47}]
        nodes=[{'name':"看看\nQuote 某人's message: 以前的话"},{'name':'Image\n'},{'name':'Animated Stickers\n'}]
        self.assertEqual(token(nodes[0]['name']),'看看')
        self.assertIs(locate_image(raw,nodes,2,3,'12'),nodes[1])
        self.assertIs(native_locate_image(raw,nodes,2,3,'12'),nodes[1])
        self.assertIsNone(locate_image(raw,nodes,2,3,'wrong-id'))

    async def test_native_cursor_combines_database_shards(self):
        import hashlib,re
        from datetime import datetime,timezone
        source=ast.parse((ROOT/'deploy/native_message_reader.py').read_text('utf-8'))
        funcs=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('messages','message_databases')]
        data={'message_0.db':[{'local_id':1,'server_id':101,'local_type':1,'create_time':100,'body':'','compressed':0,'source':'','source_compressed':0,'sender':'alice'}],
              'message_1.db':[{'local_id':2,'server_id':102,'local_type':1,'create_time':101,'body':'','compressed':0,'source':'','source_compressed':0,'sender':'alice'}]}
        queries=[]
        def query(name,sql):
            queries.append(sql)
            if 'sqlite_master' in sql:return [{'name':'exists'}]
            return data[name]
        env={'hashlib':hashlib,'re':re,'time':time,'KEYS':dict.fromkeys(data),'TABLE_DATABASES':{},'TABLE_SCANNED':{},
             'query':query,'authorized':lambda c:None,'decode':lambda *a:'消息','xml_tag':lambda *a:'','NAMES':{'alice':'alice'},
             'ACCOUNT':'account','is_account':lambda p:False,'datetime':datetime,'timezone':timezone}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'reader-functions','exec'),env)
        values=env['messages']('a@chatroom',200,before_time='200.0',before_id=1000)
        self.assertEqual([v['serverId'] for v in values],[102,101])
        self.assertTrue(any('m.create_time<200' in q for q in queries))


if __name__=='__main__':unittest.main()

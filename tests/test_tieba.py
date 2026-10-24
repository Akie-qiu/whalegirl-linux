"""Discovery boundaries, learned-source isolation and non-idempotent delivery."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from whalegirl_linux.app import create_app
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.tieba import (TiebaReader, TiebaService, active_hour, image_url, interest_hits,
                                  interest_assessment, share_excerpts, share_body)
from test_chat_habits import Gateway

ROOT = Path(__file__).resolve().parents[1]


def directory():
    root = ROOT / 'test-runs'
    root.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='tieba-', dir=root))


class Engine:
    calls = 0
    async def chat(self, *args, **kwargs):
        type(self).calls += 1
        return json.dumps({'posts': [{'tid': '123', 'score': 90, 'reason': '摸鱼反转很有趣',
            'share_text': '这位摸鱼摸得比我还专业', 'image_ids': [],
            'memes': [{'phrase': '摸鱼是物种优势', 'meaning': '拿物种特点给摸鱼找借口', 'scene': '轻松调侃，认真难过时不用'}]}]})
    async def close(self): pass


class Reader:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def threads(self, forum):
        return [{'tid': '123', 'forum': forum, 'title': '摸鱼是物种优势', 'text': '摸鱼是物种优势',
                 'url': 'https://tieba.baidu.com/p/123', 'images': []}]
    async def detail(self, thread): return {**thread, 'posts': []}


class TiebaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = directory()
        self.config = Config(self.root)
        self.config.save(Settings(enabled=True, account_id='account', allowed_chat_ids=['a@chatroom', 'b@chatroom'],
            tieba_enabled=True, tieba_start_hour=0, tieba_end_hour=0,
            tieba_share_chat_ids=['a@chatroom'], tieba_images_enabled=False), 'test-key')
        self.store = Store(self.root/'state.sqlite3')
        self.gateway = Gateway()
        self.r = Runtime(self.config, self.store, self.gateway, ROOT/'styles', engine_factory=lambda **kw: Engine())
        self.r.tieba.reader_factory = lambda credentials: Reader()
        self.b = self.r.tieba
        self.sent = []
        async def status(): return {'status': 'logged_in', 'loggedInUser': 'account'}
        async def send(chat, text, **kwargs):
            self.sent.append((chat, text))
            return Receipt(True, '987')
        self.gateway.status_for_send = status
        self.gateway.send = send
        Engine.calls = 0

    async def asyncTearDown(self):
        self.store.close()

    async def test_discovery_one_model_call_dedup_and_external_memory_isolated(self):
        await self.b.scan()
        self.assertEqual(Engine.calls, 1)
        s = self.b.snapshot()
        self.assertEqual((len(s['posts']), len(s['memes']), len(s['shares'])), (1, 1, 1))
        self.assertEqual(s['runs'][0]['state'], 'done')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memories').fetchone()[0], 0)
        self.assertIn('摸鱼是物种优势', self.b.prompt('又在摸鱼是吧'))
        self.assertEqual(self.b.prompt('晚上吃面条'), '')
        await self.b.scan()
        self.assertEqual(Engine.calls, 1)
        self.assertEqual(len(self.b.snapshot()['shares']), 1)

    async def test_keyword_priority_and_adjusted_score_select_the_share(self):
        self.config.settings.tieba_interests = ['同人文']
        visited = []
        class ManyReader(Reader):
            async def threads(self, forum):
                rows = [{**(await super().threads(forum))[0], 'tid': str(450+i),
                         'title': '普通帖子', 'text': '普通讨论', 'url': 'https://tieba.baidu.com/p/'+str(450+i)} for i in range(6)]
                rows.append({**rows[0], 'tid': '123', 'title': '同人文新篇', 'text': '角色的故事',
                             'url': 'https://tieba.baidu.com/p/123'})
                return rows
            async def detail(self, thread):
                visited.append(thread['tid'])
                return {**thread, 'posts': [], 'source_version': 2}
        class RankingEngine(Engine):
            async def chat(self, messages, **kwargs):
                posts = json.loads(messages[0]['content'])['posts']
                self.posts = posts
                return json.dumps({'posts': [{'tid': p['tid'], 'score': 61 if p['tid']=='123' else 77,
                    'reason': '有内容的讨论', 'share_text': '这篇值得看看', 'image_ids': [], 'memes': []} for p in posts]})
        engine = RankingEngine()
        self.b.reader_factory = lambda c: ManyReader()
        with patch.object(self.b, 'new_engine', return_value=engine):
            await self.b.scan()
        self.assertEqual(len(visited), 4)
        self.assertEqual(visited[0], '123')
        self.assertEqual(engine.posts[0]['interest_evidence'][0]['keyword'], '同人文')
        snapshot = self.b.snapshot()
        self.assertEqual(snapshot['shares'][0]['tid'], '123')
        chosen = next(p for p in snapshot['posts'] if p['tid']=='123')
        self.assertEqual(chosen['score'], 82)
        self.assertIn('同人文（+21）', chosen['reason'])
        self.assertTrue(chosen['archive'])

    async def test_interest_edit_reconsiders_cached_posts_without_duplicate_shares(self):
        class CachedReader(Reader):
            async def detail(self, thread): return {**thread, 'source_version': 2, 'posts': []}
        self.b.reader_factory = lambda c: CachedReader()
        await self.b.scan()
        self.assertEqual(Engine.calls, 1)
        class EmptyReader(CachedReader):
            async def threads(self, forum): return []
            async def detail(self, thread): raise AssertionError('Full cached original does not need a detail request')
        self.b.reader_factory = lambda c: EmptyReader()
        self.config.settings.tieba_interests = ['摸鱼']
        await self.b.scan()
        self.assertEqual(Engine.calls, 2)
        self.assertIn('兴趣命中：摸鱼', self.b.snapshot()['posts'][0]['reason'])
        self.assertEqual(len(self.b.snapshot()['shares']), 1)
        await self.b.scan()
        self.assertEqual(Engine.calls, 2)
        self.config.settings.tieba_interest_sensitivity = 0
        await self.b.scan()
        self.assertEqual(Engine.calls, 3)
        self.assertEqual(self.b.snapshot()['posts'][0]['score'], 90)

    async def test_changed_interests_during_model_call_do_not_commit_stale_analysis(self):
        class ChangingEngine(Engine):
            async def chat(engine, *args, **kwargs):
                self.config.settings.tieba_interests = ['新的兴趣']
                return await super().chat(*args, **kwargs)
        with patch.object(self.b, 'new_engine', return_value=ChangingEngine()):
            await self.b.scan()
        self.assertEqual(self.b.snapshot()['posts'][0]['analyzed'], 0)
        self.assertEqual(self.b.snapshot()['shares'], [])

    async def test_unproven_phrase_is_not_learned_and_expired_meme_is_hidden(self):
        self.b.learn('account', {'tid':'123','text':'普通发言','url':'https://tieba.baidu.com/p/123','posts':[]},
                     {'memes':[{'phrase':'编造的梗','meaning':'不存在的意思','scene':'随便用'}]})
        self.assertEqual(self.b.snapshot()['memes'], [])
        await self.b.scan()
        m = self.b.snapshot()['memes'][0]
        self.b.execute('UPDATE tieba_memes SET expires_at=? WHERE id=?', (time.time()-1,m['id']))
        self.assertEqual(self.b.prompt('摸鱼是物种优势'), '')
        self.b.edit('memes',m['id'],{'renew':True})
        self.assertTrue(self.b.prompt('摸鱼是物种优势'))
        self.b.edit('memes',m['id'],{'enabled':False})
        self.assertEqual(self.b.prompt('摸鱼是物种优势'), '')

    async def test_asking_what_was_learned_recalls_real_text_not_only_pictures(self):
        await self.b.scan()
        for query in ['你最近在贴吧学到什么梗了', '你学到了什么', '会哪些梗', '把学到的梗给我看看']:
            diagnostics = {}
            prompt = self.b.prompt(query, diagnostics)
            self.assertIn('摸鱼是物种优势', prompt)
            self.assertIn('拿物种特点给摸鱼找借口', prompt)
            self.assertEqual(diagnostics['mode'], 'inventory')
            self.assertEqual(diagnostics['recalled'], 1)
        self.assertIn('摸鱼是物种优势', self.b.prompt('找借口是什么意思'))
        self.assertEqual(self.b.prompt('晚上吃面条'), '')
        self.b.execute('UPDATE tieba_memes SET enabled=0')
        self.assertIn('文字梗为0条', self.b.prompt('你学到了什么梗'))
        self.b.execute('UPDATE tieba_memes SET enabled=1,expires_at=?', (time.time()-1,))
        self.assertNotIn('摸鱼是物种优势', self.b.prompt('学到什么梗'))

    async def test_meme_inventory_respects_account_and_learning_switches(self):
        await self.b.scan()
        self.config.settings.account_id='other'
        self.assertNotIn('摸鱼是物种优势', self.b.prompt('你学到什么梗'))
        self.config.settings.account_id='account'
        self.config.settings.tieba_learn_enabled=False
        self.assertEqual(self.b.prompt('你学到什么梗'), '')
        self.config.settings.tieba_learn_enabled=True
        self.config.settings.tieba_enabled=False
        self.assertEqual(self.b.prompt('你学到什么梗'), '')

    async def test_account_and_switch_isolation(self):
        await self.b.scan()
        self.config.settings.tieba_learn_enabled=False
        self.assertEqual(self.b.prompt('摸鱼是物种优势'), '')
        self.config.settings.account_id='other'
        self.assertEqual(self.b.snapshot()['memes'], [])
        self.assertEqual(self.b.snapshot()['posts'], [])

    async def test_share_goes_only_to_selected_chat_and_cannot_repeat(self):
        await self.b.scan()
        self.assertTrue(await self.b.dispatch_once())
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.sent[0][0],'a@chatroom')
        self.assertTrue(self.sent[0][1].endswith('https://tieba.baidu.com/p/123'))
        self.assertIn('原帖摘录：\n“摸鱼是物种优势”',self.sent[0][1])
        self.assertLess(self.sent[0][1].index('原帖摘录'),self.sent[0][1].index('这位摸鱼'))
        self.assertEqual(self.b.share_count('account'),1)
        self.assertEqual(self.store.db.execute("SELECT state FROM outbound_actions WHERE id LIKE 'tieba:%'").fetchone()[0],'sent')
        self.assertFalse(await self.b.dispatch_once())
        self.assertEqual(len(self.sent),1)

    async def test_model_selects_verbatim_passage_and_comment_can_be_empty(self):
        class ExcerptReader(Reader):
            async def detail(self, thread):
                return {**thread,'text':'开场说明。\n关键内容是合作比单打独斗更有趣。\n最后闲聊。','posts':[]}
        class ExcerptEngine(Engine):
            async def chat(self,*args,**kwargs):
                return json.dumps({'posts':[{'tid':'123','score':90,'share_text':'',
                    'share_excerpts':[{'pid':'','text':'关键内容是合作比单打独斗更有趣。'}]}]})
        self.b.reader_factory=lambda c:ExcerptReader()
        with patch.object(self.b,'new_engine',return_value=ExcerptEngine()):await self.b.scan()
        share=self.b.snapshot()['shares'][0]
        self.assertIn('“关键内容是合作比单打独斗更有趣。”',share['text'])
        self.assertNotIn('开场说明',share['text'])
        self.assertTrue(await self.b.dispatch_once())

    async def test_upgrade_pending_share_preserves_sent_and_uncertain_history(self):
        await self.b.scan()
        row=self.b.snapshot()['shares'][0]
        self.b.execute("UPDATE tieba_shares SET text='旧的评论加链接' WHERE id=?",(row['id'],))
        self.b.refresh_queued_excerpts()
        self.assertIn('原帖摘录',self.b.snapshot()['shares'][0]['text'])
        self.b.execute("UPDATE tieba_shares SET state='uncertain',text='保留原有未知结果' WHERE id=?",(row['id'],))
        self.b.refresh_queued_excerpts()
        self.assertEqual(self.b.snapshot()['shares'][0]['text'],'保留原有未知结果')
        self.b.execute("UPDATE tieba_shares SET state='sent',text='保留已发送内容' WHERE id=?",(row['id'],))
        self.b.refresh_queued_excerpts()
        self.assertEqual(self.b.snapshot()['shares'][0]['text'],'保留已发送内容')

    async def test_disable_quota_and_removed_permission_prevent_delivery(self):
        await self.b.scan()
        self.config.settings.tieba_share_per_day=0
        self.assertFalse(await self.b.dispatch_once())
        self.assertEqual(self.sent,[])
        self.config.settings.tieba_share_per_day=3
        self.config.settings.tieba_share_chat_ids=[]
        self.assertFalse(await self.b.dispatch_once())
        self.assertEqual(self.b.snapshot()['shares'][0]['state'],'cancelled')

    async def test_unknown_receipt_is_not_retried_after_restart(self):
        await self.b.scan()
        async def send(chat,text): return Receipt(False,error='尚未确认')
        self.gateway.send=send
        await self.b.dispatch_once()
        self.assertEqual(self.b.snapshot()['shares'][0]['state'],'uncertain')
        b=TiebaService(self.r)
        self.assertFalse(await b.dispatch_once())
        row=b.snapshot()['shares'][0]
        b.edit('shares',row['id'],{'op':'received'})
        self.assertEqual(b.snapshot()['shares'][0]['state'],'sent')

    async def test_inflight_crash_is_durable_and_does_not_send_again(self):
        await self.b.scan()
        async def send(chat,text): raise RuntimeError('transport interrupted')
        self.gateway.send=send
        with self.assertRaises(RuntimeError): await self.b.dispatch_once()
        self.assertEqual(self.b.snapshot()['shares'][0]['state'],'uncertain')
        self.assertFalse(await self.b.dispatch_once())

    async def test_normal_chat_has_priority_and_old_shares_expire(self):
        await self.b.scan()
        self.r.processing='生成回复'
        self.assertFalse(await self.b.dispatch_once())
        self.r.processing=''
        self.b.execute("UPDATE tieba_shares SET created=?", (time.time()-7*3600,))
        self.assertFalse(await self.b.dispatch_once())
        self.assertEqual(self.b.snapshot()['shares'][0]['state'],'cancelled')

    async def test_tieba_images_wait_for_tagging_and_obey_image_switch(self):
        picture=Image.new('RGB',(30,30),'blue');data=io.BytesIO();picture.save(data,format='PNG')
        with patch.object(self.r.images,'stage'):
            asset=self.r.images.save_asset(data.getvalue(),source='tieba',account='account',scope='global')
        self.assertFalse(self.r.images.tools.allowed(asset,'a@chatroom'))
        self.config.settings.tieba_images_enabled=True
        self.assertFalse(self.r.images.tools.allowed(asset,'a@chatroom'))
        self.store.db.execute("UPDATE picture_assets SET state='tagged' WHERE id=?", (asset['id'],))
        self.store.db.commit()
        self.assertTrue(self.r.images.tools.allowed(self.r.images.asset(asset['id']),'a@chatroom'))
        self.config.settings.tieba_images_enabled=False
        self.assertFalse(self.r.images.tools.allowed(self.r.images.asset(asset['id']),'a@chatroom'))

    async def test_image_is_actually_recognized_once_and_unfit_image_disabled(self):
        self.config.settings.tieba_images_enabled=True
        picture=Image.new('RGB',(30,30),'red');data=io.BytesIO();picture.save(data,format='PNG')
        with patch.object(self.r.images,'stage'):
            asset=self.r.images.save_asset(data.getvalue(),source='tieba',account='account',scope='global')
        class TagEngine(Engine):
            async def chat(self,messages,**kwargs):
                self.calls+=1
                assert messages[0]['content'][1]['type']=='image_url'
                assert 'usable' in messages[0]['content'][0]['text']
                return json.dumps({'name':'流水截图','description':'红色图像','scenes':'不推荐','usable':False,'reject_reason':'没有可用含义'})
        engine=TagEngine();engine.calls=0
        with patch.object(self.r.images,'new_engine',return_value=engine):
            self.assertTrue(await self.r.images.tag_once())
            self.assertFalse(await self.r.images.tag_once())
        saved=self.r.images.asset(asset['id'])
        self.assertEqual(engine.calls,1)
        self.assertEqual(saved['enabled'],0)
        self.assertEqual(saved['state'],'tagged')

    async def test_meme_full_edit_keeps_source_and_is_used_by_chat(self):
        await self.b.scan()
        m=self.b.snapshot()['memes'][0]
        self.b.edit('memes',m['id'],{'phrase':'下班摸鱼','meaning':'下班前悠闲摸鱼','scene':'轻松闲聊'})
        updated=self.b.snapshot()['memes'][0]
        self.assertEqual(updated['source_phrase'],'摸鱼是物种优势')
        self.assertEqual(updated['phrase'],'下班摸鱼')
        self.assertIn('下班摸鱼',self.b.prompt('准备下班摸鱼'))
        self.config.settings.tieba_interests=['摸鱼']
        await self.b.scan()
        self.assertEqual(len(self.b.snapshot()['memes']),1)
        self.assertEqual(self.b.snapshot()['memes'][0]['phrase'],'下班摸鱼')
        with self.assertRaises(ValueError):self.b.edit('memes',m['id'],{'phrase':'  '})
        self.config.settings.account_id='other'
        with self.assertRaises(ValueError):self.b.edit('memes',m['id'],{'meaning':'不能改其他账号'})

    async def test_manual_retry_keeps_attempts_and_never_retries_unknown(self):
        await self.b.scan()
        async def failed(chat,text):return Receipt(False,error='未找到会话，未发送',definite_failure=True)
        self.gateway.send=failed
        await self.b.dispatch_once()
        row=self.b.snapshot()['shares'][0]
        self.b.edit('shares',row['id'],{'op':'retry'})
        async def received(chat,text):return Receipt(True,'988')
        self.gateway.send=received
        self.r.last_send=0
        self.assertTrue(await self.b.dispatch_once())
        actions=self.store.actions('tieba:'+row['id'])
        self.assertEqual([a['state'] for a in actions],['failed','sent'])
        self.assertEqual(len({a['id'] for a in actions}),2)
        self.b.execute("UPDATE tieba_shares SET state='uncertain' WHERE id=?",(row['id'],))
        with self.assertRaises(ValueError):self.b.edit('shares',row['id'],{'op':'retry'})

    async def test_master_switch_stops_browsing_dispatch_prompt_and_pending_vision(self):
        await self.b.scan()
        image=Image.new('RGB',(24,24),'blue');data=io.BytesIO();image.save(data,format='PNG')
        with patch.object(self.r.images,'stage'):
            asset=self.r.images.save_asset(data.getvalue(),source='tieba',account='account',scope='global')
        self.config.settings.tieba_images_enabled=True
        self.config.settings.tieba_enabled=False
        calls=Engine.calls
        self.assertFalse(await self.b.scan())
        self.assertFalse(await self.b.dispatch_once())
        self.assertFalse(await self.r.images.tag_once())
        self.assertEqual(Engine.calls,calls)
        self.assertEqual(self.b.prompt('摸鱼是物种优势'),'')
        self.store.db.execute("UPDATE picture_assets SET state='tagged' WHERE id=?",(asset['id'],))
        self.store.db.commit()
        self.assertFalse(self.r.images.tools.allowed(self.r.images.asset(asset['id']),'a@chatroom'))
        self.assertEqual(self.b.snapshot()['shares'][0]['state'],'queued')
        self.assertEqual(len(self.b.snapshot()['memes']),1)
        self.config.settings.tieba_enabled=True
        self.assertTrue(self.r.images.tools.allowed(self.r.images.asset(asset['id']),'a@chatroom'))

    async def test_failed_scan_keeps_error_and_schedules_later(self):
        class BadReader(Reader):
            async def threads(self,forum): raise ValueError('列表需要确认登录')
        self.b.reader_factory=lambda c:BadReader()
        await self.b.scan()
        s=self.b.snapshot()
        self.assertEqual(s['runs'][0]['state'],'failed')
        self.assertIn('确认登录',s['runs'][0]['error'])
        self.assertGreater(s['next_at'],time.time()+1700)
        self.assertFalse(s['busy'])


class ConfigAndBoundaryTests(unittest.TestCase):
    def test_source_quotations_reject_rewriting_and_keep_reply_attribution(self):
        thread={'title':'测试帖','text':'主楼第一句。\n\n主楼主要内容。','posts':[
            {'pid':'11','floor':1,'text':'主楼第一句。\n\n主楼主要内容。'},
            {'pid':'22','floor':2,'text':'回复中的不同观点。','comments':[]}]}
        chosen=share_excerpts(thread,[{'pid':'22','text':'回复中的不同观点。'}])
        self.assertEqual(chosen[0]['label'],'2楼')
        formatted=share_body(thread,'我的看法',chosen)
        self.assertIn('2楼摘录：\n“回复中的不同观点。”',formatted)
        self.assertLess(formatted.index('回复中的不同观点'),formatted.index('我的看法'))
        rejected=share_excerpts(thread,[{'pid':'11','text':'模型自己改写的主要内容'},
                                      {'pid':'11','text':'回复中的不同观点。'}])
        self.assertEqual(rejected[0]['text'],thread['text'])
        self.assertNotIn('模型自己改写',share_body(thread,'',rejected))
        self.assertNotIn('原帖摘录',share_body({'title':'图帖','text':'','posts':[]},'看看这个'))
        long={'title':'长文','text':'原句内容。'*300}
        quotes=share_excerpts(long)
        self.assertLessEqual(sum(len(q['text']) for q in quotes),600)
        self.assertTrue(quotes[0]['truncated'])
        self.assertIn('（节选）',share_body(long,'',quotes))
        self.assertTrue(share_body(thread,'可能有剧透',chosen).startswith('可能有剧透'))

    def test_interest_evidence_semantics_and_sensitivity(self):
        s=Settings(tieba_interests=['AI','星见雅','同人文'])
        thread={'title':'ＡＩ game', 'text':'daily 讨论'+('背景 ' * 900)+'同人文新篇',
                'posts':[{'text':'雅的这套动作很帅', 'comments':[]}]}
        hits=interest_hits(thread,s.tieba_interests)
        self.assertEqual([h['keyword'] for h in hits],['AI','同人文'])
        semantic={'score':60,'interest_matches':[{'keyword':'星见雅','evidence':'雅的这套动作很帅'}]}
        result=interest_assessment(thread,semantic,s)
        self.assertEqual(result['matches'],['AI','同人文','星见雅'])
        self.assertGreaterEqual(result['score'],75)
        unrelated={'title':'daily','text':'其他角色的动作','posts':[]}
        self.assertEqual(interest_assessment(unrelated,semantic,s)['score'],60)
        s.tieba_interest_sensitivity=0
        self.assertEqual(interest_assessment(thread,semantic,s)['score'],60)
        s.tieba_interest_sensitivity=100
        self.assertLess(interest_assessment(thread,{'score':10},s)['score'],75)

    def test_credentials_encrypted_and_not_in_public_settings(self):
        c=Config(directory());c.save_tieba_credentials('test-bduss','test-stoken')
        self.assertEqual(c.tieba_credentials()['BDUSS'],'test-bduss')
        self.assertNotIn(b'test-bduss',(c.directory/'tieba-credentials.enc').read_bytes())
        self.assertNotIn('BDUSS',json.dumps(c.public()))
        self.assertFalse(any(hasattr(TiebaReader,n) for n in ['add_post','add_thread','agree']))

    def test_cdn_url_and_time_window_boundaries(self):
        self.assertTrue(image_url('https://tiebapic.baidu.com/forum/pic/item/a.jpg'))
        for v in ['https://127.0.0.1/a.jpg','http://tiebapic.baidu.com/a.jpg',
                  'https://tiebapic.baidu.com.attacker.test/a.jpg','https://secret@tiebapic.baidu.com/a.jpg']:
            self.assertFalse(image_url(v))
        s=Settings(tieba_start_hour=0,tieba_end_hour=0)
        self.assertTrue(active_hour(s))

    def test_admin_api_preserves_other_settings_and_protects_write_permissions(self):
        app=create_app(directory())
        with TestClient(app) as client:
            c=app.state.config
            c.settings.allowed_chat_ids=['a@chatroom']
            c.settings.image_character_variant='chibi'
            headers={'Authorization':'Bearer '+c.admin_token}
            self.assertEqual(client.get('/api/tieba').status_code,401)
            response=client.post('/api/tieba/config',headers=headers,json={'values':{'tieba_enabled':True,'tieba_share_chat_ids':['a@chatroom']}})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(c.settings.image_character_variant,'chibi')
            service=app.state.runtime.tieba
            service.learn(c.settings.account_id,{'tid':'1','text':'摸鱼','url':'https://tieba.baidu.com/p/1','posts':[]},
                          {'memes':[{'phrase':'摸鱼','meaning':'暂时休息','scene':'闲聊'}]})
            identity=service.snapshot()['memes'][0]['id']
            local_headers={**headers,'Origin':'http://127.0.0.1:18092','Host':'127.0.0.1:18092'}
            edited=client.post('/api/tieba/memes/'+identity,headers=local_headers,json={'phrase':'一起摸鱼','meaning':'摸会儿鱼','scene':'下班前'})
            self.assertEqual(edited.status_code,200,edited.text)
            self.assertEqual(client.post('/api/tieba/memes/'+identity,headers={**headers,'Origin':'http://other.invalid'},json={'meaning':'不得写入'}).status_code,403)
            service.requested=True
            off=client.post('/api/tieba/config',headers=headers,json={'values':{'tieba_enabled':False}})
            self.assertEqual(off.status_code,200)
            self.assertFalse(service.requested)
            self.assertTrue(c.settings.tieba_share_enabled)
            self.assertEqual(client.post('/api/tieba/config',headers=headers,json={'values':{'tieba_share_chat_ids':['unknown']}}).status_code,400)
            self.assertEqual(client.post('/api/tieba/config',headers=headers,json={'values':{'model':'evil'}}).status_code,400)


if __name__=='__main__':unittest.main()

"""Small checks for explicit permission, durable boundaries and mixed libraries."""
import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from PIL import Image
from whalegirl_linux.images import ImageService, explicit_image_request, GenerationUncertain
from whalegirl_linux.moments import MomentsBook
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.image_references import ReferenceLibrary, reference_mode

ROOT=Path(__file__).resolve().parents[1]


class ImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.path=Path(tempfile.mkdtemp(prefix='image-check-',dir=ROOT/'test-runs'))
        self.config=Config(self.path)
        self.config.save(Settings(account_id='account',enabled=True,allowed_chat_ids=['allowed'],image_per_day=1),'test-chat-key')
        self.config.save_image_key('test-picture-key')
        self.store=Store(self.path/'state.sqlite3')
        native=self.path/'native';native.mkdir()
        self.book=MomentsBook(self.store)
        self.runtime=SimpleNamespace(store=self.store,config=self.config,
            gateway=SimpleNamespace(ui_jobs_directory=native),moments=SimpleNamespace(book=self.book,lock=asyncio.Lock()))
        self.service=ImageService(self.runtime)
        out=io.BytesIO();Image.new('RGB',(80,60),'blue').save(out,format='PNG');self.raw=out.getvalue()

    def tearDown(self):
        self.store.close()

    def test_explicit_requests_only(self):
        for text in ['这张图片是什么','这个图真好看','来个害羞表情包','不要生成图片','不用发图片','你怎么看这张图','来看看这张图片','我刚才发的图片怎么样']:
            self.assertIsNone(explicit_image_request(text),text)
        self.assertEqual(explicit_image_request('帮我画一只蓝鲸'),'generate')
        self.assertEqual(explicit_image_request('发一张猫咪图片'),'send')
        self.assertEqual(explicit_image_request('那张图再发一下'),'send')
        self.assertEqual(explicit_image_request('发一张你画的图片'),'send')

    def test_two_sources_are_distinct_and_staged_for_moments(self):
        a=self.service.save_asset(self.raw,scope='global')
        b=self.service.save_asset(self.raw,'generated',scope='global',prompt='蓝鲸',model='test')
        self.assertNotEqual(a['id'],b['id'])
        self.assertNotEqual(self.service.path(a).parent,self.service.path(b).parent)
        for item in [a,b]:
            self.assertTrue((self.runtime.gateway.ui_jobs_directory/'moments-assets'/(item['id']+'.png')).is_file())
        self.service.edit_asset(a['id'],dict(name='海边',description='海水',scenes='旅行',enabled=False))
        with self.assertRaises(ValueError):self.service.stage(a['id'])
        self.assertEqual([r['id'] for r in self.service.candidates()], [b['id']])

    def test_restart_never_reissues_paid_requests(self):
        identity=self.service.enqueue(kind='generate',prompt='蓝鲸')
        self.service.change(identity,state='generating',api_started=1)
        second=ImageService(self.runtime)
        self.assertEqual(second.get(identity)['state'],'uncertain')
        self.assertEqual(second.enqueue(kind='send',asset_id='',prompt='蓝鲸',source_key='original'),
                         second.enqueue(kind='send',asset_id='',prompt='蓝鲸',source_key='original'))

    def test_cancel_after_submission_still_consumes_daily_allowance(self):
        identity=self.service.enqueue(kind='generate',prompt='蓝鲸')
        self.service.change(identity,state='cancelled',api_started=time.time())
        with self.assertRaisesRegex(ValueError,'上限'):self.service.enqueue(kind='generate',prompt='鲸鱼')
        with self.assertRaisesRegex(ValueError,'尚未允许'):self.service.enqueue(kind='send',chat='other',destination='chat')

    async def test_forwarded_command_never_triggers_generation(self):
        first=dict(chat='allowed',key='forward',trigger='direct',content='帮我画一张图',payload_json='{"forwarded":true}')
        self.assertIsNone(await self.service.chat_request(first,[first]))
        first.update(payload_json='{"reply":{}}',content='这张是什么\n[引用原话｜群友] 帮我画一张图')
        self.assertIsNone(await self.service.chat_request(first,[first]))

    async def test_slow_generation_does_not_hold_ui_and_cancel_preserves_result(self):
        started=asyncio.Event();finish=asyncio.Event()
        async def fake_generate(job):
            started.set();await finish.wait()
            return self.service.save_asset(self.raw,'generated',scope='global',prompt='蓝鲸')
        self.service.generate=fake_generate
        identity=self.service.enqueue(kind='generate',prompt='蓝鲸')
        work=asyncio.create_task(self.service.generate_once())
        await started.wait()
        self.assertFalse(work.done())
        self.service.change(identity,state='cancelled')
        finish.set();await work
        self.assertEqual(self.service.get(identity)['state'],'cancelled')
        self.assertEqual(len(self.service.assets('generated')),1)

    async def test_moments_waits_for_picture_and_receives_correct_asset(self):
        moment=self.book.create('account','publish','unit',{})
        self.book.change(moment,'awaiting_image',text='这是一条草稿')
        a=self.service.save_asset(self.raw,'generated',scope='global')
        identity=self.service.enqueue(kind='send',asset_id=a['id'],destination='moments',moment_id=moment)
        self.service.change(identity,state='ready')
        await self.service.process_once()
        row=self.book.get('account',moment)
        self.assertEqual(row['state'],'draft')
        self.assertIn(a['id'],row['payload'])
        self.assertEqual(self.service.get(identity)['receipt'],'attached-to-moments-draft')

    def test_character_default_snapshot_and_separate_reference_library(self):
        for variant in ('chibi','girl','mature'):
            self.service.references.add(self.raw,variant,'测试'+variant,'角色外观')
        identity=self.service.enqueue(kind='generate',prompt='帮我画一张你自己的少女版',character=True)
        plan=json.loads(self.service.get(identity)['reference_json'])
        self.assertEqual(plan['variant'],'girl')
        self.assertTrue(plan['character'])
        self.config.save(self.config.settings.model_copy(update={'image_character_variant':'mature'}))
        self.assertEqual(json.loads(self.service.get(identity)['reference_json'])['variant'],'girl')
        self.assertEqual(len(self.service.references.public()['items']),3)
        self.assertEqual(self.service.assets(),[])

    def test_reference_intents_preserve_explicit_request_boundary(self):
        for text in ['把这张图片改成水彩风','参考这张图画一张海报']:
            self.assertEqual(explicit_image_request(text),'generate',text)
        for text in ['你长什么样子','你知道你长什么样吗','你笑一个给我看','你会画画吗','你刚才为什么画图','画好了吗','我没有让你画一张图','不要画你的照片','这张图片是什么','这张图很像你','不要把这张图改成水彩风']:
            self.assertIsNone(explicit_image_request(text),text)
        self.assertEqual(reference_mode('保留原图，只修改颜色'),'edit')
        self.assertEqual(reference_mode('参考动作重新画一张'),'remix')
        self.assertEqual(reference_mode('其他不变，只改背景','remix'),'edit')
        self.assertEqual(reference_mode('按这个动作重新画一张','edit'),'remix')

    def message(self,server_id,*,chat='allowed',sender='alice',kind=3,stamp=100,content='[图片]',payload='{}'):
        return dict(key=f'account/{chat}/{server_id}',account='account',chat=chat,sender=sender,sender_id=sender,
                    kind=kind,is_self=0,local_id=int(server_id),content=content,timestamp=stamp,payload_json=payload)

    def test_reference_binding_keeps_chat_and_sender_scopes(self):
        rows=[self.message('1'),self.message('2',sender='bob',stamp=101),
              self.message('3',chat='other',stamp=102)]
        self.store.observe(rows)
        request=self.message('4',kind=1,stamp=103,content='把这张图片改成水彩风')
        plan=self.service.references.chat_plan(self.store,request,[request],request['content'])
        self.assertEqual([v['key'] for v in plan['sources']],[rows[0]['key']])
        request['sender']=request['sender_id']='someone-else'
        with self.assertRaisesRegex(ValueError,'没找到'):
            self.service.references.chat_plan(self.store,request,[request],request['content'])

    async def test_unresolved_quoted_reference_never_uses_a_different_image(self):
        self.store.observe([self.message('1')])
        request=self.message('4',kind=49,stamp=103,content='把这张图片改成水彩风',
                             payload=json.dumps({'reply':{'type':3,'serverId':'999'}}))
        plan=self.service.references.chat_plan(self.store,request,[request],request['content'])
        self.assertEqual(len(plan['sources']),1)
        self.assertTrue(plan['sources'][0]['unresolved'])
        async def missing(*args):return None
        self.runtime.gateway.referenced_message=missing
        with self.assertRaisesRegex(ValueError,'原图尚未同步'):
            await self.service.references.materialize(plan,self.store,self.runtime.gateway,'account','allowed')

    async def test_edit_uploads_real_image_bytes_and_no_generation_fallback(self):
        self.service.references.add(self.raw,'girl','少女图','参考少女外观')
        identity=self.service.enqueue(kind='generate',prompt='帮我画一张你自己的少女版',character=True)
        seen=[]
        async def handler(request):
            body=await request.aread();seen.append(request.url.path)
            self.assertEqual(request.url.path,'/v1/images/edits')
            self.assertIn(b'image[]',body)
            self.assertIn(b'\x89PNG\r\n\x1a\n',body)
            self.assertIn('少女版'.encode(),body)
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(self.raw).decode()}]})
        original=httpx.AsyncClient
        with patch('whalegirl_linux.images.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(handler),**kw)):
            await self.service.generate_once()
            await self.service.process_once()
        self.assertEqual(seen,['/v1/images/edits'])
        self.assertEqual(self.service.get(identity)['state'],'sent')
        self.assertEqual(self.service.get(identity)['receipt'],'saved-to-library')
        self.assertNotIn('reference_json',self.service.snapshot()['jobs'][0])

    def test_reference_files_are_scoped_to_the_account(self):
        first=ReferenceLibrary(self.config,account='account')
        second=ReferenceLibrary(self.config,account='different-account')
        a=first.store(self.raw);b=second.store(self.raw)
        self.assertNotEqual(a['id'],b['id'])
        self.assertNotEqual(first.root,second.root)

    async def test_gallery_sends_while_earlier_generation_is_still_waiting(self):
        started=asyncio.Event();finish=asyncio.Event();sent=[]
        async def slow_generate(job):
            self.service.change(job['id'],state='generating',api_started=1)
            started.set();await finish.wait()
            return self.service.save_asset(self.raw,'generated',scope='global')
        async def deliver(job):
            sent.append(job['id']);self.service.change(job['id'],state='sent',receipt='test-confirmed')
        self.service.generate=slow_generate;self.service.deliver=deliver
        generation=self.service.enqueue(kind='generate',prompt='蓝鲸',chat='allowed',destination='chat')
        work=asyncio.create_task(self.service.generate_once())
        await started.wait()
        try:
            asset=self.service.save_asset(self.raw,scope='global')
            gallery=self.service.enqueue(kind='send',asset_id=asset['id'],chat='allowed',destination='chat')
            self.assertTrue(await asyncio.wait_for(self.service.process_once(),timeout=.5))
            self.assertEqual(sent,[gallery])
            self.assertFalse(work.done())
            self.assertEqual(self.service.get(generation)['state'],'generating')
            self.assertEqual(self.service.snapshot()['workers']['generation'],generation)
        finally:
            finish.set();await work
        self.assertEqual(self.service.get(generation)['state'],'ready')
        await self.service.process_once()
        self.assertEqual(sent,[gallery,generation])
        self.assertEqual(self.service.get(generation)['state'],'sent')

    async def test_dispatchers_keep_actual_sends_serial(self):
        active=0;maximum=0;sent=[]
        async def deliver(job):
            nonlocal active,maximum
            active+=1;maximum=max(maximum,active)
            await asyncio.sleep(.01)
            sent.append(job['id']);self.service.change(job['id'],state='sent',receipt='test-confirmed')
            active-=1
        self.service.deliver=deliver
        asset=self.service.save_asset(self.raw,scope='global')
        jobs=[self.service.enqueue(kind='send',asset_id=asset['id'],chat='allowed',destination='chat') for _ in range(2)]
        await asyncio.gather(self.service.process_once(),self.service.process_once())
        self.assertEqual(maximum,1)
        self.assertEqual(sent,jobs)

    async def test_uncertain_generation_does_not_stop_gallery_or_retry(self):
        calls=[]
        async def uncertain(job):
            calls.append(job['id']);raise GenerationUncertain('结果不确定')
        self.service.generate=uncertain
        generation=self.service.enqueue(kind='generate',prompt='蓝鲸')
        asset=self.service.save_asset(self.raw,scope='global')
        gallery=self.service.enqueue(kind='send',asset_id=asset['id'])
        await self.service.generate_once()
        self.assertFalse(await self.service.generate_once())
        await self.service.process_once()
        self.assertEqual(calls,[generation])
        self.assertEqual(self.service.get(generation)['state'],'uncertain')
        self.assertEqual(self.service.get(gallery)['state'],'sent')


    def test_candidates_homophone_and_overlapping_bigram_and_multi_send(self):
        def make_png(color):
            buf = io.BytesIO()
            Image.new('RGB', (80, 60), color).save(buf, format='PNG')
            return buf.getvalue()
        other = self.service.save_asset(make_png('red'), scope='global')
        self.service.edit_asset(other['id'], dict(name='普通风景', description='树林', scenes='日常', enabled=True))
        w1 = self.service.save_asset(make_png('green'), scope='global')
        self.service.edit_asset(w1['id'], dict(name='挽昼三形态立绘', description='绝区零角色挽昼', scenes='游戏展示', enabled=True))
        w2 = self.service.save_asset(make_png('yellow'), scope='global')
        self.service.edit_asset(w2['id'], dict(name='黑蓝系三女角色立绘', description='绝区零角色挽昼的三种形态', scenes='发喜欢的角色时', enabled=True))
        for q in ('发几张碗粥角色图', '有没有瑞粥的美图', '晚粥立绘'):
            cands = self.service.candidates(q, chat='allowed')
            self.assertEqual({cands[0]['id'], cands[1]['id']}, {w1['id'], w2['id']}, q)
        first = self.message('10', kind=1, stamp=200, content='发几张碗粥角色图')
        _, ctx = self.service.tools.context(first, [first])
        res = self.service.tools.execute(
            {'tool': 'send_library_image', 'asset_ids': [w1['id'], w2['id']], 'subject_type': 'other', 'operation': 'create'},
            first, [first], ctx
        )
        self.assertTrue(res['ok'])
        jobs = self.store.db.execute("SELECT asset_id FROM picture_jobs WHERE kind='send' ORDER BY created ASC").fetchall()
        self.assertEqual([r[0] for r in jobs], [w1['id'], w2['id']])

    async def test_asking_bot_for_image_does_not_rebind_prior_chat_sticker(self):
        from whalegirl_linux.context import build_context
        old_sticker = self.message('20', sender='alice', kind=47, stamp=time.time() - 20, content='[动画表情]')
        bot_reply = dict(key='account/allowed/21', account='account', chat='allowed', sender='bot', sender_id='bot',
                         kind=1, is_self=1, local_id=21, content='这张凑合看吧', timestamp=time.time() - 15, payload_json='{}')
        self.store.observe([old_sticker, bot_reply])
        for idx, text in enumerate(['@沃不明白 发啊', '@沃不明白 有没有收藏好看的图片？发一下', '@沃不明白 发几张碗粥角色图'], start=30):
            req = self.message(str(idx), sender='alice', kind=1, stamp=time.time() - 5 + idx, content=text)
            self.store.observe([req])
            msgs, image_keys, counts = await build_context(
                self.store, self.runtime.gateway, self.config.settings, req, [req], {}
            )
            self.assertEqual(image_keys, [], text)
            self.assertEqual(counts['images'], 0, text)
            self.assertNotIn('本次相关图片尚未取得有效图像', msgs[0]['content'], text)


if __name__=='__main__':unittest.main()

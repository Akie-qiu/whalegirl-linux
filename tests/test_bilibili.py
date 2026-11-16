"""Only synthetic HTTP, source material and credentials; no live account actions."""
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from whalegirl_linux.bilibili_client import API, PASSPORT, BiliError, BilibiliClient, ReadBudget, signed
from whalegirl_linux.bilibili_reader import VideoReader, crop_frames, subtitle_fraction, subtitle_segments
from whalegirl_linux.growth import GrowthStopped
from whalegirl_linux.memory_layers import WORLD,BILIBILI,INTERNET
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]
BV = 'BV1234567890'
BODY = '街头演奏让路过的人停下来听一会儿。音乐不一定要证明效率，也可以只是让普通的一天有点不同。'*8
WBI = {'img_url': 'https://i0.hdslb.com/bfs/wbi/'+'a'*32+'.png',
       'sub_url': 'https://i0.hdslb.com/bfs/wbi/'+'b'*32+'.png'}


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = ROOT/'test-runs';directory.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix='bilibili-', dir=directory))
        self.config = Config(self.directory)
        self.config.save(Settings(account_id='account', bilibili_enabled=True))
        self.store = Store(self.directory/'state.sqlite3')
        self.requests = []
        self.handler = lambda request: httpx.Response(200, json={'code': 0, 'data': {}})
        async def transport(request):
            self.requests.append(request)
            value = self.handler(request)
            return await value if hasattr(value, '__await__') else value
        self.client = BilibiliClient(self.config, self.store, transport=httpx.MockTransport(transport))
        self.sleeper = patch('whalegirl_linux.bilibili_client.asyncio.sleep', new=AsyncMock())
        self.sleeper.start()

    async def asyncTearDown(self):
        self.sleeper.stop()
        await self.client.close()
        self.store.close()

    def login(self):
        self.client.save_cookies({'SESSDATA': 'fixture-session-only', 'bili_jct': 'fixture-csrf-only', 'DedeUserID': '12345'})
        self.client.set_meta(session_state='logged_in')

    def test_login_is_encrypted_and_not_part_of_public_status_or_sql_material(self):
        self.login()
        raw = self.client.session_path.read_bytes()
        self.assertNotIn(b'fixture-session-only', raw)
        public = json.dumps(self.client.status())
        self.assertNotIn('fixture-session-only', public)
        self.assertNotIn('fixture-csrf-only', public)
        self.assertNotIn('SESSDATA', public)
        if os.name == 'posix':self.assertEqual(self.client.session_path.stat().st_mode & 0o777, 0o600)
        sql = ' '.join(row[0] for row in self.store.db.execute('SELECT value FROM bilibili_meta'))
        self.assertNotIn('fixture-session-only', sql)

    async def test_cookies_are_only_sent_to_whitelisted_api_not_subtitle_cdn(self):
        self.login()
        await self.client.api('/x/web-interface/view', {'bvid': BV})
        await self.client.get('https://aisubtitle.hdslb.com/bfs/ai_subtitle/fixture.json', kind='subtitle')
        self.assertIn('fixture-session-only', self.requests[0].headers.get('Cookie', ''))
        self.assertEqual(self.requests[1].headers.get('Cookie'), '')
        self.assertTrue(all(request.method == 'GET' for request in self.requests))

    async def test_write_paths_untrusted_domains_and_redirects_are_rejected(self):
        for url in ('https://api.bilibili.com/x/web-interface/archive/like',
                    'https://api.bilibili.com/x/v3/fav/resource/deal', 'https://untrusted.example/x/web-interface/view',
                    'http://api.bilibili.com/x/web-interface/view', 'https://api.bilibili.com:8443/x/web-interface/view'):
            with self.assertRaises(ValueError):await self.client.get(url)
        self.assertEqual(self.requests, [])
        self.handler = lambda request: httpx.Response(302, headers={'Location': 'https://untrusted.example/'})
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view')
        self.assertEqual(len(self.requests), 1)

    async def test_risk_pause_survives_restart_and_logout_and_never_anonymously_retries(self):
        self.login()
        self.handler = lambda request: httpx.Response(200, json={'code': -352, 'message': 'private backend detail'})
        with self.assertRaisesRegex(BiliError, '暂停'):await self.client.api('/x/web-interface/view')
        self.client.logout()
        replacement = BilibiliClient(self.config, self.store, transport=httpx.MockTransport(self.handler))
        try:
            self.assertTrue(replacement.status()['paused'])
            with self.assertRaises(BiliError):await replacement.api('/x/web-interface/view')
            self.assertEqual(len(self.requests), 1)
            replacement.resume();self.assertFalse(replacement.status()['paused'])
        finally:await replacement.close()

    async def test_http_risk_and_login_expiry_stop_future_requests(self):
        self.login()
        self.handler = lambda request: httpx.Response(401)
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view')
        self.assertEqual(self.client.status()['state'], 'expired')
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view')
        self.assertEqual(len(self.requests), 1)

    async def test_anonymous_nav_can_provide_public_signing_keys(self):
        self.handler = lambda request: httpx.Response(200, json={'code': -101, 'data': {'isLogin': False, 'wbi_img': WBI}})
        status = await self.client.check_login()
        self.assertEqual(status['state'], 'anonymous')
        self.assertEqual(self.client.wbi, WBI)
        params = signed({'keyword': '音乐', 'page': 1}, WBI, now=1)
        self.assertEqual(len(params['w_rid']), 32)
        self.assertEqual(params['wts'], '1')

    async def test_qr_crossdomain_cookie_capture_and_poll_throttle(self):
        def handler(request):
            path = request.url.path
            if path.endswith('/generate'):
                return httpx.Response(200, json={'code': 0, 'data': {'qrcode_key': 'fixture_qrcode_key_12345',
                    'url': PASSPORT+'/h5-app/passport/login/scan?qrCode=fixture_only'}})
            if path.endswith('/poll'):
                return httpx.Response(200, json={'code': 0, 'data': {'code': 0,
                    'url': PASSPORT+'/x/passport-login/web/crossDomain?ticket=fixture_only'}})
            if path.endswith('/crossDomain'):
                return httpx.Response(302, headers=[('set-cookie', 'SESSDATA=fixture-session-only; Domain=.bilibili.com; Path=/'),
                    ('set-cookie', 'DedeUserID=12345; Domain=.bilibili.com; Path=/'), ('location', 'https://www.bilibili.com/')])
            return httpx.Response(200, json={'code': 0, 'data': {'isLogin': True, 'uname': '合成账号', 'wbi_img': WBI}})
        self.handler = handler
        await self.client.start_login()
        self.assertTrue(self.client.qr_png().startswith(b'\x89PNG'))
        status = await self.client.poll_login()
        self.assertTrue(status['authenticated'])
        self.assertEqual(len(self.requests), 4)
        await self.client.poll_login()
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.requests[2].headers.get('Cookie'), '')
        self.assertNotIn('ticket', json.dumps(status))

    async def test_logout_during_poll_cannot_restore_old_credentials(self):
        self.client.qr = {'key': 'fixture_qrcode_key_12345', 'url': PASSPORT+'/h5-app/passport/login/scan',
            'state': 'waiting_scan', 'expires': time.time()+100, 'polled': 0, 'account': 'account', 'generation': 0}
        def handler(request):
            self.client.logout()
            return httpx.Response(200, json={'code': 0, 'data': {'code': 0}},
                headers={'set-cookie': 'SESSDATA=fixture-session-only; Domain=.bilibili.com; Path=/'})
        self.handler = handler
        with self.assertRaisesRegex(BiliError, '取消'):await self.client.poll_login()
        self.assertEqual(self.client.cookies(), {})

    async def test_current_official_account_qr_landing_is_accepted_but_lookalikes_are_denied(self):
        self.handler=lambda request:httpx.Response(200,json={'code':0,'data':{
            'qrcode_key':'fixture_qrcode_key_12345','url':'https://account.bilibili.com/h5/account-h5/auth/scan-web?auth_code=fixture_only'}})
        result=await self.client.start_login()
        self.assertEqual(result['qr_state'],'waiting_scan')
        self.assertTrue(self.client.qr_png().startswith(b'\x89PNG'))
        for url in ('https://account.bilibili.com.example.org/h5/account-h5/auth/scan-web?x=fixture',
                    'https://account.bilibili.com/another-page?x=fixture',
                    'https://account.bilibili.com@untrusted.example/h5/account-h5/auth/scan-web'):
            self.handler=lambda request,url=url:httpx.Response(200,json={'code':0,'data':{'qrcode_key':'fixture_qrcode_key_12345','url':url}})
            with self.assertRaises(BiliError):await self.client.start_login()

    async def test_size_request_and_wall_clock_budgets_prevent_unbounded_reads(self):
        budget = ReadBudget(requests=1)
        await self.client.api('/x/web-interface/view', budget=budget)
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view', budget=budget)
        self.assertEqual(len(self.requests), 1)
        self.handler = lambda request: httpx.Response(200, content=b'x'*100)
        with self.assertRaisesRegex(BiliError, '上限'):await self.client.get(API+'/x/web-interface/view', cap=20)
        expired = ReadBudget(seconds=-1)
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view', budget=expired)
        before = len(self.requests)
        with self.store.db:
            self.store.db.executemany('INSERT INTO bilibili_requests(created,operation) VALUES(?,?)',
                [(time.time(), 'api')]*self.config.settings.bilibili_requests_per_day)
        with self.assertRaises(BiliError):await self.client.api('/x/web-interface/view')
        self.assertEqual(len(self.requests), before)

    async def test_context_guard_interrupts_network_when_role_or_settings_change(self):
        def stopped():raise GrowthStopped('配置变化')
        with self.assertRaises(GrowthStopped):
            await self.client.api('/x/web-interface/view', budget=ReadBudget(guard=stopped))
        self.assertEqual(self.requests, [])

    def test_sensitive_auth_logging_is_filtered(self):
        log = logging.getLogger('httpx');old = log.level;log.setLevel(logging.INFO)
        output = io.StringIO();handler = logging.StreamHandler(output);log.addHandler(handler)
        try:
            log.info('GET '+PASSPORT+'/x/passport-login/web/qrcode/poll?qrcode_key=fixture_private')
            log.info('Set-Cookie: SESSDATA=fixture_private')
            self.assertNotIn('fixture_private', output.getvalue())
        finally:log.removeHandler(handler);log.setLevel(old)


class ReaderTests(unittest.TestCase):
    def test_subtitle_precheck_requires_a_real_caption_url(self):
        self.assertTrue(VideoReader.has_subtitle_track({'subtitle':{'subtitles':[{'subtitle_url':'https://caption.example/x'}]}}))
        self.assertFalse(VideoReader.has_subtitle_track({'subtitle':{'subtitles':[{'subtitle_url':''}]}}))
        self.assertFalse(VideoReader.has_subtitle_track(None))

    def test_timing_coverage_is_union_and_does_not_imply_full_understanding(self):
        segments = subtitle_segments([{'from': 0, 'to': 4, 'content': '一句合成内容'},
            {'from': 2, 'to': 6, 'content': '另一句合成内容'}], 10)
        self.assertEqual(subtitle_fraction(segments, 10), .6)
        with self.assertRaises(BiliError):subtitle_segments([{'from': 9, 'to': 99, 'content': '不符'}], 10)

    def test_frame_crop_uses_actual_sprite_positions(self):
        image = Image.new('RGB', (40, 20), 'red');image.paste('blue', (20, 0, 40, 20))
        raw = io.BytesIO();image.save(raw, 'PNG')
        frames = crop_frames(raw.getvalue(), [(1, 4.0)], 2, 1, 20, 20)
        self.assertEqual(frames[0]['time'], 4.0)
        with Image.open(io.BytesIO(frames[0]['jpeg'])) as frame:
            self.assertGreater(frame.getpixel((10, 10))[2], 200)


class GrowthIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_chat_habits as fixtures
        await fixtures.ChatHabitTests.asyncSetUp(self)
        self.loop = asyncio.get_running_loop();self.loop.slow_callback_duration=5
        self.config.save(self.config.settings.model_copy(update={'bilibili_enabled': True, 'bilibili_visual_enabled': False,
            'bilibili_danmaku_enabled': False, 'bilibili_comments_enabled': False, 'bilibili_feeds': ['search'],
            'growth_internet_model_profile':'current'}))
        self.calls=[];self.requests=[];self.empty=False;self.mismatch=False;self.bvid=BV;self.interest_delta=1.2
        self.bili=self.runtime.bilibili
        await self.bili.client.http.aclose()
        def handler(request):
            self.requests.append(request)
            if request.url.path.endswith('/nav'):
                return httpx.Response(200,json={'code':-101,'data':{'isLogin':False,'wbi_img':WBI}})
            if request.url.path.endswith('/search/type'):
                return httpx.Response(200,json={'code':0,'data':{'result':[{'bvid':self.bvid,'title':'街头演奏的一个下午'}]}})
            if request.url.path.endswith('/view'):
                return httpx.Response(200,json={'code':0,'data':{'bvid':self.bvid,'aid':1,'title':'街头演奏的一个下午',
                    'duration':60,'owner':{'name':'合成作者'},'pages':[{'cid':2,'page':1,'duration':60,'part':'第一段'}]}})
            if request.url.path.endswith('/wbi/v2'):
                return httpx.Response(200,json={'code':0,'data':{'bvid':self.bvid,'aid':1,'cid':99 if self.mismatch else 2,
                    'subtitle':{'subtitles':[] if self.empty else [{'lan':'zh-CN','subtitle_url':'https://aisubtitle.hdslb.com/bfs/ai_subtitle/fixture.json'}]}}})
            return httpx.Response(200,json={'body':[{'from':0,'to':50,'content':BODY}]})
        self.bili.client.http=httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=False)
        self.sleeper=patch('whalegirl_linux.bilibili_client.asyncio.sleep',new=AsyncMock());self.sleeper.start()
        outer=self
        class Engine:
            last_error='';usage_callback=None
            async def chat(self,messages,**options):
                data=json.loads(messages[0]['content']);outer.calls.append((data,options))
                if self.usage_callback:self.usage_callback({'operation':'learn','total_tokens':180,'input_tokens':120,'output_tokens':60})
                if 'available_feeds' in data:return json.dumps({'mode':'search','query':'街头音乐','question':'陌生人为什么会停下来听'})
                if 'candidates' in data:return json.dumps({'bvid':outer.bvid,'reason':'想听普通人的一天'})
                return json.dumps({'encounter':'读了街头音乐视频的部分字幕','facts':'路过的人会停下来听音乐','curiosity':'不同人会被什么旋律留住',
                    'insights':[{'category':'interest','topic':'街头音乐','interest_delta':outer.interest_delta,
                        'text':'最近想多了解街头音乐怎样进入普通人的生活' if outer.interest_delta>0 else '最近对街头音乐的好奇有所减弱，想先接触其他声音',
                        'replaces_id':data['existing'][0]['id'] if outer.interest_delta<0 and data['existing'] else 0,
                        'source_id':data['sources'][0]['id'],'evidence':BODY[:24],'conditions':'选择下一次公开阅读时'}]},ensure_ascii=False)
            async def close(self):pass
        self.runtime.engine_factory=lambda **kwargs:Engine()

    async def asyncTearDown(self):
        self.sleeper.stop()
        await self.runtime.close()

    async def test_real_source_evidence_drives_private_canonical_interest(self):
        before=self.config.settings.system_prompt_override
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        row=growth.snapshot()['runs'][0]
        self.assertEqual(row['state'],'done',row['error'])
        self.assertEqual(len(self.calls),3)
        self.assertEqual(self.bili.interests()[0]['topic'],'街头音乐')
        memory=self.runtime.memory_book.get('account',self.bili.interests()[0]['memory_id'],[INTERNET])
        self.assertEqual(memory['source_class'],'internet_learning')
        self.assertEqual(memory['chat'],INTERNET)
        self.assertNotEqual(memory['tier'],'core')
        self.assertEqual(self.config.settings.system_prompt_override,before)
        self.assertTrue(all(request.method=='GET' for request in self.requests))
        reading=self.bili.readings()[0]
        self.assertFalse(reading['coverage']['complete_playback'])
        first={'account':'account','chat':'group@chatroom'}
        context=self.bili.context(first,'最近刷B站看了什么')
        self.assertEqual(context,'')
        self.bili.record_delivery(first,{'bilibili_used_ids':[reading['id']]})
        self.assertEqual(self.bili.context(first,'最近刷B站看了什么'),'')
        self.assertNotIn(reading['id'],self.bili.context({'account':'account','chat':'other@chatroom'},'电脑装什么驱动'))

    async def test_title_and_comments_alone_never_become_watched_experience(self):
        self.empty=True
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        run=growth.snapshot()['runs'][0]
        self.assertEqual(run['state'],'done')
        self.assertTrue(any(event['stage']=='candidate_skipped' and event['details'].get('reason_code')=='subtitle_unavailable'
            for event in run['events']))
        self.assertEqual(self.bili.interests(),[])
        self.assertEqual(len(self.calls),1)

    async def test_empty_model_understanding_is_not_shown_as_understood(self):
        original=self.runtime.growth.reflect_sources
        async def empty(engine,generation,role,sources,topic,**kwargs):
            async def answer(*args,**options):return '{}'
            engine.chat=answer
            return await original(engine,generation,role,sources,topic,**kwargs)
        self.runtime.growth.reflect_sources=empty
        self.runtime.growth.request('bilibili');await self.runtime.growth.run_once()
        self.assertEqual(self.bili.readings()[0]['state'],'unconfirmed')
        self.assertEqual(self.bili.interests(),[])

    async def test_subtitle_identity_conflict_stops_before_downloading_or_learning(self):
        self.mismatch=True
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        self.assertEqual(growth.snapshot()['runs'][0]['state'],'failed')
        self.assertEqual(len(self.calls),1)
        self.assertFalse(any(request.url.host=='aisubtitle.hdslb.com' for request in self.requests))
        self.assertEqual(self.bili.interests(),[])

    async def test_disabling_source_retracts_interest_and_chat_material_without_deleting_history(self):
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        reading=self.bili.readings()[0]
        self.assertTrue(self.bili.interests())
        self.bili.set_reading_enabled(reading['id'],False)
        self.assertEqual(self.bili.interests(),[])
        self.assertEqual(self.bili.context({'account':'account','chat':'group@chatroom'},'最近在看什么'),'')
        self.assertEqual(len(self.bili.readings()),1)
        self.bili.set_reading_enabled(reading['id'],True)
        self.assertEqual(self.bili.interests(),[])

    async def test_role_isolation_and_no_model_calls_in_reply_material_lookup(self):
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        calls=len(self.calls)
        self.bili.context({'account':'account','chat':'group@chatroom'},'最近在看什么')
        self.assertEqual(len(self.calls),calls)
        self.config.save(self.config.settings.model_copy(update={'active_persona_id':'another-role'}))
        self.assertEqual(self.bili.interests(),[])
        self.assertEqual(self.bili.readings(),[])

    async def test_regular_and_proactive_replies_never_receive_bilibili_experience(self):
        import test_chat_habits as fixtures
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        reading=self.bili.readings()[0]
        first={'account':'account','chat':'group@chatroom'}
        for proactive in (False,True):
            self.assertNotIn(reading['id'],growth.prompt(first,[{'content':'最近刷B站看了什么'}],proactive=proactive))
        event=fixtures.ChatHabitTests.event(self,content='鲸鱼娘，最近刷B站看了什么')
        fixtures.ChatHabitTests.queue(self,event)
        engine=await self.runtime.model();seen=[]
        async def reply(messages,**options):
            seen.append(options['system_prompt']+json.dumps(messages,ensure_ascii=False))
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'刚刚歇了一会儿'}],
                'bilibili_used_ids':[reading['id']]},ensure_ascii=False)
        engine.chat=reply
        await self.runtime.process_next()
        self.assertEqual(len(seen),1)
        self.assertNotIn(reading['id'],seen[0])
        self.assertNotIn('最近想多了解街头音乐怎样进入普通人的生活',seen[0])
        self.assertGreaterEqual(len(self.gateway.sent),1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_shares').fetchone()[0],0)

    async def test_new_evidence_challenges_old_interest_without_replacing_its_history(self):
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        old=self.bili.interests()[0]['memory_id']
        self.bvid='BV1234567891';self.interest_delta=-2
        growth.request('bilibili');await growth.run_once()
        self.assertEqual(growth.snapshot()['runs'][0]['state'],'done')
        self.assertLess(self.bili.interests()[0]['strength'],0)
        previous=self.runtime.memory_book.get('account',old,[INTERNET])
        self.assertEqual(previous['status'],'active')
        state=growth.policy.personality_state('interest','街头音乐')
        self.assertEqual(state['status'],'challenged')
        self.assertEqual((state['support_count'],state['challenge_count']),(1,1))
        self.assertTrue(self.bili.schedule()['curiosity'])

    async def test_bilibili_memory_tools_and_daily_growth_are_bidirectionally_isolated(self):
        book=self.runtime.memory_book;growth=self.runtime.growth
        daily=book.change('account',[WORLD],{'chat':WORLD,'subject':'self','kind':'persona','content':'日常私有观察样本，不应该输入B站反思',
            'source_key':'world:daily-fixture','tier':'ordinary','venue':'any'})
        with self.store.db:
            self.store.db.execute("UPDATE memories SET source_class='world_learning' WHERE id=?",(daily['id'],))
            self.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',(daily['id'],'daily-fixture-journal','persona','日常测试','','',time.time()))
        growth.request('bilibili');await growth.run_once()
        row=self.bili.snapshot()['insights'][0]
        self.assertNotIn(daily['content'],json.dumps(self.calls,ensure_ascii=False))
        self.assertEqual([item['id'] for item in growth.memories()],[row['id']])
        self.assertEqual(growth.search_memories('街头音乐'),[])
        self.assertIsNone(book.get('account',row['id'],['group@chatroom']))
        results=book.model_ops('account',['group@chatroom'],[{'op':'get','id':row['id']},{'op':'search','query':'街头音乐'}],[],True,chat='group@chatroom')
        self.assertIsNone(results[0]['result']);self.assertEqual(results[1]['result'],[])
        self.assertFalse(self.runtime.memory_hub.visible(row,'group@chatroom',[],True))
        edited=growth.edit(row['id'],{'op':'update','version':row['version'],'content':'只在B站范围保留的新认识'})
        self.assertEqual(edited['chat'],INTERNET)
        self.assertIn(row['id'],[item['id'] for item in growth.snapshot()['insights']])

    async def test_legacy_bilibili_memory_migration_is_audited_idempotent_and_does_not_delete(self):
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        row=self.bili.snapshot()['insights'][0]
        with self.store.db:self.store.db.execute("UPDATE memories SET chat=?,source_class='world_learning' WHERE id=?",(WORLD,row['id']))
        self.assertEqual(self.bili.isolate_memories(),1)
        self.assertEqual(self.bili.isolate_memories(),0)
        after=self.runtime.memory_book.get('account',row['id'],[INTERNET])
        self.assertEqual(after['content'],row['content']);self.assertEqual(after['status'],row['status'])
        self.assertEqual(after['version'],row['version']+1)
        self.assertIsNone(self.runtime.memory_book.get('account',row['id'],['group@chatroom']))
        revision=self.store.db.execute("SELECT id FROM memory_revisions WHERE memory_id=? AND operation='internet_scope_migration'",(row['id'],)).fetchone()
        self.assertIsNotNone(revision)
        with self.assertRaises(ValueError):self.runtime.memory_book.undo('account',['group@chatroom'],revision[0])
        self.assertEqual(len(self.bili.readings()),1)

    async def test_retired_global_budget_does_not_stop_bilibili(self):
        self.config.save(self.config.settings.model_copy(update={'growth_daily_tokens':0}))
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        self.assertEqual(growth.snapshot()['runs'][0]['state'],'done')
        self.assertEqual(len(self.calls),3)
        self.assertGreater(len(self.requests),0)

    async def test_video_count_and_total_read_cap_do_not_block_bilibili(self):
        self.config.save(self.config.settings.model_copy(update={'growth_reads_per_day':0}))
        with self.store.db:
            for index in range(30):
                self.store.db.execute('INSERT INTO bilibili_readings(id,account,role,bvid,created,state) VALUES(?,?,?,?,?,?)',
                    ('past-'+str(index),'account',self.runtime.memory_hub.role(),'BV0000000000',time.time(),'done'))
        self.bili.schedule()
        with self.store.db:self.store.db.execute('UPDATE bilibili_schedule SET next_at=0')
        self.assertTrue(self.bili.due())
        growth=self.runtime.growth;growth.request('bilibili');await growth.run_once()
        self.assertEqual(growth.runs()[0]['state'],'done')
        self.assertEqual(growth.runs()[0]['actual_readings'],1)
        self.assertTrue(self.bili.snapshot()['video_limit_removed'])
        self.assertIsNone(self.bili.snapshot()['read_limit'])

    async def test_network_request_protection_remains_without_paying_for_a_model_choice(self):
        with self.store.db:self.store.db.executemany('INSERT INTO bilibili_requests(created,operation) VALUES(?,?)',
            [(time.time(),'api')]*self.config.settings.bilibili_requests_per_day)
        requested=self.runtime.growth.request('bilibili')
        self.assertEqual(requested['state'],'skipped');self.assertIn('网络请求保护',requested['reason'])
        self.assertEqual(self.calls,[]);self.assertFalse(self.bili.client.status()['paused'])

    async def test_pending_qr_stops_background_and_private_feeds_require_login(self):
        self.bili.client.qr={'state':'waiting_confirm','expires':time.time()+60}
        self.assertFalse(self.bili.available())
        self.assertEqual(self.runtime.growth.request('bilibili')['state'],'skipped')
        self.bili.client.qr=None
        self.config.save(self.config.settings.model_copy(update={'bilibili_feeds':['history']}))
        with self.assertRaisesRegex(BiliError,'登录'):await self.bili.feed('history','',ReadBudget())
        self.assertEqual(self.requests,[])

    async def test_platform_pause_also_blocks_world_source_fallback_without_pausing_wechat(self):
        self.bili.client.pause('http_412')
        source=self.runtime.growth.save_source({'url':'https://www.bilibili.com/video/'+BV+'/',
            'title':'不能通过网页来源绕过暂停','content':BODY,'kind':'web'},self.runtime.memory_hub.role())
        self.assertIsNone(source)
        self.assertIsNone(self.runtime.growth.save_source({'url':'https://example.org/allowed',
            'title':'其他网页保持可读','content':BODY},self.runtime.memory_hub.role()))
        self.assertTrue(self.bili.alert())
        self.assertFalse(self.runtime.paused_reason)

    async def test_favorites_model_selects_only_from_actual_account_folders(self):
        self.config.save(self.config.settings.model_copy(update={'bilibili_feeds':['favorites']}))
        self.bili.client.save_cookies({'SESSDATA':'fixture-session-only','DedeUserID':'12345'})
        self.bili.client.set_meta(session_state='logged_in')
        api=AsyncMock(side_effect=[{'list':[{'id':1,'title':'音乐'},{'id':2,'title':'日常'}]},
                                  {'medias':[{'bvid':BV,'title':'日常作品'}]}])
        with patch.object(self.bili.client,'api',api):
            rows=await self.bili.feed('favorites','',ReadBudget(),choose_folder=AsyncMock(return_value='2'))
        self.assertEqual(rows[0]['bvid'],BV)
        self.assertEqual(api.await_args_list[1].args[1]['media_id'],'2')
        invalid=AsyncMock(return_value={'list':[{'id':1,'title':'音乐'},{'id':2,'title':'日常'}]})
        with patch.object(self.bili.client,'api',invalid),self.assertRaises(BiliError):
            await self.bili.feed('favorites','',ReadBudget(),choose_folder=AsyncMock(return_value='999'))
        self.assertEqual(invalid.await_count,1)

    async def test_comment_mention_monitor_baselines_latest_page_and_deduplicates_exact_video(self):
        self.config.save(self.config.settings.model_copy(update={
            'enabled':True,'bilibili_enabled':True,'bilibili_comment_mentions_enabled':True,
            'growth_internet_model_profile':'current'}))
        self.bili.client.save_cookies({'SESSDATA':'fixture-session-only','DedeUserID':'12345'})
        self.bili.client.set_meta(session_state='logged_in')
        old={'id':10,'business_id':1,
            'item':{'uri':'https://www.bilibili.com/video/'+BV+'/?p=1','business_id':1,'subject_id':98765}}
        unrelated={'id':11,'business_id':2,
            'item':{'uri':'https://www.bilibili.com/video/'+BV+'/?p=1','business_id':2,'subject_id':98765}}
        new={'id':12,'business_id':1,
            'item':{'uri':'https://www.bilibili.com/video/'+BV+'/?p=2','business_id':1,'subject_id':98765}}
        pages=AsyncMock(side_effect=[
            {'items':[old]},
            {'items':[new,unrelated,old],'cursor':{'id':10,'time':100,'is_end':False}},
            {'items':[new,unrelated,old]},
            {'items':[old],'cursor':{'id':10,'time':100,'is_end':True}},
        ])
        with patch.object(self.bili.client,'api',pages):
            self.assertTrue(await self.bili.poll_mentions(force=True))
            self.assertEqual(self.store.db.execute('SELECT state FROM bilibili_at_events WHERE event_id="10"').fetchone()[0],'baseline')
            self.assertTrue(await self.bili.poll_mentions(force=True))
            self.assertTrue(await self.bili.poll_mentions(force=True))
        calls=pages.await_args_list
        self.assertEqual(calls[0].args[0],'/x/msgfeed/at')
        self.assertEqual(calls[0].args[1],{'id':0,'like_time':0,'platform':'web','build':0,'mobi_app':'web'})
        self.assertEqual(calls[2].args[1],calls[0].args[1])
        self.assertEqual(calls[3].args[1],{'id':'10','like_time':100,'platform':'web','build':0,'mobi_app':'web'})
        event=self.store.db.execute('SELECT state,bvid,run_id FROM bilibili_at_events WHERE event_id="12"').fetchone()
        self.assertEqual(event['state'],'queued')
        self.assertEqual(event['bvid'],BV)
        run=self.store.db.execute('SELECT result FROM growth_runs WHERE id=?',(event['run_id'],)).fetchone()
        request=json.loads(run['result'])['request']
        self.assertEqual(request['target'],{'bvid':BV,'page':2})
        self.assertEqual(request['origin'],'bilibili_comment_mention')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM growth_runs WHERE target_key!=\'\'').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT state FROM bilibili_at_events WHERE event_id="11"').fetchone()[0],'ignored')

    def test_comment_at_feed_uses_video_subject_and_trusted_url_without_echo_fields(self):
        self.assertEqual(self.bili._at_video_ref({'item':{'uri':'https://bilibili.com.evil.example/video/'+BV+'/'}}),None)
        self.assertEqual(self.bili._at_video_ref({'item':{'uri':'http://www.bilibili.com/video/'+BV+'/'}}),None)
        self.assertEqual(self.bili._at_video_ref({'item':{'uri':'https://www.bilibili.com/video/'+BV+'/?p=3'}}),
            {'bvid':BV,'page':3})
        self.assertEqual(self.bili._at_video_target({'business_id':1,'item':{'subject_id':98765}}),{'aid':'98765'})
        self.assertIsNone(self.bili._at_video_target({'business_id':2,'item':{'subject_id':98765}}))
        self.assertIsNone(self.bili._at_video_target({'business_id':1,'item':{'subject_id':'not-an-id'}}))

    def test_explicit_same_video_requests_from_distinct_wechat_sources_do_not_merge(self):
        self.config.save(self.config.settings.model_copy(update={'enabled':True,'allowed_chat_ids':['group-a','group-b'],
            'bilibili_enabled':True,'bilibili_wechat_video_enabled':True}))
        first=self.runtime.growth.request('bilibili',target={'bvid':BV},origin='wechat_video',
            requester={'account':'account','chat':'group-a','sender_id':'person-a','source_key':'source-a'})
        repeated=self.runtime.growth.request('bilibili',target={'bvid':BV},origin='wechat_video',
            requester={'account':'account','chat':'group-a','sender_id':'person-a','source_key':'source-a'})
        second=self.runtime.growth.request('bilibili',target={'bvid':BV},origin='wechat_video',
            requester={'account':'account','chat':'group-b','sender_id':'person-b','source_key':'source-b'})
        self.assertEqual(repeated['id'],first['id'])
        self.assertNotEqual(second['id'],first['id'])

    def test_wechat_result_notice_is_not_queued_after_quiet_or_permission_revocation(self):
        self.config.save(self.config.settings.model_copy(update={'enabled':True,'allowed_chat_ids':['group@chatroom'],
            'bilibili_enabled':True,'bilibili_wechat_video_enabled':True}))
        requester={'account':'account','chat':'group@chatroom','sender_id':'person-1','source_key':'source-key'}
        self.runtime.chat_control.apply('group@chatroom','quiet',0)
        self.store.observe([{'key':'source-key','account':'account','chat':'group@chatroom','sender_id':'person-1',
            'sender':'小甲','kind':1,'is_self':0,'local_id':101,'content':'@鲸鱼娘看看视频',
            'timestamp':time.time()+1,'payload_json':json.dumps({'image_address':{'mentioned_me':True}})}])
        self.assertFalse(self.bili.queue_completion('1'*32,requester,{'journal_id':'journal'}))
        self.config.save(self.config.settings.model_copy(update={'allowed_chat_ids':['another-chat']}))
        self.assertFalse(self.bili.queue_completion('2'*32,requester,{'journal_id':'journal'}))

    def test_explicit_wechat_video_tool_uses_current_mention_and_link_or_quoted_link(self):
        self.config.save(self.config.settings.model_copy(update={'bilibili_wechat_video_enabled':True}))
        first={'account':'account','chat':'group@chatroom','key':'source-key'}
        message={'trigger_text':'@鲸鱼娘 https://www.bilibili.com/video/'+BV+'/?p=2','payload_json':json.dumps({
            'image_address':{'mentioned_me':True}})}
        result=self.bili.tools.explicit_video_request(first,[message])
        self.assertEqual(result['origin'],'wechat_video')
        self.assertEqual(result['reference']['url'],'https://www.bilibili.com/video/'+BV+'/?p=2')
        message={'trigger_text':'@鲸鱼娘','payload_json':json.dumps({
            'image_address':{'mentioned_me':True},'reply':{'content':'https://www.bilibili.com/video/'+BV+'/?p=2'}})}
        result=self.bili.tools.explicit_video_request(first,[message])
        self.assertEqual(result['reference']['url'],'https://www.bilibili.com/video/'+BV+'/?p=2')
        message['payload_json']=json.dumps({'image_address':{'mentioned_me':False},'reply':{'content':'https://www.bilibili.com/video/'+BV+'/'}})
        self.assertIsNone(self.bili.tools.explicit_video_request(first,[message]))

    async def test_wechat_result_notice_is_verified_and_not_saved_as_a_user_turn(self):
        self.config.save(self.config.settings.model_copy(update={'enabled':True,'allowed_chat_ids':['group@chatroom'],
            'bilibili_enabled':True,'bilibili_wechat_video_enabled':True,'growth_internet_model_profile':'current'}))
        source_key='account/group@chatroom/101'
        source={'key':source_key,'account':'account','chat':'group@chatroom','sender_id':'person-1','sender':'小甲',
            'kind':1,'is_self':0,'local_id':101,'content':'@鲸鱼娘看看这个视频','timestamp':time.time()-40,
            'payload_json':json.dumps({'image_address':{'mentioned_me':True}})}
        self.store.observe([source])
        requester={'account':'account','chat':'group@chatroom','sender_id':'person-1','source_key':source_key}
        queued=self.runtime.growth.request('bilibili',target={'bvid':BV},origin='wechat_video',requester=requester)
        role=self.runtime.memory_hub.role();run_id=queued['id'];journal_id='fixture-journal';reading_id='fixture-reading'
        body={'encounter':'读到一段街头演奏的字幕材料。','facts':'画面和字幕显示有人停下来听。',
            'author_view':'作者认为音乐能改变普通下午的气氛。','missing':'没有覆盖视频后半部分。'}
        coverage={'segment_start':0,'segment_end':30,'subtitle_segments':8,'visual_understood':True}
        with self.store.db:
            self.store.db.execute("UPDATE growth_runs SET state='done',result=? WHERE id=?",
                (json.dumps({'request':{'origin':'wechat_video','requester':requester,'target':{'bvid':BV}}},ensure_ascii=False),run_id))
            self.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
                (journal_id,run_id,'account',role,'音乐','[]',json.dumps(body,ensure_ascii=False),time.time()))
            self.store.db.execute('INSERT INTO bilibili_readings(id,account,role,bvid,created,state,metadata,coverage,journal_id) VALUES(?,?,?,?,?,?,?,?,?)',
                (reading_id,'account',role,BV,time.time(),'done',json.dumps({'title':'街头演奏','bvid':BV}),
                    json.dumps(coverage),journal_id))
        self.assertTrue(self.bili.queue_completion(run_id,requester,{'journal_id':journal_id,'reading_id':reading_id}))
        first=self.store.queue()
        material=self.bili.completion_payload(first)
        self.assertEqual(material['title'],'街头演奏')
        self.assertIn('没有覆盖视频后半部分',material['missing'])
        calls=[]
        class ReplyEngine:
            api_style='test';stream_enabled=False;last_error='';last_response_meta={}
            async def chat(self,messages,**options):
                calls.append((messages,options))
                return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'这段街头演奏还挺有意思的。'}]},ensure_ascii=False)
            async def close(self):pass
        with patch.object(self.runtime,'model',new=AsyncMock(return_value=ReplyEngine())):
            self.assertTrue(await self.runtime.process_next())
        self.assertEqual(len(calls),1)
        self.assertIn('你先前受托读取的视频结果',calls[0][1]['system_prompt'])
        self.assertIn('没有覆盖视频后半部分',calls[0][1]['system_prompt'])
        self.assertEqual(self.gateway.sent,[('text','这段街头演奏还挺有意思的')])
        history=self.store.db.execute('SELECT role,content FROM history WHERE account=? AND chat=? ORDER BY id',
            ('account','group@chatroom')).fetchall()
        self.assertEqual([row['role'] for row in history],['assistant'])
        self.assertIsNone(self.store.db.execute('SELECT 1 FROM message_log WHERE key=?',(first['key'],)).fetchone())
        self.assertEqual(self.store.db.execute('SELECT state FROM bilibili_completion_notices WHERE run_id=?',(run_id,)).fetchone()[0],'sent')


class ConsoleAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from tests.test_chat_habits import Gateway
        path=ROOT/'test-runs';path.mkdir(exist_ok=True)
        self.app=create_app(Path(tempfile.mkdtemp(prefix='bilibili-api-',dir=path)),gateway=Gateway(),run_background=False)
        self.app.state.config.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom']))
        self.client=TestClient(self.app)
        self.headers={'Authorization':'Bearer '+self.app.state.config.admin_token}

    def tearDown(self):
        self.client.close()
        asyncio.run(self.app.state.runtime.close())

    def test_account_and_sources_are_admin_only_and_cookies_cannot_be_uploaded(self):
        self.assertEqual(self.client.get('/api/bilibili').status_code,401)
        self.assertEqual(self.client.post('/api/bilibili/account',json={'action':'login'}).status_code,401)
        response=self.client.get('/api/bilibili',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['account']['read_only'])
        self.assertEqual(self.client.post('/api/bilibili/account',headers=self.headers,
            json={'action':'login','cookies':{'SESSDATA':'fixture'}}).status_code,422)
        self.assertEqual(self.client.post('/api/bilibili/account',headers=self.headers,json={'action':'like'}).status_code,422)

    def test_resume_requires_explicit_admin_acknowledgement(self):
        self.app.state.runtime.bilibili.client.pause('fixture-risk')
        self.assertEqual(self.client.post('/api/bilibili/account',headers=self.headers,json={'action':'resume'}).status_code,400)
        self.assertEqual(self.client.post('/api/bilibili/account',headers=self.headers,
            json={'action':'resume','acknowledge':True}).status_code,200)
        self.assertFalse(self.app.state.runtime.bilibili.client.status()['paused'])


class WbiBuvidTests(unittest.IsolatedAsyncioTestCase):
    async def test_wbi_api_auto_ensures_buvid_when_cookie_lacks_it(self):
        directory = ROOT / 'test-runs'
        directory.mkdir(exist_ok=True)
        tmp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(tmp.cleanup)
        config = Config(Path(tmp.name))
        config.save(Settings(account_id='account', bilibili_enabled=True))
        store = Store(Path(tmp.name) / 'state.sqlite3')
        self.addCleanup(store.db.close)

        requests = []

        def handler(request):
            requests.append(request)
            path = request.url.path
            if path == '/x/web-interface/nav':
                return httpx.Response(200, json={'code': 0, 'data': {'isLogin': True, 'uname': '肥肥鲸', 'wbi_img': WBI}})
            if path == '/x/frontend/finger/spi':
                return httpx.Response(200, json={'code': 0, 'data': {'b_3': 'AUTO-BUVID3', 'b_4': 'AUTO-BUVID4'}})
            if path == '/x/web-interface/wbi/index/top/feed/rcmd':
                return httpx.Response(200, json={'code': 0, 'data': {'item': [{'bvid': BV, 'title': '测试推荐'}]}})
            return httpx.Response(200, json={'code': 0, 'data': {}})

        client = BilibiliClient(config, store, transport=httpx.MockTransport(handler))
        with patch('whalegirl_linux.bilibili_client.asyncio.sleep', new=AsyncMock()):
            try:
                client.save_cookies({'SESSDATA': 'sess', 'bili_jct': 'jct', 'DedeUserID': '1234'})
                client.set_meta(session_state='logged_in')
                data = await client.api('/x/web-interface/wbi/index/top/feed/rcmd', {'fresh_type': 3}, wbi=True)
                self.assertEqual(data['item'][0]['bvid'], BV)
                rcmd_reqs = [r for r in requests if r.url.path == '/x/web-interface/wbi/index/top/feed/rcmd']
                self.assertEqual(len(rcmd_reqs), 1)
                self.assertIn('SESSDATA=sess', rcmd_reqs[0].headers.get('Cookie', ''))
                self.assertIn('buvid3=AUTO-BUVID3', rcmd_reqs[0].headers.get('Cookie', ''))
                self.assertIn('buvid4=AUTO-BUVID4', rcmd_reqs[0].headers.get('Cookie', ''))
                self.assertIn('w_rid', rcmd_reqs[0].url.params)
                # Second WBI call reuses cached _anon_buvid without hitting /spi again
                await client.api('/x/web-interface/wbi/index/top/feed/rcmd', {'fresh_type': 3}, wbi=True)
                spi_reqs = [r for r in requests if r.url.path == '/x/frontend/finger/spi']
                self.assertEqual(len(spi_reqs), 1)
            finally:
                await client.close()


if __name__=='__main__':unittest.main()

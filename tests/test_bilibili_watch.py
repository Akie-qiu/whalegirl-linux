"""Tests for schedule-aware HoYoverse Bilibili watch, digest, delivery, API, and 4-layer anti-spoiler gates."""
import asyncio
from datetime import datetime
import inspect
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from tests.test_chat_habits import Engine, Gateway
from whalegirl_linux.app import create_app
from whalegirl_linux.bilibili_client import BilibiliClient, ReadBudget
from whalegirl_linux.bilibili_watch import (
    BilibiliWatchService,
    SHANGHAI_TZ,
    calendar_schedule,
    clean_watch_image_url,
    detect_game,
    extract_version_cue,
)
from whalegirl_linux.growth import GrowthService
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, DEFAULT_BILIBILI_WATCH_CALENDARS, Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]
_REAL_ASYNCIO_SLEEP = asyncio.sleep


async def _fast_watch_sleep(delay=0, *args, **kwargs):
    """Skip sleep only for bilibili client/watch throttling without accumulating AsyncMock calls or breaking runtime loops."""
    frame = inspect.currentframe()
    caller = frame.f_back.f_code.co_filename if (frame and frame.f_back) else ''
    if caller.endswith(('bilibili_client.py', 'bilibili_watch.py')):
        return await _REAL_ASYNCIO_SLEEP(0)
    return await _REAL_ASYNCIO_SLEEP(delay, *args, **kwargs)


def _make_png_bytes(color=(30, 120, 220), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color=color).save(buf, format='PNG')
    return buf.getvalue()


def _ts_shanghai(year: int, month: int, day: int, hour: int, minute: int) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI_TZ).timestamp()


class WatchCalendarAndParserTests(unittest.TestCase):
    def test_calendar_schedule_peak_mini_and_quiet_modes(self):
        cals = dict(DEFAULT_BILIBILI_WATCH_CALENDARS)
        # 2026-11-04 is Genshin 7.2 anchor patch day -> 12:00 CST must be peak (150s)
        peak_patch = calendar_schedule(cals, now_ts=_ts_shanghai(2026, 11, 4, 12, 0))
        self.assertEqual(peak_patch['mode'], 'peak')
        self.assertEqual(peak_patch['interval_seconds'], 150)
        self.assertTrue(peak_patch['games']['genshin']['peak_now'])

        # Star Rail 4.7 anchor 2026-11-11 -> preview 12 days prior = 2026-10-30 at 20:00 CST -> peak (150s)
        peak_preview = calendar_schedule(cals, now_ts=_ts_shanghai(2026, 10, 30, 20, 0))
        self.assertEqual(peak_preview['mode'], 'peak')
        self.assertEqual(peak_preview['interval_seconds'], 150)
        self.assertTrue(peak_preview['games']['starrail']['peak_now'])

        # Quiet off-window day at 12:05 CST -> mini_check (300s)
        mini = calendar_schedule(cals, now_ts=_ts_shanghai(2026, 11, 7, 12, 5))
        self.assertEqual(mini['mode'], 'mini_check')
        self.assertEqual(mini['interval_seconds'], 300)

        # Quiet off-window night at 23:10 CST -> quiet (2400s)
        quiet = calendar_schedule(cals, now_ts=_ts_shanghai(2026, 11, 7, 23, 10))
        self.assertEqual(quiet['mode'], 'quiet')
        self.assertEqual(quiet['interval_seconds'], 2400)

    def test_clean_url_game_detection_and_version_cues(self):
        self.assertEqual(
            clean_watch_image_url('//i0.hdslb.com/bfs/new_dyn/hero.png@1192w.webp?from=web'),
            'https://i0.hdslb.com/bfs/new_dyn/hero.png',
        )
        self.assertEqual(clean_watch_image_url('javascript:alert(1)'), '')
        self.assertEqual(detect_game('绝区零 星见雅 代理人档案 新艾利都'), 'zzz')
        self.assertEqual(detect_game('崩坏：星穹铁道 天外卫星通信 翁法罗斯'), 'starrail')
        self.assertEqual(extract_version_cue('《原神》「7.3」版本前瞻特别节目预告'), ('7.3', 'preview'))
        self.assertEqual(extract_version_cue('崩坏：星穹铁道 4.8 版本更新维护预告'), ('4.8', 'patch'))

    def test_parse_dynamic_item_formats_and_noise_filter(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='watch-parse-', dir=runs))
        config = Config(folder)
        config.save(
            Settings(
                enabled=True,
                account_id='account',
                allowed_chat_ids=['group@chatroom'],
                bilibili_enabled=True,
                bilibili_watch_enabled=True,
            ),
            'test-key',
        )
        store = Store(folder / 'state.sqlite3')
        gateway = Gateway()
        runtime = Runtime(config, store, gateway, ROOT / 'styles', engine_factory=Engine)
        watch = runtime.bilibili.watch
        target_sr = {'mid': '1340190821', 'name': '崩坏星穹铁道', 'game': 'starrail', 'kind': 'official', 'enabled': True}
        target_gs = {'mid': '401742377', 'name': '原神', 'game': 'genshin', 'kind': 'official', 'enabled': True}
        try:
            opus_art = {
                'id_str': '100001',
                'modules': {
                    'module_author': {'mid': 1340190821, 'name': '崩坏星穹铁道', 'pub_ts': 1763000000},
                    'module_dynamic': {
                        'desc': {'text': '天外卫星通信 | 昔涟'},
                        'major': {
                            'type': 'MAJOR_TYPE_OPUS',
                            'opus': {
                                'title': '天外卫星通信｜昔涟',
                                'summary': {'text': '「这一次，我会站在你的身边。」冰属性记忆命途新角色。'},
                                'jump_url': '//www.bilibili.com/opus/100001',
                                'pics': [{'url': '//i0.hdslb.com/bfs/opus/cyrene.png@600w.webp'}],
                            },
                        },
                    },
                },
            }
            parsed = watch.parse_dynamic_item(opus_art, target_sr)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed['kind'], 'character_art')
            self.assertEqual(parsed['game'], 'starrail')
            self.assertEqual(parsed['image_urls'], ['https://i0.hdslb.com/bfs/opus/cyrene.png'])

            # Livestream (>25 min) notice for Genshin 7.3 preview auto-calibrates version & anchor
            livestream = {
                'id_str': '100002',
                'modules': {
                    'module_author': {'mid': 401742377, 'name': '原神', 'pub_ts': _ts_shanghai(2026, 12, 4, 12, 0)},
                    'module_dynamic': {
                        'desc': {'text': '《原神》「7.3」版本前瞻特别节目将于12月4日开启！'},
                        'major': {
                            'type': 'MAJOR_TYPE_ARCHIVE',
                            'archive': {
                                'bvid': 'BV173preview1',
                                'aid': '999001',
                                'title': '《原神》7.3版本前瞻特别节目预告',
                                'desc': '12月4日20:00直播',
                                'duration_text': '46:20',
                                'cover': 'https://i1.hdslb.com/bfs/archive/gs73.jpg@400w.webp',
                            },
                        },
                    },
                },
            }
            parsed_live = watch.parse_dynamic_item(livestream, target_gs)
            self.assertIsNotNone(parsed_live)
            self.assertEqual(parsed_live['kind'], 'livestream_notice')
            self.assertEqual(parsed_live['version'], '7.3')
            self.assertEqual(parsed_live['calendar_cue'], 'preview')
            self.assertTrue(watch.maybe_calibrate_calendar(parsed_live))
            self.assertEqual(config.settings.bilibili_watch_calendars['genshin']['version'], '7.3')
            self.assertEqual(config.settings.bilibili_watch_calendars['genshin']['anchor_date'], '2026-12-16')

            # Forward & prize noise are skipped
            noise = {
                'id_str': '100003',
                'modules': {
                    'module_author': {'mid': 401742377, 'name': '原神', 'pub_ts': 1763000100},
                    'module_dynamic': {
                        'desc': {'text': '7.2版本二创征集获奖公示及周边上新中奖名单'},
                        'major': {'type': 'MAJOR_TYPE_DRAW', 'draw': {'items': [{'src': 'https://i0.hdslb.com/bfs/new_dyn/noise.png'}]}},
                    },
                },
            }
            self.assertIsNone(watch.parse_dynamic_item(noise, target_gs))
            forward = {'id_str': '100004', 'modules': {'module_dynamic': {'major': {'type': 'MAJOR_TYPE_FORWARD'}}}}
            self.assertIsNone(watch.parse_dynamic_item(forward, target_gs))

            # Cutscene archive video must be marked as story-sensitive PV (never treated as safe character art)
            cutscene = {
                'id_str': '100005',
                'modules': {
                    'module_author': {'mid': 401742377, 'name': '原神', 'pub_ts': 1763000200},
                    'module_dynamic': {
                        'desc': {'text': '《原神》过场动画-「高天的门扉」'},
                        'major': {
                            'type': 'MAJOR_TYPE_ARCHIVE',
                            'archive': {
                                'bvid': 'BV1cut73001',
                                'aid': '999002',
                                'title': '《原神》过场动画-「高天的门扉」',
                                'desc': '主线剧情过场',
                                'duration_text': '02:35',
                                'cover': 'https://i1.hdslb.com/bfs/archive/cut.jpg',
                            },
                        },
                    },
                },
            }
            parsed_cut = watch.parse_dynamic_item(cutscene, target_gs)
            self.assertIsNotNone(parsed_cut)
            self.assertEqual(parsed_cut['kind'], 'pv')
            self.assertTrue(parsed_cut['is_story'])

            # ZZZ raffle / birthday wallpaper post mentioning 代理人 must still be skipped
            target_zzz = {'mid': '1636034895', 'name': '绝区零', 'game': 'zzz', 'kind': 'official', 'enabled': True}
            zzz_raffle = {
                'id_str': '100006',
                'modules': {
                    'module_author': {'mid': 1636034895, 'name': '绝区零', 'pub_ts': 1763000300},
                    'module_dynamic': {
                        'desc': {'text': '互动抽奖 #生日快乐# 代理人纪念壁纸分享，关注并转发抽取周边好礼！'},
                        'major': {
                            'type': 'MAJOR_TYPE_DRAW',
                            'draw': {'items': [{'src': 'https://i0.hdslb.com/bfs/new_dyn/zzz_bday.png'}]},
                        },
                    },
                },
            }
            self.assertIsNone(watch.parse_dynamic_item(zzz_raffle, target_zzz))
        finally:
            asyncio.run(runtime.close())


class WatchClientSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='watch-sec-', dir=runs))
        self.config = Config(self.folder)
        self.config.save(Settings(account_id='account', bilibili_enabled=True))
        self.store = Store(self.folder / 'state.sqlite3')
        self.requests = []
        self.png = _make_png_bytes()

        def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path == '/x/frontend/finger/spi':
                return httpx.Response(200, json={'code': 0, 'data': {'b_3': 'ANON-BUVID3-TOKEN', 'b_4': 'ANON-BUVID4-TOKEN'}})
            if path == '/x/web-interface/nav':
                return httpx.Response(
                    200,
                    json={
                        'code': 0,
                        'data': {
                            'isLogin': False,
                            'wbi_img': {
                                'img_url': 'https://i0.hdslb.com/bfs/wbi/' + 'a' * 32 + '.png',
                                'sub_url': 'https://i0.hdslb.com/bfs/wbi/' + 'b' * 32 + '.png',
                            },
                        },
                    },
                )
            if path == '/x/polymer/web-dynamic/v1/feed/space':
                return httpx.Response(200, json={'code': 0, 'data': {'items': []}})
            if str(request.url.host) == 'aisubtitle.hdslb.com':
                return httpx.Response(200, json={'body': [{'from': 0.0, 'to': 2.0, 'content': '测试字幕'}]})
            return httpx.Response(200, content=self.png, headers={'Content-Type': 'image/png'})

        self.client = BilibiliClient(self.config, self.store, transport=httpx.MockTransport(handler))
        self.client.save_cookies({'SESSDATA': 'watch-secret-cookie', 'bili_jct': 'csrf', 'DedeUserID': '1234'})

    async def asyncTearDown(self):
        await self.client.close()
        self.store.close()

    async def test_watch_image_allowlist_blocks_untrusted_hosts_and_strips_cookies(self):
        self.assertTrue(self.client.permitted('https://i0.hdslb.com/bfs/new_dyn/splash.png', 'watch_image'))
        self.assertFalse(self.client.permitted('https://i0.hdslb.com/bfs/face/avatar.png', 'watch_image'))
        self.assertFalse(self.client.permitted('http://i0.hdslb.com/bfs/new_dyn/splash.png', 'watch_image'))
        self.assertFalse(self.client.permitted('https://evil.example/bfs/new_dyn/splash.png', 'watch_image'))

        raw = await self.client.get('https://i0.hdslb.com/bfs/new_dyn/splash.png', kind='watch_image', json_body=False)
        self.assertEqual(raw, self.png)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].headers.get('Cookie'), '')

        with self.assertRaises(ValueError):
            await self.client.get('https://evil.example/bfs/new_dyn/splash.png', kind='watch_image', json_body=False)

    async def test_ensure_buvid_sent_to_feed_space_and_stripped_on_media_and_logout(self):
        self.client.logout()
        self.assertTrue(await self.client.ensure_buvid())
        self.assertEqual(self.client._anon_buvid.get('buvid3'), 'ANON-BUVID3-TOKEN')
        await self.client.api(
            '/x/polymer/web-dynamic/v1/feed/space',
            {'host_mid': '401742377', 'timezone_offset': '-480', 'web_location': '333.999'},
            wbi=True,
        )
        feed_reqs = [req for req in self.requests if req.url.path == '/x/polymer/web-dynamic/v1/feed/space']
        self.assertEqual(len(feed_reqs), 1)
        self.assertIn('buvid3=ANON-BUVID3-TOKEN', feed_reqs[0].headers.get('Cookie', ''))
        self.assertIn('w_rid', feed_reqs[0].url.params)

        await self.client.get('https://i0.hdslb.com/bfs/new_dyn/splash.png', kind='watch_image', json_body=False)
        await self.client.get('https://aisubtitle.hdslb.com/bfs/subtitle/sample.json', kind='subtitle')
        img_reqs = [req for req in self.requests if req.url.path == '/bfs/new_dyn/splash.png']
        sub_reqs = [req for req in self.requests if req.url.path == '/bfs/subtitle/sample.json']
        self.assertEqual(img_reqs[-1].headers.get('Cookie'), '')
        self.assertEqual(sub_reqs[-1].headers.get('Cookie'), '')

        self.client.logout()
        self.assertEqual(self.client._anon_buvid, {})


class WatchProbeDigestAndDiscussionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='watch-rt-', dir=runs))
        self.config = Config(self.folder)
        self.config.save(
            Settings(
                enabled=True,
                account_id='account',
                allowed_chat_ids=['group@chatroom', 'other@chatroom'],
                bilibili_enabled=True,
                bilibili_watch_enabled=True,
                bilibili_visual_enabled=True,
                vision_enabled=True,
                images_chat_enabled=True,
                images_allowed_chat_ids=['group@chatroom'],
                debounce_single_seconds=0,
                debounce_burst_seconds=0,
                debounce_max_seconds=0,
            ),
            'test-key',
        )
        self.store = Store(self.folder / 'state.sqlite3')
        self.gateway = Gateway()
        ui_jobs = self.folder / 'ui-jobs'
        ui_jobs.mkdir(exist_ok=True)
        self.gateway.ui_jobs_directory = ui_jobs
        self.png = _make_png_bytes((20, 180, 250))
        self.requests = []
        now = time.time()

        def handler(request):
            self.requests.append(request)
            path = request.url.path
            if path == '/x/polymer/web-dynamic/v1/feed/space':
                mid = request.url.params.get('host_mid', '')
                if mid == '401742377':
                    return httpx.Response(
                        200,
                        json={
                            'code': 0,
                            'data': {
                                'items': [
                                    {
                                        'id_str': '500001',
                                        'modules': {
                                            'module_author': {'mid': 401742377, 'name': '原神', 'pub_ts': now - 60},
                                            'module_dynamic': {
                                                'desc': {'text': '《原神》角色立绘 | 妮可·莱恩「黄金的预言家」'},
                                                'major': {
                                                    'type': 'MAJOR_TYPE_DRAW',
                                                    'draw': {'items': [{'src': '//i0.hdslb.com/bfs/new_dyn/nicole.png@800w.webp'}]},
                                                },
                                            },
                                        },
                                    },
                                    {
                                        'id_str': '500000',
                                        'modules': {
                                            'module_author': {'mid': 401742377, 'name': '原神', 'pub_ts': now - 120},
                                            'module_dynamic': {
                                                'desc': {'text': '《原神》7.3版本前瞻特别节目预告'},
                                                'major': {
                                                    'type': 'MAJOR_TYPE_ARCHIVE',
                                                    'archive': {
                                                        'bvid': 'BV1live73001',
                                                        'aid': '73001',
                                                        'title': '《原神》7.3版本前瞻特别节目',
                                                        'desc': '12月5日开启',
                                                        'duration_text': '52:10',
                                                        'cover': 'https://i0.hdslb.com/bfs/archive/live73.png',
                                                    },
                                                },
                                            },
                                        },
                                    },
                                ]
                            },
                        },
                    )
                return httpx.Response(200, json={'code': 0, 'data': {'items': []}})
            if path == '/x/web-interface/wbi/search/type':
                return httpx.Response(
                    200,
                    json={
                        'code': 0,
                        'data': {
                            'result': [
                                {
                                    'bvid': 'BV1sum730001',
                                    'aid': 88001,
                                    'mid': 99001,
                                    'author': '原学省流君',
                                    'title': '【原神7.3】6分钟前瞻总结+兑换码一图流',
                                    'description': '含全部福利和卡池安排',
                                    'duration': '06:12',
                                    'pubdate': now - 120,
                                    'play': 150000,
                                },
                                {
                                    'bvid': 'BV1full73002',
                                    'aid': 88002,
                                    'mid': 99002,
                                    'author': '无剪辑搬运',
                                    'title': '【原神7.3】前瞻直播回放全流程无剪辑',
                                    'description': '完整录屏',
                                    'duration': '64:00',
                                    'pubdate': now - 120,
                                    'play': 999999,
                                },
                            ]
                        },
                    },
                )
            if path == '/x/frontend/finger/spi':
                return httpx.Response(200, json={'code': 0, 'data': {'b_3': 'WATCH-BUVID3', 'b_4': 'WATCH-BUVID4'}})
            if path == '/x/web-interface/nav':
                return httpx.Response(
                    200,
                    json={
                        'code': 0,
                        'data': {
                            'isLogin': False,
                            'wbi_img': {
                                'img_url': 'https://i0.hdslb.com/bfs/wbi/' + 'a' * 32 + '.png',
                                'sub_url': 'https://i0.hdslb.com/bfs/wbi/' + 'b' * 32 + '.png',
                            },
                        },
                    },
                )
            if path == '/x/v2/reply/main':
                return httpx.Response(
                    200,
                    json={
                        'code': 0,
                        'data': {'upper': {'top': {'content': {'message': '挪德卡莱新角色火元素法器，福利原石300！'}}}, 'replies': []},
                    },
                )
            if str(request.url.host).endswith('hdslb.com') and path.endswith('.png'):
                return httpx.Response(200, content=self.png, headers={'Content-Type': 'image/png'})
            return httpx.Response(200, json={'code': 0, 'data': {}})

        self.runtime = Runtime(self.config, self.store, self.gateway, ROOT / 'styles', engine_factory=Engine)
        await self.runtime.bilibili.client.close()
        self.runtime.bilibili.client = BilibiliClient(self.config, self.store, transport=httpx.MockTransport(handler))
        self.runtime.bilibili.watch.client = self.runtime.bilibili.client
        self.sleeper = patch('asyncio.sleep', new=_fast_watch_sleep)
        self.sleeper.start()
        self.growth = GrowthService(self.runtime)

    async def asyncTearDown(self):
        self.sleeper.stop()
        await self.runtime.close()

    def _queue_human(self, mid: int, content: str, *, sender='阿明', chat='group@chatroom', ts=None, trigger='direct'):
        ev = {
            'key': f'account/{chat}/{mid}',
            'account': 'account',
            'chat': chat,
            'sender': sender,
            'sender_id': sender,
            'kind': 1,
            'is_self': 0,
            'local_id': mid,
            'content': content,
            'timestamp': time.time() if ts is None else ts,
            'trigger': trigger,
        }
        self.store.insert(ev)
        self.store.observe([ev])
        return ev

    async def test_probe_search_summary_and_growth_vision_digest(self):
        watch = self.runtime.bilibili.watch
        # Keep only Genshin enabled for deterministic probing
        targets = [t for t in self.config.settings.bilibili_watch_targets if t.get('game') == 'genshin']
        self.config.save(self.config.settings.model_copy(update={'bilibili_watch_targets': targets}), 'test-key')

        res = await watch.probe_once(force=True)
        self.assertTrue(res['ok'])
        # 1 character art + 1 livestream_notice + 1 searched summary video (64m stream replay filtered out)
        self.assertEqual(res['queued'], 3)

        vision_calls = []

        class DigestEngine:
            api_style = 'test'
            stream_enabled = False
            last_error = ''
            last_response_meta = {}

            async def chat(self, messages, **options):
                content = messages[0].get('content') if messages else ''
                imgs = [p for p in content if isinstance(p, dict) and p.get('type') == 'image_url'] if isinstance(content, list) else []
                vision_calls.append(imgs)
                return json.dumps(
                    {
                        'game': 'genshin',
                        'version': '7.3',
                        'kind': 'character_art',
                        'is_story': False,
                        'character_names': ['妮可·莱恩'],
                        'safe_facts': ['金发蓝瞳配色，手持星象书', '定位推测为火元素辅助'],
                        'spoiler_facts': ['实为前代魔女会成员转世'],
                        'chat_teaser': '妮可这立绘金色星轨也太华丽了吧！',
                        'keywords': ['妮可', '魔女会', '7.3'],
                    },
                    ensure_ascii=False,
                )

            async def close(self):
                pass

        with patch.object(self.growth, 'worker_engine', return_value=DigestEngine()):
            processed = await self.growth.run_once()
        self.assertTrue(processed)
        # First queued non-story dynamic digested with splash image passed to vision model
        self.assertEqual(len(vision_calls), 1)
        self.assertEqual(len(vision_calls[0]), 1)
        self.assertTrue(vision_calls[0][0]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        snap = watch.snapshot()
        done_items = [it for it in snap['items'] if it['state'] == 'done']
        self.assertEqual(len(done_items), 1)
        self.assertEqual(done_items[0]['character_names'], ['妮可·莱恩'])
        self.assertEqual(done_items[0]['spoiler_facts'], ['实为前代魔女会成员转世'])
        self.assertEqual(done_items[0]['image_count'], 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_readings').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_interest_events').fetchone()[0], 0)

    def _seed_story_and_art_items(self):
        watch = self.runtime.bilibili.watch
        now = time.time()
        with self.store.lock, self.store.db:
            self.store.db.execute(
                '''INSERT INTO bilibili_watch_items(
                       id, account, game, source_mid, source_name, source_kind, dyn_id, bvid, aid,
                       kind, version, title, raw_text, url, pub_ts, duration, is_story, state, enabled,
                       safe_facts_json, spoiler_facts_json, character_names_json, keywords_json,
                       image_paths_json, chat_teaser, created, updated
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    'item-story-73',
                    'account',
                    'genshin',
                    '401742377',
                    '原神',
                    'official',
                    'dyn_story_73',
                    'BV1story7301',
                    '9001',
                    'story_summary',
                    '7.3',
                    '挪德卡莱7.3主线剧情总结',
                    '挪德卡莱主线流程解析',
                    'https://www.bilibili.com/video/BV1story7301/',
                    now - 100,
                    620,
                    1,
                    'done',
                    1,
                    json.dumps(['7.3主线聚焦挪德卡莱港城新区域', '完成任务可领对应原石奖励'], ensure_ascii=False),
                    json.dumps(['幕后真凶其实是假扮引路人的执行官木偶', '最终决战后大团长暂时退场沉睡'], ensure_ascii=False),
                    json.dumps(['木偶'], ensure_ascii=False),
                    json.dumps(['挪德卡莱', '7.3', '木偶'], ensure_ascii=False),
                    '[]',
                    '',
                    now - 100,
                    now - 100,
                ),
            )
            watch.watch_cache_dir.mkdir(parents=True, exist_ok=True)
            art_file = watch.watch_cache_dir / 'item-art-73_0.png'
            art_file.write_bytes(self.png)
            self.store.db.execute(
                '''INSERT INTO bilibili_watch_items(
                       id, account, game, source_mid, source_name, source_kind, dyn_id, bvid, aid,
                       kind, version, title, raw_text, url, pub_ts, duration, is_story, state, enabled,
                       safe_facts_json, spoiler_facts_json, character_names_json, keywords_json,
                       image_paths_json, chat_teaser, created, updated
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    'item-art-73',
                    'account',
                    'genshin',
                    '401742377',
                    '原神',
                    'official',
                    'dyn_art_73',
                    '',
                    '',
                    'character_art',
                    '7.3',
                    '挪德卡莱新角色立绘｜丝柯克新衣装',
                    '新衣装公开',
                    'https://t.bilibili.com/dyn_art_73',
                    now - 50,
                    0,
                    0,
                    'done',
                    1,
                    json.dumps(['银紫渐变长裙搭配星海披风', '限时活动可享折扣外观'], ensure_ascii=False),
                    '[]',
                    json.dumps(['丝柯克'], ensure_ascii=False),
                    json.dumps(['丝柯克', '挪德卡莱', '7.3'], ensure_ascii=False),
                    json.dumps([str(art_file)], ensure_ascii=False),
                    '丝柯克这套星海新立绘也太仙了吧！',
                    now - 50,
                    now - 50,
                ),
            )

    async def test_four_layer_anti_spoiler_gates_and_search_brake(self):
        self._seed_story_and_art_items()
        watch = self.runtime.bilibili.watch
        prompts = []

        class ChatEngine:
            api_style = 'test'
            stream_enabled = False
            last_error = ''
            last_response_meta = {}

            async def chat(self, messages, **options):
                prompts.append(options.get('system_prompt', ''))
                return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '收到啦！'}]}, ensure_ascii=False)

            async def close(self):
                pass

        with patch.object(self.runtime, 'model', new=AsyncMock(return_value=ChatEngine())):
            # 1) Layer 2: Surface look/banner chat omits spoiler_facts physically from system_prompt
            self._queue_human(1, '挪德卡莱7.3丝柯克新立绘和福利怎么样啊')
            self.assertTrue(await self.runtime.process_next())
            self.assertIn('银紫渐变长裙搭配星海披风', prompts[-1])
            self.assertNotIn('假扮引路人的执行官木偶', prompts[-1])
            self.assertNotIn('大团长暂时退场沉睡', prompts[-1])

            # 2) Layer 2 + Layer 4: Explicit plot question injects spoiler_facts + progressive framing
            self._queue_human(2, '挪德卡莱7.3主线剧情到底讲了啥，幕后黑手是谁呀？')
            self.assertTrue(await self.runtime.process_next())
            self.assertIn('假扮引路人的执行官木偶', prompts[-1])
            self.assertIn('渐进式聊剧情', prompts[-1])

            # 3) Layer 3: Group member activates spoiler brake -> blocks BOTH spoiler_facts AND plot web_search
            self._queue_human(3, '挪德卡莱7.3别剧透！我还没过主线呢，现在真凶是谁我都不知道')
            self.assertTrue(await self.runtime.process_next())
            self.assertNotIn('假扮引路人的执行官木偶', prompts[-1])
            self.assertIn('防剧透锁定生效中', prompts[-1])
            allowed_plot, reason = watch.allow_web_search('account', 'group@chatroom', '原神 7.3 挪德卡莱 结局 真相 剧透')
            self.assertFalse(allowed_plot)
            self.assertIn('防剧透', reason)
            allowed_safe, _ = watch.allow_web_search('account', 'group@chatroom', '原神 7.3 卡池时间 兑换码')
            self.assertTrue(allowed_safe)

            # End-to-end runtime gate: even if model emits a plot search_query, web_search.run is NOT invoked
            self.config.save(self.config.settings.model_copy(update={'web_search_enabled': True}), 'test-key')
            search_followups = []

            class SearchAttemptEngine:
                api_style = 'test'
                stream_enabled = False
                last_error = ''
                last_response_meta = {}

                def __init__(self):
                    self.calls = 0

                async def chat(self, messages, **options):
                    self.calls += 1
                    search_followups.append((messages, options))
                    if self.calls == 1:
                        return json.dumps({'should_reply': False, 'search_query': '原神 7.3 挪德卡莱 结局 剧透'}, ensure_ascii=False)
                    return json.dumps({'should_reply': True, 'actions': [{'type': 'text', 'text': '先不剧透啦'}]}, ensure_ascii=False)

                async def close(self):
                    pass

            with (
                patch.object(self.runtime, 'model', new=AsyncMock(return_value=SearchAttemptEngine())),
                patch.object(self.runtime.web_search, 'run', new=AsyncMock()) as mocked_search,
            ):
                self._queue_human(30, '挪德卡莱7.3主线最后结局是啥')
                self.assertTrue(await self.runtime.process_next())
                mocked_search.assert_not_called()
            self.assertEqual(len(search_followups), 2)
            self.assertIn('防剧透', json.dumps(search_followups[1][0], ensure_ascii=False))
            self.config.save(self.config.settings.model_copy(update={'web_search_enabled': False}), 'test-key')

            # 4) Layer 3 Release: explicit unlock clears the lock and restores plot discussion on next turn
            t_release = time.time() + 1
            self._queue_human(4, '原神 挪德卡莱 剧透封印解除，我已经过完主线了！', ts=t_release)
            self.assertTrue(await self.runtime.process_next())
            self.assertEqual(watch.snapshot()['spoiler_locks'], [])
            allowed_after, _ = watch.allow_web_search('account', 'group@chatroom', '原神 7.3 挪德卡莱 主线结局')
            self.assertTrue(allowed_after)
            self._queue_human(5, '快说说挪德卡莱7.3主线剧情里谁是真凶', ts=t_release + 1)
            self.assertTrue(await self.runtime.process_next())
            self.assertIn('假扮引路人的执行官木偶', prompts[-1])

            # 5) Unrelated non-game turn stays clean without game leak
            self._queue_human(6, '你们中午吃什么外卖呀', ts=t_release + 2, chat='other@chatroom')
            self.assertTrue(await self.runtime.process_next())
            self.assertNotIn('三游最新情报资料', prompts[-1])

        # Verify zero leakage into private bilibili reading/interest tables
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_readings').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_interest_events').fetchone()[0], 0)

    async def test_proactive_delivery_requires_manual_check_never_pushes_story_and_queues_image(self):
        self._seed_story_and_art_items()
        watch = self.runtime.bilibili.watch

        # Unchecked by default -> sends nothing
        with patch('whalegirl_linux.bilibili_watch.active_hour', return_value=True):
            self.assertEqual(await watch.deliver_ready_items(), 0)
        self.assertEqual(self.gateway.sent, [])

        # Manually check group@chatroom
        self.config.save(
            self.config.settings.model_copy(
                update={'bilibili_watch_chat_ids': ['group@chatroom'], 'bilibili_watch_push_cooldown_minutes': 0}
            ),
            'test-key',
        )
        with patch('whalegirl_linux.bilibili_watch.active_hour', return_value=True):
            sent_n = await watch.deliver_ready_items()
            self.assertEqual(sent_n, 1)
            # Second call is idempotent & respects story exclusion (item-story-73 never delivered)
            self.assertEqual(await watch.deliver_ready_items(), 0)

        self.assertEqual(self.gateway.sent, [('text', '丝柯克这套星海新立绘也太仙了吧！')])
        hist = self.store.db.execute(
            'SELECT role, content FROM history WHERE account=? AND chat=?',
            ('account', 'group@chatroom'),
        ).fetchall()
        self.assertEqual([r['role'] for r in hist], ['assistant'])
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM message_log WHERE account='account' AND is_self=0 AND sender_id!=''"
            ).fetchone()[0],
            0,
        )
        deliveries = self.store.db.execute('SELECT item_id, text_state, image_job_id FROM bilibili_watch_deliveries').fetchall()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]['item_id'], 'item-art-73')
        self.assertEqual(deliveries[0]['text_state'], 'sent')
        self.assertTrue(deliveries[0]['image_job_id'])
        jobs = self.store.db.execute('SELECT kind, destination, source_key, asset_id FROM picture_jobs').fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertEqual((jobs[0]['kind'], jobs[0]['destination']), ('send', 'chat'))
        self.assertEqual(jobs[0]['source_key'], 'bili_watch:item-art-73:group@chatroom')
        asset = self.store.db.execute(
            'SELECT scope, scope_chat, state, source FROM picture_assets WHERE id=?',
            (jobs[0]['asset_id'],),
        ).fetchone()
        self.assertEqual((asset['scope'], asset['scope_chat'], asset['state'], asset['source']), ('chat', 'group@chatroom', 'tagged', 'uploaded'))


class WatchConsoleAPITests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.app = create_app(Path(tempfile.mkdtemp(prefix='watch-api-', dir=runs)), gateway=Gateway(), run_background=False)
        self.app.state.config.save(
            Settings(
                enabled=True,
                account_id='account',
                allowed_chat_ids=['group@chatroom', 'friend1'],
                bilibili_enabled=True,
                bilibili_watch_enabled=True,
            ),
            'test-key',
        )
        def handler(request):
            path = request.url.path
            if path == '/x/frontend/finger/spi':
                return httpx.Response(200, json={'code': 0, 'data': {'b_3': 'API-BUVID3', 'b_4': 'API-BUVID4'}})
            if path == '/x/web-interface/nav':
                return httpx.Response(
                    200,
                    json={
                        'code': 0,
                        'data': {
                            'isLogin': False,
                            'wbi_img': {
                                'img_url': 'https://i0.hdslb.com/bfs/wbi/' + 'a' * 32 + '.png',
                                'sub_url': 'https://i0.hdslb.com/bfs/wbi/' + 'b' * 32 + '.png',
                            },
                        },
                    },
                )
            if path == '/x/polymer/web-dynamic/v1/feed/space':
                return httpx.Response(200, json={'code': 0, 'data': {'items': []}})
            return httpx.Response(200, json={'code': 0, 'data': {}})

        asyncio.run(self.app.state.runtime.bilibili.client.close())
        mock_bili = BilibiliClient(self.app.state.config, self.app.state.runtime.store, transport=httpx.MockTransport(handler))
        self.app.state.runtime.bilibili.client = mock_bili
        self.app.state.runtime.bilibili.watch.client = mock_bili
        self.sleeper = patch('asyncio.sleep', new=_fast_watch_sleep)
        self.sleeper.start()
        self.client = TestClient(self.app)
        self.headers = {'Authorization': 'Bearer ' + self.app.state.config.admin_token}

    def tearDown(self):
        self.sleeper.stop()
        self.client.close()
        asyncio.run(self.app.state.runtime.close())

    def test_watch_endpoints_auth_config_items_images_and_locks(self):
        # Auth enforcement
        self.assertEqual(self.client.get('/api/bilibili/watch').status_code, 401)
        snap_res = self.client.get('/api/bilibili/watch', headers=self.headers)
        self.assertEqual(snap_res.status_code, 200)
        body = snap_res.json()
        self.assertIn('schedule', body)
        self.assertEqual(body['config']['bilibili_watch_chat_ids'], [])

        # Unallowed chat rejected; allowed chat saved
        bad_cfg = self.client.post(
            '/api/bilibili/watch/config',
            headers=self.headers,
            json={'bilibili_watch_chat_ids': ['stranger@chatroom']},
        )
        self.assertEqual(bad_cfg.status_code, 400)
        extra_cfg = self.client.post(
            '/api/bilibili/watch/config',
            headers=self.headers,
            json={'unknown_field': 1},
        )
        self.assertEqual(extra_cfg.status_code, 422)
        ok_cfg = self.client.post(
            '/api/bilibili/watch/config',
            headers=self.headers,
            json={'bilibili_watch_chat_ids': ['group@chatroom'], 'bilibili_watch_push_cooldown_minutes': 25},
        )
        self.assertEqual(ok_cfg.status_code, 200)
        self.assertEqual(ok_cfg.json()['config']['bilibili_watch_chat_ids'], ['group@chatroom'])

        # Seed watch item + cached PNG image, then test actions & image endpoint
        item_id = 'a' * 32
        watch = self.app.state.runtime.bilibili.watch
        watch.watch_cache_dir.mkdir(parents=True, exist_ok=True)
        png = _make_png_bytes((240, 120, 60))
        img_path = watch.watch_cache_dir / f'{item_id}_0.png'
        img_path.write_bytes(png)
        store = self.app.state.runtime.store
        now = time.time()
        with store.lock, store.db:
            store.db.execute(
                '''INSERT INTO bilibili_watch_items(
                       id, account, game, source_mid, source_name, source_kind, dyn_id, bvid,
                       kind, version, title, raw_text, url, pub_ts, duration, is_story, state, enabled,
                       safe_facts_json, spoiler_facts_json, character_names_json, keywords_json,
                       image_paths_json, chat_teaser, created, updated
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    item_id,
                    'account',
                    'zzz',
                    '1636034895',
                    '绝区零',
                    'official',
                    'dyn_api_1',
                    '',
                    'character_art',
                    '3.3',
                    '绝区零｜星见雅代理人档案',
                    '代理人档案',
                    'https://t.bilibili.com/dyn_api_1',
                    now,
                    0,
                    0,
                    'done',
                    1,
                    json.dumps(['对空六科课长', '冰烈属性异常风格']),
                    '[]',
                    json.dumps(['星见雅']),
                    json.dumps(['星见雅', '对空六科']),
                    json.dumps([str(img_path)]),
                    '星见雅新立绘也太飒了吧！',
                    now,
                    now,
                ),
            )

        self.assertEqual(self.client.get(f'/api/bilibili/watch/items/{item_id}/images/0', headers=self.headers).content, png)
        self.assertEqual(self.client.get('/api/bilibili/watch/items/bad-id/images/0', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get(f'/api/bilibili/watch/items/{item_id}/images/3', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get(f'/api/bilibili/watch/items/{item_id}/images/1', headers=self.headers).status_code, 404)

        act1 = self.client.post(f'/api/bilibili/watch/items/{item_id}', headers=self.headers, json={'action': 'mark_story'})
        self.assertEqual(act1.status_code, 200)
        self.assertTrue(act1.json()['item']['is_story'])
        act2 = self.client.post(f'/api/bilibili/watch/items/{item_id}', headers=self.headers, json={'action': 'unmark_story'})
        self.assertEqual(act2.status_code, 200)
        self.assertFalse(act2.json()['item']['is_story'])
        act3 = self.client.post(f'/api/bilibili/watch/items/{item_id}', headers=self.headers, json={'action': 'disable'})
        self.assertEqual(act3.status_code, 200)
        self.assertFalse(act3.json()['item']['enabled'])
        act4 = self.client.post(f'/api/bilibili/watch/items/{item_id}', headers=self.headers, json={'action': 'enable'})
        self.assertEqual(act4.status_code, 200)
        self.assertTrue(act4.json()['item']['enabled'])

        # Spoiler lock set & clear
        lock_set = self.client.post(
            '/api/bilibili/watch/spoiler-locks',
            headers=self.headers,
            json={'action': 'lock', 'chat': 'group@chatroom', 'game': 'zzz', 'minutes': 90, 'reason': '手动锁定'},
        )
        self.assertEqual(lock_set.status_code, 200)
        self.assertEqual(len(lock_set.json()['snapshot']['spoiler_locks']), 1)
        self.assertEqual(lock_set.json()['snapshot']['spoiler_locks'][0]['game'], 'zzz')
        lock_clear = self.client.post(
            '/api/bilibili/watch/spoiler-locks',
            headers=self.headers,
            json={'action': 'unlock', 'chat': 'group@chatroom', 'game': 'zzz'},
        )
        self.assertEqual(lock_clear.status_code, 200)
        self.assertEqual(lock_clear.json()['snapshot']['spoiler_locks'], [])

        probe_res = self.client.post(
            '/api/bilibili/watch/probe',
            headers=self.headers,
            json={'force': False, 'digest': False},
        )
        self.assertEqual(probe_res.status_code, 200)
        self.assertIn('snapshot', probe_res.json())


if __name__ == '__main__':
    unittest.main()

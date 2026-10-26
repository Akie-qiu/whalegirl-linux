"""Focused SNS parsing, independent switches and non-replay delivery checks."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from whalegirl_linux.gateway import UIJobError
from whalegirl_linux.moments import MomentsBook, MomentsService, comment_context, similar_publication
from whalegirl_linux.moments_payload import parse_post
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class PayloadTests(unittest.TestCase):
    def test_unsigned_native_id_and_32_bit_comment_fallback(self):
        raw = '''<SnsDataItem><TimelineObject><id>18446744073709551600</id><username>account</username>
            <createTime>1792610000</createTime><contentDesc>一条测试动态</contentDesc><ContentObject><type>2</type></ContentObject>
            </TimelineObject><LocalExtraInfo><nickname>测试</nickname><user_comment><comment_64id>0</comment_64id>
            <comment_id>14</comment_id><username>account</username><content>自己的评论</content><create_time>1792610100</create_time>
            <is_local_added>1</is_local_added></user_comment></LocalExtraInfo></SnsDataItem>'''
        post = parse_post({'tid': -16, 'user_name': 'account', 'content': raw}, {'account'})
        self.assertEqual(post['id'], '18446744073709551600')
        self.assertTrue(post['own'])
        self.assertEqual(post['comments'][0]['id'], '14')
        self.assertTrue(post['comments'][0]['own'])
        self.assertTrue(post['comments'][0]['local'])

    def test_invalid_xml_and_unpublished_id_are_not_actions(self):
        self.assertIsNone(parse_post({'tid': 0, 'content': '<TimelineObject><id>0</id></TimelineObject>'}))
        self.assertIsNone(parse_post({'content': '<!DOCTYPE foo><TimelineObject/>'}))


class CommentContextTests(unittest.TestCase):
    def test_reply_chain_identifies_the_bot_and_original_question(self):
        question = {'id': '1', 'author': 'friend', 'name': '朋友', 'text': '去哪儿', 'created': 1}
        answer = {'id': '2', 'author': 'native-account', 'text': '公园', 'own': True,
                  'reply_to': '1', 'target': 'friend', 'created': 2}
        target = {'id': '3', 'author': 'friend', 'text': '那里远吗', 'reply_to': '2',
                  'target': 'native-account', 'created': 3}
        post = {'own': True, 'author': 'native-account', 'comments': [target, question, answer]}
        result = comment_context(post, target, 'configured-account')
        self.assertEqual([c['id'] for c in result['thread_oldest_first']], ['1', '2', '3'])
        self.assertTrue(result['reply_target']['reply_to_you'])
        self.assertEqual(result['reply_target']['reply_to_person'], '你自己')
        self.assertTrue(result['parent_comment_available'])

    def test_other_people_replying_to_each_other_are_not_addressing_the_bot(self):
        parent = {'id': '2', 'author': 'alice', 'name': '甲', 'text': '周末见', 'created': 1}
        target = {'id': '10', 'author': 'bob', 'name': '乙', 'text': '好呀', 'reply_to': '2',
                  'target': 'alice', 'created': 1}
        result = comment_context({'comments': [target, parent]}, target, 'account')
        self.assertFalse(result['reply_target']['reply_to_you'])
        self.assertEqual(result['reply_target']['reply_to_person'], '甲')
        self.assertEqual([c['id'] for c in result['recent_public_comments']], ['2', '10'])

    def test_missing_parent_and_cycle_are_explicit_and_bounded(self):
        target = {'id': '3', 'author': 'friend', 'text': '哪一个', 'reply_to': '2'}
        result = comment_context({'comments': [target]}, target, 'account')
        self.assertFalse(result['parent_comment_available'])
        self.assertEqual(len(result['thread_oldest_first']), 1)
        parent = {'id': '2', 'author': 'account', 'own': True, 'text': '这个', 'reply_to': '3'}
        result = comment_context({'comments': [target, parent]}, target, 'account')
        self.assertEqual(len(result['thread_oldest_first']), 2)


class MomentsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        path = Path(tempfile.mkdtemp(prefix='moments-', dir=ROOT / 'test-runs'))
        self.store = Store(path / 'state.sqlite3')
        self.settings = Settings(account_id='account', moments_enabled=True, moments_publish_enabled=True,
            moments_reply_own_enabled=True, moments_comment_friends_enabled=False, affection_enabled=False,
            moments_start_hour=0, moments_end_hour=0)
        self.calls = []
        async def native(operation, account, **values):
            self.calls.append((operation, account, values))
            return {'confirmed': False}
        self.runtime = SimpleNamespace(store=self.store, config=SimpleNamespace(settings=self.settings, generation=0),
            gateway=SimpleNamespace(ui_lock=asyncio.Lock(), inflight_sends=set(), moments=native),
            gateway_state={'status':'logged_in'}, account_id='account', processing='', paused_reason='',
            last_send=0, safety=SimpleNamespace(held=lambda a:'', check=lambda *a,**kw:{'allowed':True}))
        self.service = MomentsService(self.runtime)

    def tearDown(self):
        self.store.close()

    def draft(self, kind='publish'):
        identity = self.service.book.create('account', kind, str(time.time_ns()), {})
        self.service.book.change(identity, 'draft', text='测试草稿，不会真实发布')
        return identity

    async def test_own_and_friend_switches_are_independent(self):
        self.service.gate('reply_own')
        with self.assertRaisesRegex(ValueError, '开关'):
            self.service.gate('comment_friend')
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_reply_own_enabled':False, 'moments_comment_friends_enabled':True})
        self.service.gate('comment_friend')
        with self.assertRaisesRegex(ValueError, '开关'):
            self.service.gate('reply_own')

    async def test_uncertain_send_is_once_and_blocks_replay(self):
        identity = self.draft()
        row = await self.service.send(identity)
        self.assertEqual(row['state'], 'uncertain')
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            await self.service.send(identity)
        self.assertEqual(len(self.calls), 1)

    async def test_definite_pre_send_failure_does_not_claim_delivery(self):
        async def fail(*a, **kw):
            raise UIJobError('No exact source', 'failed_before_send')
        self.runtime.gateway.moments = fail
        row = await self.service.send(self.draft())
        self.assertEqual(row['state'], 'failed')
        self.assertFalse(row['receipt'])

    async def test_old_draft_sent_today_consumes_today_quota(self):
        identity = self.draft()
        self.store.db.execute('UPDATE moments_actions SET created=? WHERE id=?', (time.time()-172800, identity))
        self.store.db.commit()
        self.service.book.change(identity, 'sent', receipt='server-id')
        self.assertEqual(self.service.book.count('account', 'publish'), 1)
        self.assertEqual(self.service.book.count('account', 'reply_own'), 0)

    async def test_manual_previews_do_not_take_automatic_slots_or_reservations(self):
        self.generation_fixture({'participate':True, 'text':'新的游戏配色练习', 'media_mode':'none'},
            moments_publish_per_day=1, moments_mode='auto')
        self.draft()
        now = time.time()
        saved = {}
        plan = self.service._build_publish_plan(self.runtime.config.settings, now)
        plan['slots'][0].update(start=now-60, end=now+7200, target=now+600, done=True)
        saved['publish_plan'] = plan
        self.assertEqual(self.service.book.count('account', 'publish', pending=True), 1)
        self.assertEqual(self.service.book.count('account', 'publish', pending=True, scheduled_only=True), 0)
        self.assertIsNotNone(self.service.next_publish_time(self.runtime.config.settings, saved, now))
        self.assertFalse(saved['publish_plan']['slots'][0]['done'])
        row = await self.service.generate('publish', auto_publish=True)
        self.assertEqual(row['state'], 'draft')
        self.assertEqual(self.service.book.count('account', 'publish', pending=True, scheduled_only=True), 1)
        with self.assertRaisesRegex(ValueError, '额度'):
            await self.service.generate('publish', auto_publish=True)

    async def test_explicit_publish_survives_chat_busy_and_restart_without_sending_manual_preview(self):
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_mode':'draft', 'moments_reply_own_enabled':False, 'moments_publish_per_day':2})
        self.runtime.images = SimpleNamespace(stage=lambda *args, **kwargs: None)
        manual, requested = self.draft(), self.draft()
        self.runtime.processing = 'chat'
        row = await self.service.send(requested, requested=True)
        self.assertEqual(row['state'], 'draft')
        self.assertTrue(next(r for r in self.service.book.recent('account') if r['id'] == requested)['queued'])
        self.assertFalse(self.calls)
        self.service = MomentsService(self.runtime)
        self.runtime.processing = ''
        async def native(operation, account, **values):
            self.calls.append(values)
            return {'confirmed':True}
        self.runtime.gateway.moments = native
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.book.get('account', requested)['state'], 'sent')
        self.assertEqual(self.service.book.get('account', manual)['state'], 'draft')
        self.service.book.save_state('account', {'last_poll':time.time()})
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_empty_moments_generation_keeps_provider_error_in_ledger(self):
        self.generation_fixture({})
        class Engine:
            last_error = 'HTTP 404（模型或接口不可用）'
            async def chat(self, *args, **kwargs): return ''
            async def close(self): pass
        self.runtime.engine_factory = lambda **kwargs: Engine()
        with self.assertRaisesRegex(ValueError, 'HTTP 404'):
            await self.service.generate('publish')
        self.assertIn('HTTP 404', self.service.book.recent('account')[0]['error'])

    async def test_master_off_preserves_records_and_prevents_native_calls(self):
        identity = self.draft()
        self.runtime.config.settings = self.settings.model_copy(update={'moments_enabled':False})
        with self.assertRaises(ValueError):
            await self.service.send(identity)
        self.assertEqual(self.service.book.get('account', identity)['state'], 'draft')
        self.assertFalse(self.calls)

    async def test_reenable_starts_comment_window_now_without_backfill(self):
        disabled = self.settings.model_copy(update={'moments_reply_own_enabled':False})
        self.service.settings_changed(self.settings, disabled)
        before = time.time()
        self.service.settings_changed(disabled, self.settings)
        self.assertGreaterEqual(self.service.book.state('account')['active_reply_own'], before)

    def cached_comments(self, count=1):
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_mode': 'auto', 'moments_publish_enabled': False})
        now = time.time()
        self.service.book.save_state('account', {'last_poll': now, 'active_reply_own': now-100})
        comments = [{'id': str(i), 'author': 'friend', 'created': now-5+i,
                     'text': '评论', 'own': False} for i in range(1, count+1)]
        post = {'id': 'post', 'author': 'account', 'own': True, 'comments': comments}
        self.cache_reads = []

        async def cached(route):
            self.cache_reads.append(route)
            return {'account': 'account', 'posts': [post]}

        async def generate(kind, payload, source):
            identity = self.service.book.create('account', kind, source, payload)
            self.service.book.change(identity, 'draft', text='回复')
            return self.service.book.get('account', identity)

        async def native(operation, account, **values):
            self.calls.append((operation, account, values))
            return {'confirmed': True, 'receipt': 'server-comment'}

        self.runtime.gateway.native_request = cached
        self.runtime.gateway.moments = native
        self.service.generate = generate
        return post

    async def test_comments_reply_before_timeline_refresh_without_opening_read_ui(self):
        self.cached_comments()
        await self.service.tick()
        self.assertEqual(self.cache_reads, ['/moments'])
        self.assertEqual([call[0] for call in self.calls], ['moments_send'])
        self.assertEqual(self.service.book.find_source('account', 'reply_own', 'post/1')['state'], 'sent')
        self.assertGreater(self.service.last_cache_scan, 0)
        self.assertEqual(self.service.last_scan, 0)
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_native_reply_prevents_automatic_duplicate_after_manual_send(self):
        post = self.cached_comments()
        post['comments'].append({'id':'reply','author':'account','created':time.time(),
            'text':'already replied','own':True,'local':False,'reply_to':'1'})
        await self.service.tick()
        self.assertFalse(self.calls)
        self.assertIsNone(self.service.book.find_source('account','reply_own','post/1'))

    async def test_manual_and_auto_sources_cannot_submit_same_comment_twice(self):
        post = self.cached_comments()
        payload = {'post':post,'comment':post['comments'][0]}
        sent = self.service.book.create('account','reply_own','manual-random-id',payload)
        self.service.book.change(sent,'sent',text='already replied',receipt='native-reply')
        pending = self.service.book.create('account','reply_own','post/1',payload)
        self.service.book.change(pending,'draft',text='would be a duplicate')
        row = await self.service.send(pending)
        self.assertEqual(row['state'],'skipped')
        self.assertFalse(self.calls)
        other = {'post':{**post,'id':'another-post'},'comment':post['comments'][0]}
        self.assertIsNone(self.service.book.delivered_target('account','reply_own',other))

    async def test_retry_keeps_original_text_and_creates_current_source_draft(self):
        post = self.cached_comments()
        old = self.service.book.create('account','reply_own','post/1',{'post':post,'comment':post['comments'][0]})
        self.service.book.change(old,'failed',text='approved text',error='failed before submission')
        new = await self.service.retry_draft(old)
        row = self.service.book.get('account',new)
        self.assertEqual(row['state'],'draft')
        self.assertEqual(row['text'],'approved text')
        self.assertNotEqual(new,old)
        self.assertEqual(self.service.book.get('account',old)['state'],'failed')
        self.assertFalse(self.calls)
        self.service.book.change(new,'uncertain')
        with self.assertRaises(ValueError):await self.service.retry_draft(new)

    async def test_processed_comments_do_not_hide_new_comments_after_first_three(self):
        post = self.cached_comments(4)
        for comment in post['comments'][:3]:
            identity = self.service.book.create('account', 'reply_own', 'post/'+comment['id'], {})
            self.service.book.change(identity, 'skipped')
        await self.service.tick()
        self.assertEqual(self.service.book.find_source('account', 'reply_own', 'post/4')['state'], 'sent')
        self.assertEqual(len(self.calls), 1)

    async def test_auto_draft_deferred_for_chat_is_sent_once_when_idle(self):
        post = self.cached_comments()
        self.runtime.config.settings = self.runtime.config.settings.model_copy(update={'moments_reply_own_per_day': 1})
        identity = self.service.book.create('account', 'reply_own', 'post/1',
                                            {'post': post, 'comment': post['comments'][0]})
        self.service.book.change(identity, 'draft', text='已生成的回复')
        self.runtime.processing = 'chat'
        await self.service.tick()
        self.assertFalse(self.calls)
        self.runtime.processing = ''
        await self.service.tick()
        self.assertEqual(self.service.book.get('account', identity)['state'], 'sent')
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    async def test_moments_also_consume_account_sending_quota(self):
        from whalegirl_linux.safety import SafetyGuard
        identity = self.draft()
        self.service.book.change(identity, 'sent', receipt='server-id')
        guard = SafetyGuard(self.store)
        self.assertEqual(guard.activity('account')[0]['day'], 1)
        limits = self.settings.model_copy(update={'safety_per_day':1})
        self.assertFalse(guard.check('account','another-chat',limits,reply='不同内容')['allowed'])

    def generation_fixture(self, answer, **settings):
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_publish_per_day': 10, 'moments_image_pick_enabled': True,
            'moments_image_generate_enabled': True, **settings})
        self.runtime.config.api_key = lambda: 'fixture'
        self.runtime.config.image_api_key = lambda: 'fixture'
        self.runtime.styles = SimpleNamespace(get_active=lambda: SimpleNamespace(system_prompt='人设'))
        self.model_inputs, self.jobs, self.staged = [], [], []
        outer = self
        class Engine:
            async def chat(self, messages, **kwargs):
                outer.model_inputs.append((json.loads(messages[0]['content']), kwargs))
                return json.dumps(answer, ensure_ascii=False)
            async def close(self): pass
        self.runtime.engine_factory = lambda **kwargs: Engine()
        self.runtime.images = SimpleNamespace(
            candidates=lambda *args, **kwargs: [{'id': 'existing', 'source': 'uploaded', 'name': '现成配图'}],
            generation_count=lambda: 0, stage=lambda *args, **kwargs: self.staged.append(args[0]),
            enqueue=lambda **kwargs: self.jobs.append(kwargs))

    async def test_comment_generation_uses_one_call_and_only_the_selected_public_thread(self):
        self.generation_fixture({'participate': True, 'text': '对，就是刚才说的公园'}, global_memory=False)
        self.service.posts = [{'id': 'other', 'own': True, 'text': '无关的另一条动态'}]
        parent = {'id': '1', 'author': 'account', 'own': True, 'text': '公园', 'created': 1}
        target = {'id': '2', 'author': 'friend', 'name': '朋友', 'text': '你说的是那里吗',
                  'reply_to': '1', 'created': 2}
        post = {'id': 'chosen', 'author': 'account', 'own': True, 'text': '周末出门',
                'comments': [parent, target]}
        payload = {'post': post, 'comment': target}
        row = await self.service.generate('reply_own', payload)
        self.assertEqual(row['state'], 'draft')
        self.assertEqual(json.loads(row['payload']), payload)
        self.assertEqual(len(self.model_inputs), 1)
        data, options = self.model_inputs[0]
        self.assertNotIn('recent_own_posts', data)
        self.assertNotIn('comments', data['source']['post'])
        self.assertEqual(data['source']['conversation']['reply_target']['id'], '2')
        self.assertIn('唯一要回复', options['system_prompt'])
        self.assertNotIn('无关的另一条动态', json.dumps(data, ensure_ascii=False))
        self.assertFalse(self.calls)

    async def test_new_picture_can_be_preferred_even_with_existing_library_choice(self):
        self.generation_fixture({'participate': True, 'text': '研究一下游戏里这套奇怪的配色',
            'media_mode': 'generate', 'asset_ids': ['existing'], 'image_prompt': '游戏角色服装的暖冷色对比概念图',
            'post_topic': '游戏配色', 'post_angle': '衣服暖冷色对比', 'visual_concept': '左右对比构图'})
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'awaiting_image')
        self.assertEqual(len(self.jobs), 1)
        self.assertFalse(self.staged)
        payload = json.loads(row['payload'])
        self.assertEqual(payload['assets'], [])
        self.assertEqual(payload['post_topic'], '游戏配色')
        self.assertEqual(payload['media_mode'], 'generate')
        data, options = self.model_inputs[0]
        self.assertEqual(data['image_policy']['generation_eagerness'], 75)
        self.assertTrue(data['image_policy']['generation_available'])
        self.assertIn('不能文案换了', options['system_prompt'])
        self.assertEqual(len(self.model_inputs), 1)
        self.assertFalse(self.calls)

    async def test_eagerness_zero_or_budget_full_prevents_generation(self):
        for updates, used in [({'moments_image_generate_eagerness': 0}, 0), ({}, 10),
                              ({'moments_image_generate_enabled': False}, 0)]:
            with self.subTest(updates=updates, used=used):
                self.generation_fixture({'participate': True, 'text': '一张新的构图练习',
                    'media_mode': 'generate', 'image_prompt': '一幅新的画面'}, **updates)
                self.runtime.images.generation_count = lambda: used
                row = await self.service.generate('publish')
                self.assertEqual(row['state'], 'draft')
                self.assertFalse(self.jobs)
                self.assertFalse(self.model_inputs[0][0]['image_policy']['generation_available'])
                self.service.book.change(row['id'], 'cancelled')

    async def test_fixed_images_are_preserved_and_no_extra_generation_is_queued(self):
        self.generation_fixture({'participate': True, 'text': '固定图配一条动态',
            'media_mode': 'generate', 'asset_ids': [], 'image_prompt': '不要执行'}, moments_asset_ids=['fixed'])
        row = await self.service.generate('publish')
        self.assertEqual(json.loads(row['payload'])['assets'], ['fixed'])
        self.assertEqual(self.staged, ['fixed'])
        self.assertFalse(self.jobs)

    async def test_history_survives_restart_and_excludes_other_accounts_and_cancelled_posts(self):
        earlier = self.draft()
        self.service.book.change(earlier, 'sent', text='把角色技能和战斗里的节奏放一起看还挺有意思')
        payload = {'post_topic': '战斗节奏', 'visual_concept': '技能时间轴', 'assets': ['existing']}
        self.store.db.execute('UPDATE moments_actions SET payload=? WHERE id=?', (json.dumps(payload), earlier))
        other = self.service.book.create('other', 'publish', 'other', {})
        self.service.book.change(other, 'sent', text='另一个账号的内容')
        cancelled = self.draft()
        self.service.book.change(cancelled, 'cancelled', text='已取消的内容')
        expired = self.draft()
        self.service.book.change(expired, 'sent', text='很久以前的内容')
        self.store.db.execute('UPDATE moments_actions SET updated=? WHERE id=?', (time.time()-9*86400, expired))
        self.store.db.commit()
        self.service = MomentsService(self.runtime)
        self.generation_fixture({'participate': True, 'text': '今晚想聊聊完全不同的电影配乐',
            'media_mode': 'library', 'asset_ids': ['existing']})
        row = await self.service.generate('publish')
        history = self.model_inputs[0][0]['recent_publications']
        self.assertEqual([item['id'] for item in history], [earlier])
        self.assertEqual(history[0]['topic'], '战斗节奏')
        self.assertTrue(self.model_inputs[0][0]['image_library'][0]['recently_used'])
        self.assertEqual(row['state'], 'draft')
        self.assertEqual(json.loads(row['payload'])['assets'], [])
        self.assertFalse(self.jobs)

    async def test_near_duplicate_text_skips_before_spending_image_budget(self):
        earlier = self.draft()
        self.service.book.change(earlier, 'sent', text='今天又在窗边看雨，看着看着就开始发呆啦')
        self.generation_fixture({'participate': True, 'text': '今天又在窗边看雨，看着看着就开始发呆了',
            'media_mode': 'generate', 'image_prompt': '窗边看雨的女孩'})
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'skipped')
        self.assertIn('高度相似', row['error'])
        self.assertFalse(self.jobs)

    async def test_reused_image_prompt_skips_even_with_different_caption(self):
        earlier = self.service.book.create('account', 'publish', 'old', {
            'image_prompt': '蓝发少女坐在窗边捧着热茶凝视雨滴，侧面半身构图，柔和蓝灰色调'})
        self.service.book.change(earlier, 'sent', text='这杯热茶挺适合下雨天')
        self.generation_fixture({'participate': True, 'text': '最近游戏抽卡的运气有点神奇',
            'media_mode': 'generate', 'image_prompt': '蓝发少女坐在窗边捧着热茶凝视雨滴，侧面半身构图，柔和蓝灰色调'})
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'skipped')
        self.assertIn('配图构思', row['error'])
        self.assertFalse(self.jobs)

    async def test_synced_manual_post_is_used_for_duplicate_filter(self):
        self.service.posts = [{'own': True, 'created': time.time(), 'text': '这次的关卡设计真的很有意思，尤其是最后那段转折'}]
        self.generation_fixture({'participate': True, 'text': self.service.posts[0]['text']})
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'skipped')

    async def test_generation_quota_race_preserves_text_and_does_not_publish_without_picture(self):
        self.generation_fixture({'participate': True, 'text': '一起看看这个奇妙的新场景',
            'media_mode': 'generate', 'image_prompt': '雨后虹色城市的俯瞰图'})
        def full(**kwargs): raise ValueError('今日生图任务已达上限')
        self.runtime.images.enqueue = full
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'image_failed')
        self.assertEqual(row['text'], '一起看看这个奇妙的新场景')
        self.assertFalse(self.calls)

    async def test_auto_picture_draft_resumes_when_ready_without_publishing_old_or_manual_drafts(self):
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_mode': 'auto', 'moments_reply_own_enabled': False})
        now = time.time()
        self.service.book.save_state('account', {'last_poll': now})
        manual = self.draft()
        self.service.book.change(manual, 'draft', text='手动草稿不自动发')
        old = self.service.book.create('account', 'publish', 'old-auto',
            {'auto_publish': True, 'media_mode': 'generate', 'assets': ['old-picture']})
        self.service.book.change(old, 'draft', text='过期任务不自动发')
        self.store.db.execute('UPDATE moments_actions SET created=? WHERE id=?', (now-86400, old))
        waiting = self.service.book.create('account', 'publish', 'new-auto',
            {'auto_publish': True, 'media_mode': 'generate', 'assets': []})
        self.service.book.change(waiting, 'awaiting_image', text='新图完成后发布这一条')
        self.store.db.commit()
        await self.service.tick()
        self.assertFalse(self.calls)
        self.store.db.execute("UPDATE moments_actions SET state='draft',payload=? WHERE id=?",
            (json.dumps({'auto_publish': True, 'media_mode': 'generate', 'assets': ['new-picture']}), waiting))
        self.store.db.commit()
        self.runtime.images = SimpleNamespace(stage=lambda *args, **kwargs: None)
        async def send(operation, account, **values):
            self.calls.append(values)
            return {'confirmed': True, 'receipt': 'new-post-id'}
        self.runtime.gateway.moments = send
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.service.book.get('account', waiting)['state'], 'sent')
        self.assertEqual(self.service.book.get('account', manual)['state'], 'draft')
        self.assertEqual(self.service.book.get('account', old)['state'], 'draft')
        await self.service.tick()
        self.assertEqual(len(self.calls), 1)

    def test_variety_comparison_ignores_punctuation_and_accepts_distinct_topics(self):
        self.assertTrue(similar_publication('今天想摸鱼！', '今天想摸鱼'))
        self.assertFalse(similar_publication('聊聊今天的新角色技能', '电影里的配乐选得真有意思'))
        self.assertFalse(similar_publication('早上好', '晚上好'))

    async def test_publish_schedule_divides_active_window_into_random_slots_without_piling_up(self):
        from datetime import datetime
        from whalegirl_linux.moments import TZ
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_mode': 'auto', 'moments_publish_enabled': True, 'moments_publish_per_day': 3,
            'moments_reply_own_enabled': False, 'moments_start_hour': 0, 'moments_end_hour': 0})
        day_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        plan = self.service._build_publish_plan(self.runtime.config.settings, now=day_start + 10)
        self.assertEqual(len(plan['slots']), 3)
        for i, sl in enumerate(plan['slots']):
            self.assertAlmostEqual(sl['start'], day_start + i * 28800, delta=1.0)
            self.assertAlmostEqual(sl['end'], day_start + (i + 1) * 28800, delta=1.0)
            self.assertGreaterEqual(sl['target'], sl['start'] + 28800 * 0.15 - 1.0)
            self.assertLessEqual(sl['target'], sl['end'] - 28800 * 0.15 + 1.0)
            self.assertFalse(sl['done'])
        self.assertGreaterEqual(plan['slots'][1]['target'] - plan['slots'][0]['target'], 28800 * 0.30 - 2.0)
        self.assertGreaterEqual(plan['slots'][2]['target'] - plan['slots'][1]['target'], 28800 * 0.30 - 2.0)

        # Verify tick() does not pile up posts on consecutive 15-minute polls
        now = time.time()
        saved = {'last_poll': 0, 'publish_plan': {
            'day': datetime.fromtimestamp(now, TZ).strftime('%Y-%m-%d'),
            'per_day': 3, 'start_hour': 0, 'end_hour': 0,
            'slots': [
                {'index': 0, 'start': now - 600, 'end': now + 7200, 'target': now - 10, 'attempts': 0, 'done': False},
                {'index': 1, 'start': now + 7200, 'end': now + 14400, 'target': now + 9000, 'attempts': 0, 'done': False},
                {'index': 2, 'start': now + 14400, 'end': now + 21600, 'target': now + 18000, 'attempts': 0, 'done': False},
            ],
        }}
        self.service.book.save_state('account', saved)
        generated_sources = []

        async def fake_generate(kind, payload=None, source=None, *, auto_publish=False):
            generated_sources.append(source)
            identity = self.service.book.create('account', kind, source, {'auto_publish': auto_publish})
            self.service.book.change(identity, 'draft', text=f'动态-{len(generated_sources)}')
            return self.service.book.get('account', identity)

        async def fake_moments(operation, account, **values):
            self.calls.append((operation, account, values))
            if operation in ('moments_probe', 'moments_read'):
                return {'posts': [], 'friends': [], 'capabilities': {'read': True}}
            return {'confirmed': True, 'receipt': f'rcpt-{len(self.calls)}'}

        self.service.generate = fake_generate
        self.runtime.gateway.moments = fake_moments
        await self.service.tick()
        self.assertEqual(len(generated_sources), 1)
        self.assertTrue(generated_sources[0].endswith('/0'))

        # Force another poll 15 minutes later; Slot 1 is still in the future so it MUST NOT fire
        st = self.service.book.state('account')
        st['last_poll'] = 0
        self.service.book.save_state('account', st)
        await self.service.tick()
        self.assertEqual(len(generated_sources), 1)

    async def test_publish_slot_failure_retries_with_distinct_source(self):
        from datetime import datetime
        from whalegirl_linux.moments import TZ
        self.runtime.config.settings = self.settings.model_copy(update={
            'moments_mode': 'auto', 'moments_publish_enabled': True, 'moments_publish_per_day': 2,
            'moments_reply_own_enabled': False, 'moments_poll_minutes': 5})
        now = time.time()
        day = datetime.fromtimestamp(now, TZ).strftime('%Y-%m-%d')
        slot = {'index': 0, 'start': now - 600, 'end': now + 7200, 'target': now - 5, 'attempts': 0, 'done': False}
        saved = {'publish_plan': {'day': day, 'per_day': 2, 'start_hour': 0, 'end_hour': 0, 'slots': [slot]}}

        sources = []
        async def flaky_generate(kind, payload=None, source=None, *, auto_publish=False):
            sources.append(source)
            identity = self.service.book.create('account', kind, source, {})
            if len(sources) == 1:
                self.service.book.change(identity, 'failed', error='生成失败，未提交微信')
            else:
                self.service.book.change(identity, 'draft', text='重试成功')
            return self.service.book.get('account', identity)

        async def fake_send(identity):
            self.service.book.change(identity, 'sent', receipt='ok')
            return self.service.book.get('account', identity)

        self.service.generate = flaky_generate
        self.service.send = fake_send

        await self.service.run_publish_slot(self.runtime.config.settings, saved, slot, now=now)
        self.assertEqual(sources, [f'{day}/0'])
        self.assertFalse(slot['done'])
        self.assertEqual(slot['attempts'], 1)
        self.assertGreater(slot['target'], now)

        await self.service.run_publish_slot(self.runtime.config.settings, saved, slot, now=slot['target'] + 1)
        self.assertEqual(sources, [f'{day}/0', f'{day}/0/r2'])
        self.assertTrue(slot['done'])
        self.assertEqual(self.service.book.count('account', 'publish'), 1)

    async def test_unescaped_inner_quotes_in_moments_json_are_recovered(self):
        from whalegirl_linux.moments import parse_moments_json
        raw = '{\n  "participate": true,\n  "text": "看到有人分析"为什么周一总是困"，困就是困啊",\n  "media_mode": "none"\n}'
        parsed = parse_moments_json(raw)
        self.assertTrue(parsed['participate'])
        self.assertEqual(parsed['text'], '看到有人分析"为什么周一总是困"，困就是困啊')

    async def test_timeout_generation_failure_can_auto_retry_and_manual_retry(self):
        post = self.cached_comments()
        comment = post['comments'][0]
        payload = {'post': post, 'comment': comment}
        failed_id = self.service.book.create('account', 'reply_own', 'post/1', payload)
        self.service.book.change(failed_id, 'failed', text='', error='生成失败，未提交微信：请求超时 (20s)')
        self.store.db.execute('UPDATE moments_actions SET updated=? WHERE id=?', (time.time() - 120, failed_id))
        self.store.db.commit()
        await self.service.tick()
        row = self.service.book.get('account', failed_id)
        self.assertEqual(row['state'], 'sent')
        self.assertEqual(row['text'], '回复')
        self.assertEqual(json.loads(row['payload']).get('gen_attempts'), 2)


class MomentsQueueApiTests(unittest.TestCase):
    def test_click_publish_queues_and_edit_withdraws_request_without_deleting_draft(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from test_proactive import Gateway
        folder = Path(tempfile.mkdtemp(prefix='moments-queue-api-', dir=ROOT/'test-runs'))
        gateway = Gateway()
        gateway.ui_lock, gateway.inflight_sends = asyncio.Lock(), set()
        app = create_app(folder, gateway=gateway, run_background=False)
        runtime, config = app.state.runtime, app.state.config
        config.save(config.settings.model_copy(update={
            'enabled':True, 'account_id':'account', 'allowed_chat_ids':['friend'], 'moments_enabled':True,
            'moments_publish_enabled':True, 'moments_publish_per_day':5}), 'fixture-key')
        runtime.account_id, runtime.gateway_state = 'account', {'status':'logged_in'}
        runtime.processing = 'chat'
        book = runtime.moments.book
        identity = book.create('account', 'publish', 'manual', {'auto_publish':True})
        book.change(identity, 'draft', text='保留为草稿')
        client = TestClient(app)
        headers = {'Authorization':'Bearer '+config.admin_token}
        response = client.post('/api/moments/actions/'+identity, headers=headers, json={'op':'publish'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(next(r for r in response.json()['actions'] if r['id'] == identity)['queued'])
        response = client.post('/api/moments/actions/'+identity, headers=headers,
                               json={'op':'edit','text':'修改后保留'})
        self.assertEqual(response.status_code, 200, response.text)
        row = book.get('account', identity)
        self.assertEqual((row['state'], row['text']), ('draft', '修改后保留'))
        self.assertNotIn('publish_requested_at', json.loads(row['payload']))
        self.assertFalse(json.loads(row['payload'])['auto_publish'])
        self.assertFalse(gateway.sent)
        asyncio.run(runtime.close())
        runtime.store.close()


if __name__ == '__main__':
    unittest.main()

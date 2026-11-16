"""Observed virtualized comment blocks, transient send failures and refresh recovery."""
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
import moments_ui

from whalegirl_linux.gateway import UIJobError
from tests import test_moments as fixtures


class VirtualCommentBlockTests(unittest.TestCase):
    def view(self, *, y=571, following=False, other=False):
        post = {'role': 'list-item', 'name': 'different post' if other else 'unique source post',
                'bounds': {'x': 366, 'y': y, 'width': 548, 'height': 528}}
        rows = [post]
        if following:
            rows.append({'role': 'list-item', 'name': 'comments',
                'bounds': {'x': 366, 'y': y+528, 'width': 548, 'height': 47}})
        return {'role': 'frame', 'name': 'Moments', 'children': [{'role': 'list',
            'bounds': {'x': 366, 'y': 156, 'width': 548, 'height': 508}, 'children': rows}]}

    def test_open_moments_minimizes_existing_editor_without_touching_draft(self):
        editor = {'role':'text','name':'','states':['EDITABLE'],
                  'bounds':{'x':0,'y':0,'width':300,'height':80}}
        draft_frame = {'role':'frame','name':'Moments','children':[editor]}
        main = {'role':'frame','name':'Main','children':[
            {'role':'push-button','name':'Moments'}]}
        timeline = {'role':'frame','name':'Moments','children':[
            {'role':'push-button','name':'Messages'}]}
        snapshots = iter([draft_frame, main])
        def tree():
            return next(snapshots, timeline)
        clicks = []
        result = type('Result', (), {'stdout':b'123\n'})()
        with patch.object(moments_ui,'tree',side_effect=tree), \
             patch.object(moments_ui,'command',return_value=result), \
             patch.object(moments_ui,'click_node',side_effect=lambda node: clicks.append(node)), \
             patch.object(moments_ui.time,'sleep'), \
             patch('native_recovery.minimize_moments_editor') as minimize:
            opened = moments_ui.open_moments()
        minimize.assert_called_once()
        self.assertIs(minimize.call_args.args[1], draft_frame)
        self.assertEqual([node.get('name') for node in clicks], ['Moments'])
        self.assertIs(opened, timeline)

    def test_publish_does_not_hide_or_replace_an_existing_draft(self):
        editor = {'role':'text','name':'','states':['EDITABLE'],
                  'bounds':{'x':0,'y':0,'width':300,'height':80}}
        draft_frame = {'role':'frame','name':'Moments','children':[editor]}
        clicks = []
        with patch.object(moments_ui,'tree',return_value=draft_frame), \
             patch.object(moments_ui,'click_node',side_effect=lambda node: clicks.append(node)), \
             patch('native_recovery.minimize_moments_editor') as minimize:
            with self.assertRaisesRegex(ValueError,'preserve the draft'):
                moments_ui.open_moments(minimize_existing_editor=False)
        minimize.assert_not_called()
        self.assertEqual(clicks, [])

    def test_verified_post_at_bottom_scrolls_until_its_comment_object_is_created(self):
        step = [0]
        def snapshot():
            return self.view(y=571-step[0]*100, following=step[0] >= 5)
        def scroll(*args):
            self.assertEqual(args[-1], '5')
            step[0] += 1
        with patch.object(moments_ui, 'frame', side_effect=snapshot), \
             patch.object(moments_ui, 'command', side_effect=scroll), \
             patch.object(moments_ui, 'activate_moments'), patch.object(moments_ui.time, 'sleep'):
            bounds, area = moments_ui.bind_timeline_comments({'text': 'unique source post'})
        self.assertEqual(step[0], 5)
        self.assertEqual(bounds['height'], 47)
        self.assertLess(bounds['y'], area['y']+area['height'])

    def test_missing_original_never_scrolls_or_adopts_some_other_comment_block(self):
        with patch.object(moments_ui, 'frame', return_value=self.view(other=True)), \
             patch.object(moments_ui, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'not confirmed'):
                moments_ui.bind_timeline_comments({'text': 'unique source post'})
        command.assert_not_called()

    def test_comment_block_loading_has_a_finite_scroll_limit(self):
        with patch.object(moments_ui, 'frame', return_value=self.view()), \
             patch.object(moments_ui, 'command') as command, \
             patch.object(moments_ui, 'activate_moments'), patch.object(moments_ui.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'bounded scrolling'):
                moments_ui.bind_timeline_comments({'text': 'unique source post'})
        self.assertEqual(command.call_count, 10)

    def test_history_past_old_seven_step_limit_can_find_exact_original(self):
        step = [0]
        def snapshot():
            return self.view(y=200, other=step[0] < 9)
        def scroll(*args):
            if args[-1] == '5':
                step[0] += 1
        post = {'id': 'post', 'text': 'unique source post'}
        with patch.object(moments_ui, 'frame', side_effect=snapshot), \
             patch.object(moments_ui, 'command', side_effect=scroll), \
             patch.object(moments_ui.time, 'sleep'):
            row = moments_ui.locate_post(post, {'posts': [post]}, allow_partial=True)
        self.assertEqual(step[0], 9)
        self.assertEqual(row['name'], post['text'])


class MomentsTransportRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT / 'test-runs').mkdir(exist_ok=True)
        fixtures.MomentsTests.setUp(self)

    tearDown = fixtures.MomentsTests.tearDown
    draft = fixtures.MomentsTests.draft
    cached_comments = fixtures.MomentsTests.cached_comments

    async def test_resource_failure_before_submit_retries_same_text_at_most_twice(self):
        self.runtime.config.settings = self.settings.model_copy(update={'moments_publish_per_day': 1})
        calls = []
        async def native(operation, account, **kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise UIJobError('[Errno 11] Resource temporarily unavailable', 'failed_before_send')
            return {'confirmed': True, 'receipt': 'native-post'}
        self.runtime.gateway.moments = native
        with patch('whalegirl_linux.moments.asyncio.sleep', return_value=None):
            result = await self.service.send(self.draft())
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(result['receipt'], 'native-post')
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c == calls[0] for c in calls))

    async def test_repeated_definite_resource_failure_stays_failed_and_stops(self):
        calls = []
        async def native(*args, **kwargs):
            calls.append(kwargs)
            raise UIJobError('[Errno 11] Resource temporarily unavailable', 'failed_before_send')
        self.runtime.gateway.moments = native
        with patch('whalegirl_linux.moments.asyncio.sleep', return_value=None):
            result = await self.service.send(self.draft())
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(len(calls), 3)
        self.assertFalse(result['receipt'])

    async def test_submitted_or_unknown_resource_error_is_never_replayed(self):
        for state in ('sending', 'submitted', ''):
            with self.subTest(state=state):
                calls = []
                async def native(*args, **kwargs):
                    calls.append(kwargs)
                    raise UIJobError('[Errno 11] Resource temporarily unavailable', state)
                self.runtime.gateway.moments = native
                result = await self.service.send(self.draft())
                self.assertEqual(result['state'], 'uncertain')
                self.assertEqual(len(calls), 1)
                self.service.book.change(result['id'], 'cancelled')

    async def test_unclassified_os_error_is_never_assumed_to_be_before_submit(self):
        async def native(*args, **kwargs):
            raise OSError(errno.EAGAIN, 'busy')
        self.runtime.gateway.moments = native
        result = await self.service.send(self.draft())
        self.assertEqual(result['state'], 'uncertain')

    async def test_switch_off_during_backoff_preserves_unsent_draft(self):
        calls = []
        async def native(*args, **kwargs):
            calls.append(kwargs)
            self.runtime.config.settings = self.settings.model_copy(update={'moments_publish_enabled': False})
            raise UIJobError('[Errno 11] Resource temporarily unavailable', 'failed_before_send')
        self.runtime.gateway.moments = native
        with patch('whalegirl_linux.moments.asyncio.sleep', return_value=None):
            result = await self.service.send(self.draft())
        self.assertEqual(result['state'], 'draft')
        self.assertEqual(len(calls), 1)

    async def test_ui_refresh_failure_keeps_cache_comments_working_without_advancing_last_poll(self):
        self.cached_comments()
        saved = self.service.book.state('account')
        saved['last_poll'] = 1
        self.service.book.save_state('account', saved)
        async def fail():
            raise UIJobError('temporarily busy', 'failed_before_send')
        self.service.scan = fail
        await self.service.tick()
        result = self.service.book.find_source('account', 'reply_own', 'post/1')
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(self.service.book.state('account')['last_poll'], 1)
        self.assertEqual(self.cache_reads, ['/moments'])
        self.assertIn('刷新失败', self.service.error)

    async def test_refresh_recovers_next_tick_and_then_updates_last_poll(self):
        self.cached_comments()
        saved = self.service.book.state('account')
        saved['last_poll'] = 1
        self.service.book.save_state('account', saved)
        calls = []
        async def scan():
            calls.append(1)
            if len(calls) == 1:
                raise OSError(errno.EAGAIN, 'busy')
            return {'posts': []}
        self.service.scan = scan
        await self.service.tick()
        self.assertEqual(self.service.book.state('account')['last_poll'], 1)
        await self.service.tick()
        self.assertEqual(len(calls), 2)
        self.assertGreater(self.service.book.state('account')['last_poll'], 1)

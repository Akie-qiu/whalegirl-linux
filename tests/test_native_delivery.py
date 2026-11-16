"""Regressions for observed process exhaustion and native popup interference."""
import errno
from pathlib import Path
import subprocess
import sys
import json
import tempfile
from collections import deque
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
import native_process
import chat_select_linux as ui
import moments_ui


class NativeProcessTests(unittest.TestCase):
    def test_only_failed_spawn_is_retried(self):
        with patch.object(native_process.subprocess,'run',side_effect=[OSError(errno.EAGAIN,'busy'),SimpleNamespace(returncode=0)]) as run,patch.object(native_process.time,'sleep'):
            self.assertEqual(native_process.run(['click']).returncode,0)
            self.assertEqual(run.call_count,2)

    def test_started_process_failure_and_timeout_are_not_replayed(self):
        for error in (subprocess.CalledProcessError(1,['click']),subprocess.TimeoutExpired(['click'],1)):
            with patch.object(native_process.subprocess,'run',side_effect=error) as run:
                with self.assertRaises(type(error)):native_process.run(['click'])
                self.assertEqual(run.call_count,1)


class NativePopupTests(unittest.TestCase):
    def test_sticker_button_name_accepts_old_and_new_wechat_capitalization(self):
        opened={'role':'frame','children':[{'role':'page-tab','name':'Custom Stickers'}]}
        for label in ('Send sticker(Alt+E)','Send Sticker(Alt+E)'):
            button={'role':'push-button','name':label,'bounds':{'x':20,'y':40,'width':28,'height':28}}
            initial={'role':'frame','children':[button]}
            with self.subTest(label=label), patch.object(ui,'tree',return_value=opened), \
                 patch.object(ui,'selected',return_value=True), \
                 patch.object(ui,'run_process') as command, \
                 patch.object(ui,'click_node') as click, patch.object(ui.time,'sleep'):
                self.assertIs(ui.ensure_favorite_panel(initial,'临时测试',True),opened)
            click.assert_called_once_with(button)
            command.assert_not_called()

    def test_missing_or_duplicate_sticker_button_uses_verified_hotkey(self):
        opened={'role':'frame','children':[{'role':'page-tab','name':'Custom Stickers'}]}
        for count in (0,2):
            controls=[{'role':'push-button','name':'Send sticker(Alt+E)'} for _ in range(count)]
            initial={'role':'frame','children':controls}
            with self.subTest(button_count=count), \
                 patch.object(ui,'tree',return_value=opened), \
                 patch.object(ui,'selected',return_value=True), \
                 patch.object(ui,'run_process') as command, \
                 patch.object(ui,'click_node') as click, \
                 patch.object(ui.time,'sleep'):
                self.assertIs(ui.ensure_favorite_panel(initial,'临时测试',True),opened)
            click.assert_not_called()
            command.assert_called_once_with(['xdotool','key','--clearmodifiers','alt+e'],
                                            check=True,timeout=3)

    def test_hotkey_does_not_continue_if_destination_or_panel_is_unverified(self):
        initial={'role':'frame','children':[]}
        with patch.object(ui,'tree',return_value={'role':'frame','children':[]}), \
             patch.object(ui,'selected',return_value=True), \
             patch.object(ui,'run_process'), patch.object(ui.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'favorite panel did not open'):
                ui.ensure_favorite_panel(initial,'临时测试',True)
        with patch.object(ui,'tree',return_value={'role':'frame','children':[]}), \
             patch.object(ui,'selected',return_value=False), \
             patch.object(ui,'run_process'), patch.object(ui.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'Destination changed'):
                ui.ensure_favorite_panel(initial,'临时测试',True)

    def test_rebuilt_favorite_panel_requires_selected_destination_and_unique_ref(self):
        root={'children':[{'role':'list','native_ref':':1.8|/org/a11y/atspi/accessible/88',
                           'children':[{'name':'Custom Stickers'}]}]}
        with patch.object(ui,'selected',return_value=True):
            self.assertEqual(ui.rebind_favorite_panel(root,'临时测试',True),
                             ':1.8|/org/a11y/atspi/accessible/88')
        with patch.object(ui,'selected',return_value=False):
            with self.assertRaisesRegex(ValueError,'Destination changed'):
                ui.rebind_favorite_panel(root,'临时测试',True)
        duplicate={'children':root['children']*2}
        with patch.object(ui,'selected',return_value=True):
            with self.assertRaisesRegex(ValueError,'not unique'):
                ui.rebind_favorite_panel(duplicate,'临时测试',True)

    def test_rebuilt_panel_drops_stale_anchors_then_realigns_from_verified_edge(self):
        old_panel=':1.7|/org/a11y/atspi/accessible/77'
        new_panel=':1.8|/org/a11y/atspi/accessible/88'
        order=[f'{i:032x}' for i in range(1,21)]
        def page(first,panel_ref):
            tiles=[{'role':'label','name':'Custom Stickers','native_ref':'tile:'+str(index),
                    'bounds':{'x':index%5*70,'y':10+(index-first)//5*70,'width':60,'height':60}}
                   for index in range(first,first+10)]
            listing={'role':'list','name':'Favorites','native_ref':panel_ref,
                     'bounds':{'x':0,'y':0,'width':400,'height':90},
                     'children':[{'name':'Custom Stickers'},*tiles]}
            return {'role':'frame','children':[{'role':'page-tab','name':'Custom Stickers'},listing]}
        initial=page(10,old_panel)
        rebuilt=page(0,new_panel)
        def snapshot(sticker_panel_ref=None):
            if sticker_panel_ref==old_panel:
                raise ValueError('Native sticker panel reference is stale or is not a sticker panel')
            return next(iter(rebuilt['children'][1:])) if sticker_panel_ref else rebuilt
        path=Path(tempfile.mkdtemp(prefix='sticker-rebind-'))/'job.json'
        (path.parent/'sticker-panel-anchors.cache').write_text(json.dumps({
            'panel_ref':old_panel,'order':order,'updated':__import__('time').time(),
            'anchors':{'tile:'+str(index):index for index in range(10,20)},
            'grid':{'xs':[0,70,140,210,280],'shift':0}}))
        job={'md5':order[3],'chat':'test'}
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':value} for value in order]}), \
             patch.object(ui,'tree',side_effect=snapshot),patch.object(ui,'selected',return_value=True), \
             patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'), \
             patch.dict(ui.PROFILE,{'snapshots':0,'snapshot_seconds':0},clear=True):
            ui.send_native_sticker(job,path,initial,'test',True)
            self.assertEqual(ui.PROFILE['panel_rebinds'],1)
        self.assertEqual(job['state'],'submitted')
        clicks=[call.args[0] for call in command.call_args_list if call.args[0][0]=='/opt/tools/click']
        self.assertEqual(clicks,[['/opt/tools/click','240','40']])

    def test_sticker_anchor_cache_reuses_visible_page_without_rescanning(self):
        order=[f'{i:032x}' for i in range(1,65)]
        listing={'role':'list','native_ref':'favorite-panel','bounds':{'x':0,'y':0,'width':600,'height':90},
                 'children':[{'role':'label','name':'Custom Stickers','native_ref':f'tile:{i}',
                    'bounds':{'x':i*70,'y':10,'width':60,'height':60}} for i in range(5)]}
        root={'children':[{'role':'page-tab','name':'Custom Stickers'},listing]}
        folder=Path(tempfile.mkdtemp(prefix='sticker-anchors-'));path=folder/'job.json'
        import time
        (folder/'sticker-panel-anchors.cache').write_text(json.dumps({'schema':2,'session':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'page':[list(row) for row in ui.favorite_page_signature(listing['children'])],
            'panel_ref':'favorite-panel',
            'order':order,'updated':time.time(),'anchors':{f'tile:{i}':30+i for i in range(5)},
            'grid':{'xs':[0,70,140,210,280],'shift':0}}))
        job={'md5':order[32],'chat':'test','fast_session_nonce':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}
        (path.parent/('a'*32+'.guard.json')).write_text(json.dumps({'chat':'test','allowed':True,'updated':time.time()}))
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree') as snapshot,patch.object(ui,'selected',return_value=True),\
             patch.object(ui,'run_process') as command:
            ui.send_native_sticker(job,path,root,'test',True)
        self.assertEqual(job['state'],'submitted');self.assertEqual(job['favorite_cache'],'anchor_hit')
        snapshot.assert_not_called()
        self.assertEqual(sum(c.args[0][0]=='/opt/tools/click' for c in command.call_args_list),1)

    def test_sticker_scroll_recovers_from_ambiguous_virtual_page_without_guessing(self):
        # A five-column grid of 20 favorites; the viewport shows one row and Qt
        # exposes one more. Index 8 is not on the confirmed top page, so it is
        # approached upward from the confirmed bottom. An ambiguous page (a
        # duplicated object) must be skipped rather than used to guess.
        def page(first,top_y,duplicate=False):
            tiles=[]
            for index in range(first,first+10):
                ref='tile:'+str(index)
                if duplicate and index==first+3:ref='tile:'+str(first+7)
                tiles.append({'role':'label','name':'Custom Stickers','native_ref':ref,
                    'bounds':{'x':index%5*70,'y':top_y+(index-first)//5*70,'width':60,'height':60}})
            return {'role':'frame','children':[
                {'role':'page-tab','name':'Custom Stickers'},
                {'role':'list','bounds':{'x':0,'y':0,'width':400,'height':90},
                 'children':tiles}]}
        top,bottom=page(0,10),page(10,-60)
        # A moving virtualized page is sampled more than once now: the first
        # snapshots can contain recycled/animated bounds, then the settled page
        # must repeat before index alignment is trusted.
        settled=page(5,10)
        pages=deque([top,top,top,bottom,bottom,bottom,page(5,10,duplicate=True),settled,settled])
        def snapshot():
            return pages.popleft() if pages else settled
        path=Path(tempfile.mkdtemp(prefix='sticker-scroll-'))/'job.json'
        order=[f'{i:032x}' for i in range(1,21)]
        job={'md5':order[8],'chat':'test'}
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}), \
             patch.object(ui,'tree',side_effect=snapshot), \
             patch.object(ui,'selected',return_value=True), \
             patch.object(ui,'run_process') as command, \
             patch.object(ui.time,'sleep'):
            ui.send_native_sticker(job,path,top,'test',True)
        self.assertEqual(job['state'],'submitted')
        self.assertEqual(job['favorite_grid'],{'cols':5,'shift':0})
        self.assertEqual(job['favorite_scroll'],[[0,5,10]])
        self.assertFalse(pages, 'the transient page should settle without exhausting the simulated UI')
        clicks=[call.args[0] for call in command.call_args_list if call.args[0][0]=='/opt/tools/click']
        self.assertEqual(clicks,[['/opt/tools/click','240','40']])  # favorite 8: column 3, row 1
        self.assertEqual(json.loads(path.read_text())['state'],'sending')

    def test_failed_sticker_always_closes_popup(self):
        with patch.object(ui,'_send_native_sticker',side_effect=ValueError('page')) as send,patch.object(ui,'run_process') as command:
            with self.assertRaisesRegex(ValueError,'page'):ui.send_native_sticker({},None,{},'name',False)
            self.assertEqual(send.call_count,1)
            self.assertIn('Escape',command.call_args.args[0])

    def test_moments_dismisses_sticker_overlay_before_sidebar_click(self):
        events=[]
        sidebar={'role':'push-button','name':'Moments','bounds':{'x':0,'y':0,'width':10,'height':10}}
        popup={'role':'page-tab','name':'Custom Stickers'}
        timeline={'role':'frame','name':'Moments','children':[{'role':'push-button','name':'Messages'}]}
        roots=iter([{'children':[sidebar,popup]},{'children':[sidebar]},timeline,timeline,timeline,timeline])
        def command(*args,**kwargs):events.append('Escape' if 'Escape' in args else args[1]);return SimpleNamespace(stdout=b'1\n')
        with patch.object(moments_ui,'tree',side_effect=lambda:next(roots,timeline)),patch.object(moments_ui,'command',side_effect=command),patch.object(moments_ui,'click_node',side_effect=lambda n:events.append('click '+n['name'])),patch.object(moments_ui.time,'sleep'):
            moments_ui.open_moments()
        self.assertLess(events.index('Escape'),events.index('click Moments'))


class SnapshotContentionTests(unittest.TestCase):
    def test_busy_snapshot_retries_only_the_read_not_the_picker_action(self):
        opened={'role':'frame','children':[{'role':'page-tab','name':'Custom Stickers'}]}
        busy=subprocess.CalledProcessError(1,['a11y-dump'],output='{"error":"TimeoutError"}')
        with patch.object(ui,'run_process',side_effect=[busy,SimpleNamespace(stdout=json.dumps(opened))]) as read, \
             patch.object(ui,'selected',return_value=True), patch.object(ui,'click_node') as click, \
             patch.object(ui.time,'sleep'),patch.object(ui.time,'monotonic',return_value=0), \
             patch.dict(ui.PROFILE,{'snapshots':0,'snapshot_seconds':0},clear=True):
            initial={'children':[{'role':'push-button','name':'Send Sticker(Alt+E)'}]}
            self.assertEqual(ui.ensure_favorite_panel(initial,'test',True),opened)
            self.assertEqual(ui.PROFILE['snapshot_contention_retries'],1)
        self.assertEqual(read.call_count,2)
        self.assertTrue(all(call.args[0][0]=='/opt/tools/a11y-dump' for call in read.call_args_list))
        click.assert_called_once()

    def test_partial_snapshot_busy_read_recovers_without_a_ui_command(self):
        reader=ModuleType('native_panel_reader')
        from unittest.mock import Mock
        reader.read=Mock(side_effect=[TimeoutError('Accessibility reader busy'),{'role':'list'}])
        with patch.dict(sys.modules,{'native_panel_reader':reader}), \
             patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'), \
             patch.object(ui.time,'monotonic',return_value=0), \
             patch.dict(ui.PROFILE,{'snapshots':0,'snapshot_seconds':0},clear=True):
            self.assertEqual(ui.tree(sticker_panel_ref='bus|/panel'),{'role':'list'})
            self.assertEqual(ui.PROFILE['snapshot_contention_retries'],1)
        self.assertEqual(reader.read.call_count,2)
        command.assert_not_called()

    def test_persistent_contention_stops_after_three_read_attempts(self):
        busy=subprocess.CalledProcessError(1,['a11y-dump'],output='{"error":"TimeoutError"}')
        with patch.object(ui,'run_process',side_effect=busy) as read,patch.object(ui.time,'sleep'), \
             patch.object(ui.time,'monotonic',return_value=0), \
             patch.dict(ui.PROFILE,{'snapshots':0,'snapshot_seconds':0},clear=True):
            with self.assertRaises(subprocess.CalledProcessError):ui.tree()
        self.assertEqual(read.call_count,3)

    def test_slow_traversal_timeout_is_not_retried(self):
        failure=subprocess.CalledProcessError(1,['a11y-dump'],output='{"error":"TimeoutError"}')
        with patch.object(ui,'run_process',side_effect=failure) as read, \
             patch.object(ui.time,'monotonic',side_effect=[0,0,6.5]):
            with self.assertRaises(subprocess.CalledProcessError):ui.tree()
        read.assert_called_once()

    def test_other_read_failures_are_not_retried(self):
        failures=[subprocess.CalledProcessError(1,['a11y-dump'],output=value)
                  for value in ('{"error":"ValueError"}','[]','unparseable')]
        failures.append(subprocess.TimeoutExpired(['a11y-dump'],8))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), \
                 patch.object(ui,'run_process',side_effect=failure) as read:
                with self.assertRaises(type(failure)):ui.tree()
            read.assert_called_once()


class ChatListNavigationTests(unittest.TestCase):
    def root(self, *names):
        return {'role':'frame','children':[{'role':'list','name':'Chats',
            'bounds':{'x':50,'y':100,'width':200,'height':500},
            'children':[{'role':'list-item','name':name,'bounds':{'x':50,'y':100+i*45,'width':200,'height':45}}
                        for i,name in enumerate(names)]}]}

    def test_offscreen_target_is_found_by_scrolling_only_chat_list(self):
        top=self.root('first','second')
        with patch.object(ui,'tree',side_effect=[top,top,top,self.root('third','临时测试')]),\
             patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'):
            target=ui.locate_chat(self.root('other'),'临时测试')
        self.assertEqual(target['name'],'临时测试')
        self.assertEqual([c.args[0][-1] for c in command.call_args_list],['4','4','4','5'])
        self.assertTrue(all('Return' not in c.args[0] for c in command.call_args_list))

    def test_visible_target_needs_no_extra_snapshot(self):
        with patch.object(ui,'tree') as snapshot,patch.object(ui,'run_process') as command:
            self.assertEqual(ui.locate_chat(self.root('临时测试'),'临时测试')['name'],'临时测试')
        snapshot.assert_not_called();command.assert_not_called()

    def test_coalesced_up_scroll_and_one_unchanged_frame_do_not_skip_newer_chats(self):
        middle=self.root('middle')
        pages=[middle,middle,self.root('near top'),self.root('临时测试')]
        with patch.object(ui,'tree',side_effect=pages),patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'):
            target=ui.locate_chat(self.root('old bottom'),'临时测试')
        self.assertEqual(target['name'],'临时测试')
        self.assertTrue(all(c.args[0][-1]=='4' for c in command.call_args_list))
        self.assertFalse(any('--delay' in c.args[0] and c.args[0][c.args[0].index('--delay')+1]=='1'
                             for c in command.call_args_list))

    def test_recycled_row_cannot_click_search_box_and_partial_row_is_clipped(self):
        root=self.root('临时测试');row=root['children'][0]['children'][0]
        row['bounds'].update(y=40,height=68)
        self.assertIsNone(ui.visible_chat(root,'临时测试')[1])
        row['bounds'].update(y=80,height=45)
        listing,target=ui.visible_chat(root,'临时测试')
        self.assertEqual(target['bounds']['y'],100)
        self.assertEqual(target['bounds']['height'],25)
        self.assertEqual(row['bounds']['y'],80)

    def test_absent_target_stops_at_observed_boundaries_without_typing_or_clicking_a_row(self):
        root=self.root('another chat')
        with patch.object(ui,'tree',return_value=root),patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'尚未发送'):ui.locate_chat(root,'临时测试')
        self.assertEqual([c.args[0][-1] for c in command.call_args_list],['4','4','5','5'])
        self.assertTrue(all(c.args[0][0]=='xdotool' for c in command.call_args_list))

    def test_pre_send_failure_is_saved_and_submitted_boundary_preserved(self):
        root=Path(__file__).resolve().parents[1]/'test-runs';root.mkdir(exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix='select-error-',dir=root));nonce='a'*32;path=temp/(nonce+'.json')
        for state in ('','sending'):
            path.write_text(json.dumps({'chat':'test','state':state}))
            with patch.object(ui,'select_chat',side_effect=ValueError('target is absent')),\
                 patch.object(ui,'Path',return_value=temp),patch.object(sys,'argv',['selector','test::whalejob::'+nonce]):
                with self.assertRaises(ValueError):ui.main()
            saved=json.loads(path.read_text())
            self.assertEqual(saved['state'],state or 'failed_before_send')
            self.assertEqual(saved['error'],'target is absent')


class MomentsImageDialogTests(unittest.TestCase):
    def test_hidden_or_destroyed_file_dialog_does_not_mask_live_picker(self):
        live = self.dialog(20, True)
        old = {**self.dialog(20), 'states': ['DEFUNCT']}
        self.assertIs(moments_ui.image_file_dialog({'children': [old, live]}), live)

    def test_two_visible_pickers_require_a_unique_active_dialog(self):
        first, second = self.dialog(20), self.dialog(40)
        with self.assertRaisesRegex(ValueError, 'not unique'):
            moments_ui.image_file_dialog({'children': [first, second]})
        second['states'] = ['ACTIVE', 'VISIBLE']
        self.assertIs(moments_ui.image_file_dialog({'children': [first, second]}), second)

    def dialog(self, x, enabled=False):
        return {'role':'dialog','name':'Open','bounds':{'x':x,'y':20,'width':630,'height':415},'children':[
            {'role':'text','name':'File name:','states':['EDITABLE'],'bounds':{'x':x+100,'y':350,'width':430,'height':25}},
            {'role':'push-button','name':'Open','states':[] if enabled else ['DISABLED'],
             'bounds':{'x':x+540,'y':350,'width':80,'height':25}}]}

    def test_image_dialog_moves_onscreen_and_uses_named_filename_field(self):
        events=[]
        roots=[self.dialog(649),self.dialog(197),self.dialog(197,True),
               {'role':'frame','name':'Moments','children':[
                   {'role':'text','states':['EDITABLE'],'bounds':{'width':300}},
                   {'role':'push-button','name':'Cancel'}]}]
        def command(*args,**kwargs):
            events.append(args)
            return SimpleNamespace(stdout=b'1024 768\n' if 'getdisplaygeometry' in args else b'42\n')
        with patch.object(moments_ui,'tree',side_effect=roots),patch.object(moments_ui,'command',side_effect=command),\
             patch.object(moments_ui,'put_text') as put,patch.object(moments_ui,'click_node') as click:
            moments_ui.select_publish_images(['/data/first.png','/data/second.png'])
        self.assertIn(('xdotool','windowmove','--sync','42','197','176'),events)
        self.assertEqual(put.call_args.args[0]['name'],'File name:')
        self.assertEqual(put.call_args.args[1],'"/data/first.png" "/data/second.png"')
        self.assertEqual(click.call_args.args[0]['states'],[])

    def test_disabled_open_is_never_clicked(self):
        roots=iter([self.dialog(649),self.dialog(197),self.dialog(197)])
        def command(*args,**kwargs):
            return SimpleNamespace(stdout=b'1024 768\n' if 'getdisplaygeometry' in args else b'42\n')
        with patch.object(moments_ui,'tree',side_effect=lambda:next(roots,self.dialog(197))),\
             patch.object(moments_ui,'command',side_effect=command),patch.object(moments_ui,'put_text'),\
             patch.object(moments_ui.time,'sleep'),patch.object(moments_ui.time,'monotonic',side_effect=[0,0,0,3]),\
             patch.object(moments_ui,'click_node') as click:
            with self.assertRaisesRegex(ValueError,'image file selection'):
                moments_ui.select_publish_images(['/data/first.png'])
        click.assert_not_called()


if __name__=='__main__':unittest.main()

"""Regressions for observed process exhaustion and native popup interference."""
import errno
from pathlib import Path
import subprocess
import sys
import json
import tempfile
from types import SimpleNamespace
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
    def test_sticker_scroll_recovers_from_ambiguous_virtual_page_without_guessing(self):
        def page(indices):
            tiles=[]
            for position,index in enumerate(indices):
                tiles.append({'role':'label','name':'Custom Stickers','native_ref':'tile:'+str(index),
                    'bounds':{'x':position*70,'y':10,'width':60,'height':60}})
            return {'role':'frame','children':[
                {'role':'page-tab','name':'Custom Stickers'},
                {'role':'list','bounds':{'x':0,'y':0,'width':400,'height':90},
                 'children':tiles}]}
        start=page(range(5))
        ambiguous=page([0,1,2,2,4])
        pages=iter([page(range(5)),ambiguous,page(range(3,8)),page(range(6,11))])
        path=Path(tempfile.mkdtemp(prefix='sticker-scroll-'))/'job.json'
        order=[f'{i:032x}' for i in range(1,16)]
        job={'md5':order[8],'chat':'test'}
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree',side_effect=lambda:next(pages)),\
             patch.object(ui,'selected',return_value=True),\
             patch.object(ui,'run_process') as command,\
             patch.object(ui.time,'sleep'):
            ui.send_native_sticker(job,path,start,'test',True)
        self.assertEqual(job['state'],'submitted')
        self.assertEqual(job['favorite_scroll'],[[0,0,5],[0,3,5],[3,6,5]])
        clicks=[call.args[0] for call in command.call_args_list if call.args[0][0]=='/opt/tools/click']
        self.assertEqual(len(clicks),1)
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


class ChatListNavigationTests(unittest.TestCase):
    def root(self, *names):
        return {'role':'frame','children':[{'role':'list','name':'Chats',
            'bounds':{'x':50,'y':100,'width':200,'height':500},
            'children':[{'role':'list-item','name':name,'bounds':{'x':50,'y':100+i*45,'width':200,'height':45}}
                        for i,name in enumerate(names)]}]}

    def test_offscreen_target_is_found_by_scrolling_only_chat_list(self):
        with patch.object(ui,'tree',side_effect=[self.root('first','second'),self.root('third','临时测试')]),\
             patch.object(ui,'run_process') as command,patch.object(ui.time,'sleep'):
            target=ui.locate_chat(self.root('other'),'临时测试')
        self.assertEqual(target['name'],'临时测试')
        self.assertEqual(command.call_count,2)
        self.assertEqual(command.call_args_list[0].args[0][-1],'4')
        self.assertEqual(command.call_args_list[1].args[0][-1],'5')
        self.assertTrue(all('Return' not in c.args[0] for c in command.call_args_list))

    def test_visible_target_needs_no_extra_snapshot(self):
        with patch.object(ui,'tree') as snapshot,patch.object(ui,'run_process') as command:
            self.assertEqual(ui.locate_chat(self.root('临时测试'),'临时测试')['name'],'临时测试')
        snapshot.assert_not_called();command.assert_not_called()

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
    def dialog(self, x, enabled=False):
        return {'role':'dialog','name':'Open','bounds':{'x':x,'y':20,'width':630,'height':415},'children':[
            {'role':'text','name':'File name:','states':['EDITABLE'],'bounds':{'x':x+100,'y':350,'width':430,'height':25}},
            {'role':'push-button','name':'Open','states':[] if enabled else ['DISABLED'],
             'bounds':{'x':x+540,'y':350,'width':80,'height':25}}]}

    def test_image_dialog_moves_onscreen_and_uses_named_filename_field(self):
        events=[]
        roots=[self.dialog(649),self.dialog(197),self.dialog(197,True)]
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

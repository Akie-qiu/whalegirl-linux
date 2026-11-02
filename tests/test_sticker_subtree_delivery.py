"""Partial scroll reads must finish with a fresh full destination check."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'deploy'))
spec=importlib.util.spec_from_file_location('subtree_selector',ROOT/'deploy/chat_select_linux.py')
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)
PANEL=':1.42|/org/a11y/atspi/accessible/99'


class StickerSubtreeDeliveryTests(unittest.TestCase):
    def page(self,indices):
        return {'children':[{'role':'page-tab','name':'Custom Stickers'},
            {'role':'list','native_ref':PANEL,'bounds':{'x':0,'y':0,'width':600,'height':90},
             'children':[{'role':'label','name':'Custom Stickers','native_ref':'tile:'+str(index),
                'bounds':{'x':position*70,'y':10,'width':60,'height':60}}
                for position,index in enumerate(indices)]}]}

    def prepare(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='subtree-delivery-',dir=runs));path=folder/'job.json'
        order=[f'{i:032x}' for i in range(1,65)]
        (folder/'sticker-panel-anchors.cache').write_text(json.dumps({'panel_ref':PANEL,
            'order':order,'updated':time.time()-120,'anchors':{'tile:'+str(i):i for i in range(30,35)}}))
        return order,path

    def test_scroll_reads_panel_then_rechecks_full_destination(self):
        order,path=self.prepare();job={'md5':order[36],'chat':'test'};reads=[]
        full=self.page(range(33,38))
        def snapshot(sticker_panel_ref=None):
            reads.append(sticker_panel_ref)
            return full['children'][1] if sticker_panel_ref else full
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree',side_effect=snapshot),patch.object(ui.time,'sleep'),\
             patch.object(ui,'selected',side_effect=lambda root,*a:root is full),patch.object(ui,'run_process') as commands:
            ui.send_native_sticker(job,path,self.page(range(30,35)),'test',True)
        self.assertEqual(reads,[PANEL,None]);self.assertEqual(job['state'],'submitted')
        self.assertEqual(job['favorite_cache'],'anchor_scroll')
        self.assertEqual(sum(c.args[0][0]=='/opt/tools/click' for c in commands.call_args_list),1)

    def test_destination_changed_during_scroll_is_not_sent(self):
        order,path=self.prepare();job={'md5':order[36],'chat':'test'};full=self.page(range(33,38))
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree',side_effect=lambda sticker_panel_ref=None:full['children'][1] if sticker_panel_ref else full),\
             patch.object(ui.time,'sleep'),patch.object(ui,'selected',return_value=False),patch.object(ui,'run_process') as commands:
            with self.assertRaisesRegex(ValueError,'Destination changed'):
                ui.send_native_sticker(job,path,self.page(range(30,35)),'test',True)
        self.assertFalse(any(c.args[0][0]=='/opt/tools/click' for c in commands.call_args_list))

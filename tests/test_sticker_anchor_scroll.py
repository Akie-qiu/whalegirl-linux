"""Use verified native grid identities without resetting a nearby cached page."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
spec=importlib.util.spec_from_file_location('anchor_scroll_selector',ROOT/'deploy/chat_select_linux.py')
ui=importlib.util.module_from_spec(spec);spec.loader.exec_module(ui)


class StickerAnchorScrollTests(unittest.TestCase):
    def test_nearby_index_needs_one_snapshot_and_no_boundary_reset(self):
        def page(indices):
            return {'children':[{'role':'page-tab','name':'Custom Stickers'},
                {'role':'list','native_ref':'panel','bounds':{'x':0,'y':0,'width':600,'height':90},
                 'children':[{'role':'label','name':'Custom Stickers','native_ref':'tile:'+str(index),
                    'bounds':{'x':position*70,'y':10,'width':60,'height':60}}
                    for position,index in enumerate(indices)]}]}
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='anchor-scroll-',dir=runs));path=folder/'job.json'
        order=[f'{i:032x}' for i in range(1,65)]
        (folder/'sticker-panel-anchors.cache').write_text(json.dumps({'panel_ref':'panel',
            'order':order,'updated':time.time(),'anchors':{'tile:'+str(i):i for i in range(30,35)}}))
        job={'md5':order[36],'chat':'test'}
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree',return_value=page(range(33,38))) as snapshot,\
             patch.object(ui,'selected',return_value=True),patch.object(ui,'run_process') as commands,\
             patch.object(ui.time,'sleep'):
            ui.send_native_sticker(job,path,page(range(30,35)),'test',True)
        self.assertEqual(job['state'],'submitted')
        self.assertEqual(job['favorite_cache'],'anchor_scroll')
        snapshot.assert_called_once()
        wheels=[c.args[0] for c in commands.call_args_list if '--repeat' in c.args[0]]
        self.assertEqual(len(wheels),1)
        self.assertEqual(wheels[0][wheels[0].index('--repeat')+1],'2')
        self.assertEqual(sum(c.args[0][0]=='/opt/tools/click' for c in commands.call_args_list),1)

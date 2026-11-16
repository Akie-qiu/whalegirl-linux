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
    def test_nearby_index_uses_settled_snapshots_without_boundary_reset(self):
        def page(first):
            # Two exposed rows of a five-column grid; only the lower row is in view.
            return {'children':[{'role':'page-tab','name':'Custom Stickers'},
                {'role':'list','native_ref':'panel','bounds':{'x':0,'y':0,'width':600,'height':90},
                 'children':[{'role':'label','name':'Custom Stickers','native_ref':'tile:'+str(index),
                    'bounds':{'x':index%5*70,'y':-60+(index-first)//5*70,'width':60,'height':60}}
                    for index in range(first,first+10)]}]}
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='anchor-scroll-',dir=runs));path=folder/'job.json'
        order=[f'{i:032x}' for i in range(1,65)]
        (folder/'sticker-panel-anchors.cache').write_text(json.dumps({'schema':2,'session':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
            'page':[list(row) for row in ui.favorite_page_signature(page(25)['children'][1]['children'])],
            'panel_ref':'panel',
            'order':order,'updated':time.time(),'anchors':{'tile:'+str(i):i for i in range(30,35)},
            'grid':{'xs':[0,70,140,210,280],'shift':0}}))
        job={'md5':order[36],'chat':'test','fast_session_nonce':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}
        (path.parent/('a'*32+'.guard.json')).write_text(json.dumps({'chat':'test','allowed':True,'updated':time.time()}))
        with patch.object(ui,'native_read',return_value={'stickers':[{'md5':v} for v in order]}),\
             patch.object(ui,'tree',return_value=page(30)) as snapshot,\
             patch.object(ui,'selected',return_value=True),patch.object(ui,'run_process') as commands,\
             patch.object(ui.time,'sleep'):
            ui.send_native_sticker(job,path,page(25),'test',True)
        self.assertEqual(job['state'],'submitted')
        self.assertEqual(job['favorite_cache'],'anchor_scroll')
        self.assertEqual(snapshot.call_count,2,'a scrolled panel needs two matching snapshots before selection')
        wheels=[c.args[0] for c in commands.call_args_list if '--repeat' in c.args[0]]
        self.assertEqual(len(wheels),1)
        self.assertEqual(wheels[0][wheels[0].index('--repeat')+1],'2')
        self.assertEqual(sum(c.args[0][0]=='/opt/tools/click' for c in commands.call_args_list),1)

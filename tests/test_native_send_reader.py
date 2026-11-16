"""Narrow reads must refresh values and verify object ancestry, never coordinates alone."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
import native_send_reader as reader

ROOT=Path(__file__).resolve().parents[1]


class ReaderTests(unittest.TestCase):
    def setUp(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='send-reader-',dir=runs));(self.folder/'read.lock').write_text('')
        def node(key,role,name,bounds,states=(),children=()):
            return {'role':role,'name':name,'bounds':bounds,'states':list(states),
                    'native_ref':':1.2|/org/a11y/atspi/accessible/'+key,'children':list(children)}
        self.row=node('row','list-item','临时测试 新消息',{'x':0,'y':100,'width':180,'height':50},['SELECTED'])
        self.chats=node('chats','list','Chats',{'x':0,'y':70,'width':180,'height':500},children=[self.row])
        self.title=node('title','label','临时测试',{'x':200,'y':30,'width':300,'height':30})
        self.editor=node('editor','text','',{'x':200,'y':500,'width':600,'height':150},['EDITABLE','FOCUSED'])
        self.frame=node('frame','frame','Weixin',{'x':0,'y':0,'width':800,'height':700},children=[self.chats,self.title,self.editor])
        self.root=node('root','desktop-frame','main',{'x':0,'y':0,'width':800,'height':700},children=[self.frame])
        self.nodes={};self.members={}
        def walk(n):
            ref=tuple(n['native_ref'].split('|'));self.nodes[ref]=copy.deepcopy(n)
            self.members[ref]=[tuple(c['native_ref'].split('|')) for c in n['children']]
            for child in n['children']:walk(child)
        walk(self.root)
        self.bus=SimpleNamespace(connection=Mock(),role=lambda r:self.nodes[r]['role'],
            properties=lambda r:{'Name':self.nodes[r]['name']},bounds=lambda r:self.nodes[r]['bounds'],
            states=lambda r,role:self.nodes[r]['states'],children=Mock(side_effect=lambda r:self.members[r]))
        self.safe=SimpleNamespace(A11yBus=Mock(return_value=self.bus),
            parse_sticker_panel_ref=lambda v:tuple(v.split('|')) if isinstance(v,str) and '|' in v else (_ for _ in ()).throw(ValueError('missing ref')),
            acquire_read_lock=Mock(),publish_stats=Mock(),dump_tree=Mock(return_value=(copy.deepcopy(self.root),{'nodes':6})),
            fcntl=SimpleNamespace(LOCK_UN=8,flock=Mock()))
        self.directory=patch.object(reader,'STATE_DIRECTORY',self.folder);self.directory.start()
        self.importer=patch.object(reader.importlib,'import_module',return_value=self.safe);self.importer.start()
        reader._BUS=None

    def tearDown(self):
        reader._discard_bus();self.importer.stop();self.directory.stop()

    def live(self,node):return self.nodes[tuple(node['native_ref'].split('|'))]

    def test_connection_reused_but_bounds_are_read_again_on_each_call(self):
        root=reader.read();one=reader.read(root,name='临时测试',is_group=True,require_focus=True)
        self.live(self.editor)['bounds']={'x':240,'y':520,'width':520,'height':120}
        two=reader.read(one,name='临时测试',is_group=True,require_focus=True)
        self.assertEqual(self.safe.A11yBus.call_count,1)
        self.assertEqual(one['children'][0]['children'][-1]['bounds']['x'],200)
        self.assertEqual(two['children'][0]['children'][-1]['bounds']['x'],240)
        self.assertEqual(self.safe.acquire_read_lock.call_count,3)
        self.assertEqual(self.safe.fcntl.flock.call_count,3)

    def test_switched_chat_name_or_selection_cannot_reuse_old_proof(self):
        for field,value in [('name','另一群'),('states',[])]:
            with self.subTest(field=field):
                self.nodes[tuple(self.row['native_ref'].split('|'))]=copy.deepcopy(self.row)
                self.live(self.row)[field]=value
                with self.assertRaises(ValueError):reader.read(self.root,name='临时测试',is_group=True)

    def test_recycled_ref_detached_parent_and_lost_focus_are_rejected(self):
        ref=tuple(self.row['native_ref'].split('|'));parent=tuple(self.chats['native_ref'].split('|'))
        self.members[parent]=[]
        with self.assertRaises(ValueError):reader.read(self.root,name='临时测试',is_group=True)
        self.members[parent]=[ref];self.live(self.editor)['role']='label'
        with self.assertRaises(ValueError):reader.read(self.root,name='临时测试',is_group=True)
        self.live(self.editor)['role']='text';self.live(self.editor)['states']=['EDITABLE']
        with self.assertRaises(ValueError):reader.read(self.root,name='临时测试',is_group=True,require_focus=True)
        reader.read(self.root,name='临时测试',is_group=True)

    def test_wrong_requested_target_rejected_and_error_unlocks_bus(self):
        with self.assertRaises(ValueError):reader.read(self.root,name='另一群',is_group=True)
        self.assertIsNone(reader._BUS);self.bus.connection.close_sync.assert_called_once()
        self.safe.fcntl.flock.assert_called_once()

    def test_duplicate_native_title_labels_do_not_disable_valid_proof(self):
        root=copy.deepcopy(self.root)
        duplicate=copy.deepcopy(self.title);duplicate['native_ref']=':1.2|/org/a11y/atspi/accessible/title2'
        root['children'][0]['children'].append(duplicate)
        ref=tuple(duplicate['native_ref'].split('|'));self.nodes[ref]=duplicate
        self.members[tuple(self.frame['native_ref'].split('|'))].append(ref)
        value=reader.read(root,name='临时测试',is_group=True,require_focus=True)
        self.assertEqual(value['children'][0]['children'][0]['children'][0]['states'],['SELECTED'])

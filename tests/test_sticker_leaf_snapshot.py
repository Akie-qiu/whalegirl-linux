"""Compact sending snapshots avoid unused calls for native sticker leaves."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


spec=importlib.util.spec_from_file_location(
    'sticker_leaf_a11y',Path(__file__).resolve().parents[1]/'deploy/a11y_safe.py')
safe=importlib.util.module_from_spec(spec)
if importlib.util.find_spec('fcntl') is None:
    # dump_tree itself is platform-independent; native locking stays Linux-only.
    with patch.dict(sys.modules,{'fcntl':types.ModuleType('fcntl')}):
        spec.loader.exec_module(safe)
else:
    spec.loader.exec_module(safe)


class Bus:
    root=safe.DESKTOP
    panel=('test','/panel');tile=('test','/tile')
    header=('test','/header');row=('test','/row');hint=('test','/hint')

    def __init__(self):self.calls=[]
    def role(self,ref):
        return {self.root:'desktop-frame',self.header:'push-button',self.row:'list-item',
                self.panel:'list',self.tile:'label',self.hint:'label'}[ref]
    def properties(self,ref):
        return {'Name':{self.root:'main',self.header:'Custom Stickers',self.row:'destination',
                       self.panel:'sticker panel',self.tile:'Custom Stickers',self.hint:'hint'}[ref]}
    def bounds(self,ref):return {'x':10,'y':20,'width':64,'height':64}
    def states(self,ref,role):
        self.calls.append(('states',ref))
        return ['SELECTED'] if ref in (self.row,self.tile) else []
    def children(self,ref):
        self.calls.append(('children',ref))
        return {self.root:[self.header,self.row,self.panel],self.panel:[self.tile],
                self.tile:[self.hint]}.get(ref,[])


class StickerLeafSnapshotTests(unittest.TestCase):
    def test_compact_preserves_tile_identity_bounds_and_reads_panel(self):
        bus=Bus();tree,stats=safe.dump_tree(bus,skip_messages=True)
        tile=tree['children'][2]['children'][0]
        self.assertEqual(tile,{'role':'label','name':'Custom Stickers','native_ref':'test|/tile',
                               'bounds':{'x':10,'y':20,'width':64,'height':64}})
        self.assertNotIn(('states',bus.tile),bus.calls)
        self.assertNotIn(('children',bus.tile),bus.calls)
        for ref in (bus.header,bus.row,bus.panel):
            self.assertIn(('states',ref),bus.calls)
            self.assertIn(('children',ref),bus.calls)
        self.assertEqual(tree['children'][1]['states'],['SELECTED'])
        self.assertEqual(tree['children'][2]['native_ref'],'test|/panel')
        self.assertEqual(stats['nodes'],5)

    def test_full_snapshot_keeps_original_tile_states_and_children(self):
        bus=Bus();tree,stats=safe.dump_tree(bus)
        tile=tree['children'][2]['children'][0]
        self.assertIn(('states',bus.tile),bus.calls)
        self.assertIn(('children',bus.tile),bus.calls)
        self.assertEqual(tile['states'],['SELECTED'])
        self.assertEqual(tile['children'][0]['name'],'hint')
        self.assertEqual(tile['native_ref'],'test|/tile')
        self.assertEqual(stats['nodes'],6)


if __name__=='__main__':unittest.main()

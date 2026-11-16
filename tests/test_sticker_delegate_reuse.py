"""Qt recycles objects across pages; historical anchors must not accumulate."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('delegate_base',Path(__file__).with_name('test_sticker_grid_safety.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)

class RecyclingGrid(base.FakeGrid):
    def tiles(self):
        nodes=super().tiles()
        for node in nodes:
            item=int(node['native_ref'].split(':')[-1])
            # A small pool backs the whole list, like the captured live Qt grid.
            node['native_ref']='delegate:'+str(item%40)
        return nodes

class DelegateReuseTests(unittest.TestCase):
    def test_every_sticker_with_recycled_delegates(self):
        failures=[]
        for target in range(95):
            job,clicks,error=base.send(RecyclingGrid(total=95),target,base.workspace())
            if error or clicks!=[target]:failures.append((target,error,clicks))
        self.assertEqual(failures,[])

    def test_addition_reordering_and_removal_invalidate_prior_positions(self):
        folder=base.workspace()
        order=['%032x'%i for i in range(1,96)]
        added=['%032x'%i for i in range(96,102)]+order
        removed=added[:31]+added[35:]
        for current in (order,list(reversed(order)),added,removed,removed[20:]+removed[:20]):
            total=len(current)
            for target in (0,2,total//3,total//2,total-1):
                job,clicks,error=base.send(RecyclingGrid(total=total),target,folder,order=current)
                self.assertIsNone(error,(total,target,error));self.assertEqual(clicks,[target])

    def test_inventory_changed_before_the_click_refuses_without_sending(self):
        order=['%032x'%i for i in range(1,96)];grid=RecyclingGrid(total=len(order))
        job={'md5':order[39],'chat':'test'};folder=base.workspace();calls=0
        def native(route,**options):
            nonlocal calls
            if route=='/stickers':
                calls+=1;values=order if calls==1 else list(reversed(order))
                return {'stickers':[{'md5':m} for m in values]}
            return {'messages':[]}
        with patch.object(base.selector,'native_read',side_effect=native),patch.object(base.selector,'tree',side_effect=grid.tree),patch.object(base.selector,'selected',return_value=True),patch.object(base.selector,'run_process',side_effect=grid.command),patch.object(base.selector.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'Favorite sync changed'):
                base.selector.send_native_sticker(job,folder/'job.json',grid.root(),'test',True)
        self.assertEqual(grid.clicked,[])

    def test_legacy_cache_cannot_prove_a_new_popup(self):
        grid=RecyclingGrid(total=95);folder=base.workspace()
        # Its refs and columns exist in the new popup but refer to other rows.
        cache={'panel_ref':base.PANEL,'updated':__import__('time').time(),
               'anchors':{'delegate:'+str(i):i+40 for i in range(30)},
               'grid':{'xs':[0,70,140,210,280],'shift':0}}
        job,clicks,error=base.send(grid,39,folder,cache=cache)
        self.assertIsNone(error);self.assertEqual(clicks,[39])
        self.assertGreater(job.get('favorite_scroll',[]).__len__(),0)

    def test_cold_sticker_waits_for_a_new_local_row_without_reclicking(self):
        rows=[{'messages':[{'localId':8,'isSelf':True,'type':47,'sticker':{'md5':'a'*32}}]},
              {'messages':[{'localId':9,'isSelf':True,'type':47,'sticker':{'md5':'b'*32}}]},
              {'messages':[{'localId':10,'isSelf':True,'type':47,'sticker':{'md5':'a'*32}}]}]
        with patch.object(base.selector,'native_read',side_effect=rows),patch.object(base.selector.time,'sleep'),patch.object(base.selector,'run_process') as command:
            self.assertEqual(base.selector.await_sticker_local('test','a'*32,8),10)
        command.assert_not_called()

    def test_missing_local_row_remains_unconfirmed_without_retrying_click(self):
        with patch.object(base.selector,'native_read',return_value={'messages':[]}) as read,patch.object(base.selector.time,'sleep'),patch.object(base.selector,'run_process') as command:
            self.assertEqual(base.selector.await_sticker_local('test','a'*32,8),0)
        self.assertEqual(read.call_count,5);command.assert_not_called()

if __name__=='__main__':unittest.main()

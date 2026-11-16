"""Composer cleanup, restart ownership and UI backoff; no real publishing."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'deploy'))
import moments_ui as ui
from moments_session import ComposerSession,digest
from whalegirl_linux.gateway import UIJobError
from whalegirl_linux.moments_health import MomentsUiHealth
from tests import test_moments as fixtures


def node(role,name='',**extras):return {'role':role,'name':name,**extras}


def editor_view():
    return node('frame','Moments',window={'pid':123},children=[
        node('text',states=['EDITABLE'],bounds={'x':100,'y':100,'width':300,'height':60}),
        node('push-button','Cancel',bounds={'x':300,'y':200,'width':60,'height':30}),
        node('push-button','Post',bounds={'x':100,'y':200,'width':60,'height':30}),
        node('push-button','Close')])


def timeline():return node('frame','Moments',window={'pid':123},children=[node('push-button','Close'),node('push-button','Messages')])


class ComposerCleanupTests(unittest.TestCase):
    def test_cancel_then_new_discard_confirmation_then_close_without_escape_or_post(self):
        phase=[0];draft=editor_view();discard=node('push-button',"Don't Save",bounds={'x':200,'y':230})
        roots=[draft,{'children':[draft,discard]},timeline(),{'children':[]}]
        clicks=[]
        def click(value):
            clicks.append(value['name']);phase[0]+=1
        with patch.object(ui,'tree',side_effect=lambda:roots[min(phase[0],3)]),\
             patch.object(ui,'click_node',side_effect=click),patch.object(ui,'command') as command,\
             patch.object(ui.time,'sleep'):
            ui.close_moments(owned_editor=True)
        self.assertEqual(clicks,['Cancel',"Don't Save",'Close'])
        command.assert_not_called()

    def test_foreign_editor_is_preserved_without_any_click(self):
        with patch.object(ui,'tree',return_value=editor_view()),patch.object(ui,'click_node') as click:
            with self.assertRaisesRegex(ValueError,'unowned'):ui.close_moments()
        click.assert_not_called()

    def test_two_discard_buttons_do_not_get_a_guessed_click(self):
        root=editor_view();phase=[0]
        after={'children':[root,node('push-button','Discard',bounds={'x':1}),node('push-button','Discard',bounds={'x':2})]}
        clicks=[]
        with patch.object(ui,'tree',side_effect=lambda:after if phase[0] else root),\
             patch.object(ui,'click_node',side_effect=lambda n:(clicks.append(n['name']),phase.__setitem__(0,1))):
            with self.assertRaisesRegex(ValueError,'ambiguous'):ui.close_moments(owned_editor=True)
        self.assertEqual(clicks,['Cancel'])

    def test_file_picker_is_cancelled_before_the_timeline_is_closed(self):
        picker=node('dialog','Select File',children=[node('push-button','Cancel')]);phase=[0]
        roots=[{'children':[timeline(),picker]},timeline(),{'children':[]}];clicks=[]
        with patch.object(ui,'tree',side_effect=lambda:roots[min(phase[0],2)]),\
             patch.object(ui,'click_node',side_effect=lambda n:(clicks.append(n['name']),phase.__setitem__(0,phase[0]+1))),\
             patch.object(ui.time,'sleep'):
            ui.close_moments(owned_editor=True)
        self.assertEqual(clicks,['Cancel','Close'])

    def test_missing_close_control_is_a_real_failure(self):
        with patch.object(ui,'tree',return_value=node('frame','Moments')),patch.object(ui,'command'):
            with self.assertRaises(ValueError):ui.close_moments()


class ComposerOwnershipTests(unittest.TestCase):
    def setUp(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='moments-session-',dir=runs))
        self.frame=editor_view()
        self.interface=SimpleNamespace(ROOT=self.folder,tree=lambda:self.frame,walk=ui.walk,
            frame=lambda:self.frame,editors=ui.editors,composer=lambda r:r,
            command=lambda *args:SimpleNamespace(stdout=b'77\n'),close_moments=lambda **kwargs:None)
        self.session=ComposerSession(self.interface,'account',self.folder/'job.json')
        self.expected={'ref':':1.2|/org/a11y/atspi/accessible/5','text_hash':digest('合成文案'),'chars':4}

    def seed(self,**changes):
        self.session.value={'account':'account','job':'old.json','created':time.time()-30,'updated':time.time()-10,
            'creator_pid':99999999,'window':'77','wechat_pid':123,'stage':'editing','editor':self.expected,**changes}
        self.session.write()

    def test_checkpoint_saves_hash_and_identity_without_plaintext(self):
        self.session.owns=True
        with patch('moments_session.editor_snapshot',return_value=self.expected):self.session.checkpoint('合成文案')
        text=self.session.path.read_text()
        self.assertNotIn('合成文案',text)
        self.assertEqual(json.loads(text)['editor'],self.expected)

    def test_wrong_editor_text_is_never_adopted_as_ours(self):
        self.session.owns=True
        with patch('moments_session.editor_snapshot',return_value=self.expected):
            with self.assertRaisesRegex(ValueError,'did not match'):self.session.checkpoint('另一段内容')
        self.assertFalse(self.session.path.exists())

    def test_dead_process_matching_editor_is_recovered_once(self):
        self.seed();calls=[];self.interface.close_moments=lambda **kw:calls.append(kw)
        with patch('moments_session.os.kill',side_effect=ProcessLookupError),\
             patch('moments_session.editor_snapshot',return_value=self.expected):
            self.assertEqual(self.session.recover()['state'],'recovered')
            self.assertIsNone(self.session.recover())
        self.assertEqual(calls,[{'owned_editor':True}])

    def test_changed_editor_or_window_or_account_is_not_cancelled(self):
        for changes in ({'wechat_pid':456},{'window':'88'},{'account':'someone-else'},
                        {'editor':{**self.expected,'text_hash':digest('人工修改')}}):
            self.seed(**changes);calls=[];self.interface.close_moments=lambda **kw:calls.append(kw)
            with patch('moments_session.os.kill',side_effect=ProcessLookupError),\
                 patch('moments_session.editor_snapshot',return_value=self.expected):
                self.assertIsNone(self.session.recover())
            self.assertEqual(calls,[])

    def test_submitting_and_live_native_job_remain_blocked(self):
        self.seed(stage='submitting')
        self.assertEqual(self.session.recover()['code'],'outcome_uncertain')
        self.seed()
        with patch('moments_session.os.kill',return_value=None):
            self.assertEqual(self.session.recover()['code'],'editor_job_active')

    def test_human_edit_after_our_checkpoint_is_preserved_during_cleanup(self):
        self.seed(expected_hash=self.expected['text_hash']);self.session.owns=True
        changed={**self.expected,'text_hash':digest('人工修改后的草稿')}
        with patch('moments_session.editor_snapshot',return_value=changed):
            self.assertFalse(self.session.can_close())

    def test_already_closed_surface_does_not_require_editor_cancellation(self):
        self.session.owns=True
        self.interface.frame=lambda:(_ for _ in ()).throw(ValueError('not visible'))
        self.assertFalse(self.session.can_close())


class NativeTransactionTests(unittest.TestCase):
    def setUp(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='moments-transaction-',dir=runs))
        self.path=self.folder/'job.json'

    def test_foreign_composer_returns_cache_without_opening_or_cancelling(self):
        self.path.write_text(json.dumps({'operation':'moments_read','created':time.time()}))
        with patch.object(ui,'ROOT',self.folder),patch.object(ui,'authorize',return_value=({'posts':[]},{})),\
             patch.object(ui,'tree',return_value=editor_view()),patch.object(ui,'open_moments') as open_ui,\
             patch.object(ui,'close_moments') as close_ui:
            ui.run(self.path,'account')
        result=json.loads(self.path.read_text())
        self.assertEqual(result['ui_surface']['code'],'unowned_editor')
        self.assertFalse(result['refreshed']);open_ui.assert_not_called();close_ui.assert_not_called()

    def test_confirmed_publish_does_not_become_failed_when_cleanup_fails(self):
        self.path.write_text(json.dumps({'operation':'moments_send','kind':'publish','created':time.time(),
                                        'text':'合成动态','source':{}}))
        draft=editor_view();session=SimpleNamespace(owns=True,recover=lambda:None,checkpoint=lambda text:None,
                                                   expect=lambda text:None,can_close=lambda:True,
                                                   submitting=lambda:None,closed=lambda:None)
        clicks=[]
        with patch.object(ui,'ROOT',self.folder),patch.object(ui,'authorize',return_value=({'posts':[]},{})),\
             patch.object(ui,'tree',return_value=timeline()),patch.object(ui,'open_moments'),\
             patch.object(ui,'prepare_publish',return_value=draft),patch.object(ui,'composer',return_value=draft),\
             patch.object(ui,'put_text'),patch.object(ui,'click_node',side_effect=lambda n:clicks.append(n['name'])),\
             patch.object(ui,'native_read',return_value={'posts':[{'id':'new','own':True,'text':'合成动态'}]}),\
             patch.object(ui,'close_moments',side_effect=ValueError('cleanup')),\
             patch('moments_session.ComposerSession',return_value=session):
            ui.run(self.path,'account')
        result=json.loads(self.path.read_text())
        self.assertEqual(result['state'],'submitted');self.assertTrue(result['confirmed'])
        self.assertEqual(result['ui_cleanup']['state'],'blocked');self.assertEqual(clicks,['Post'])


class UiHealthTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.MomentsTests.setUp
    tearDown=fixtures.MomentsTests.tearDown
    draft=fixtures.MomentsTests.draft

    async def test_block_is_persistent_and_backoff_is_bounded(self):
        value=self.service.ui_health.record('account','unowned_editor')
        self.assertAlmostEqual(value['retry_at']-value['checked_at'],30)
        other=MomentsUiHealth(self.store)
        self.assertEqual(other.status('account'),value)
        for _ in range(10):value=other.record('account','unowned_editor')
        self.assertLessEqual(value['retry_at']-value['checked_at'],300)

    async def test_blocked_tick_only_reads_cache_without_generation_or_ui_request(self):
        self.service.ui_health.record('account','unowned_editor')
        cache=[]
        async def read(route):cache.append(route);return {'account':'account','posts':[],'friends':[]}
        self.runtime.gateway.native_request=read
        await self.service.tick()
        self.assertEqual(cache,['/moments']);self.assertEqual(self.calls,[])
        self.assertIn('已保留',self.service.error)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM moments_actions').fetchone()[0],0)

    async def test_blocked_scan_does_not_claim_a_timeline_refresh_or_consume_poll(self):
        async def read(*args):return {'posts':[],'friends':[],'refreshed':False,
                                     'ui_surface':{'state':'blocked','code':'unowned_editor'}}
        self.runtime.gateway.moments=read
        await self.service.scan()
        self.assertEqual(self.service.last_scan,0)
        self.assertEqual(self.service.ui_health.status('account')['state'],'blocked')
        self.assertGreater(self.service.last_cache_scan,0)

    async def test_healthy_manual_scan_clears_block_before_retry_deadline(self):
        self.service.ui_health.record('account','unowned_editor')
        async def read(*args):return {'posts':[],'friends':[],'ui_surface':{'state':'ready'}}
        self.runtime.gateway.moments=read
        await self.service.scan()
        self.assertEqual(self.service.ui_health.status('account')['state'],'ready')
        self.assertEqual(self.service.error,'')

    async def test_sent_post_with_cleanup_warning_stays_sent_and_is_not_replayed(self):
        async def send(*args,**kwargs):return {'confirmed':True,'receipt':'post',
                                              'ui_cleanup':{'state':'blocked','code':'cleanup_failed'}}
        self.runtime.gateway.moments=send
        identity=self.draft();row=await self.service.send(identity)
        self.assertEqual(row['state'],'sent');self.assertEqual(row['receipt'],'post')
        self.assertEqual(self.service.ui_health.status('account')['state'],'blocked')
        with self.assertRaises(ValueError):await self.service.send(identity)

    async def test_model_rate_limit_is_not_misclassified_as_a_ui_failure(self):
        self.assertEqual(MomentsUiHealth.error_code(UIJobError('HTTP 429')),'')


if __name__=='__main__':unittest.main()

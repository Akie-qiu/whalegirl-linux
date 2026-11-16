"""Offline checks for selective release rollback; never executes systemctl/SSH."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('personality_rollback',ROOT/'deploy'/'rollback_personality_life.py')
rollback=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(rollback)


class PersonalityRollbackTests(unittest.TestCase):
    def test_only_plugin_settings_are_removed_and_other_bilibili_flags_survive(self):
        original={'life_autonomy_enabled':True,'growth_mature_traits_chat_enabled':False,
            'bilibili_comment_mentions_enabled':True,'bilibili_wechat_video_enabled':True,
            'model':'synthetic-model','growth_exploration':55}
        after,keys=rollback.reverted_settings(original,{})
        self.assertEqual(keys,['growth_mature_traits_chat_enabled','life_autonomy_enabled'])
        self.assertNotIn('life_autonomy_enabled',after)
        self.assertTrue(after['bilibili_comment_mentions_enabled'])
        self.assertTrue(after['bilibili_wechat_video_enabled'])
        self.assertEqual(after['model'],'synthetic-model')
        self.assertEqual(after['growth_exploration'],55)
        self.assertIn('life_autonomy_enabled',original)

    def test_recorded_life_parameter_difference_can_be_reverted_without_touching_others(self):
        original={'growth_exploration':55,'bilibili_interval_minutes':90,'other_setting':'preserved'}
        delta={'growth_exploration':{'before_present':True,'before':35,'after_present':True,'after':55}}
        after,keys=rollback.reverted_settings(original,delta)
        self.assertEqual(after,{'growth_exploration':35,'bilibili_interval_minutes':90,'other_setting':'preserved'})
        self.assertEqual(keys,['growth_exploration'])

    def test_changed_after_deployment_refuses_overwrite(self):
        delta={'growth_exploration':{'before_present':True,'before':35,'after_present':True,'after':55}}
        with self.assertRaisesRegex(ValueError,'refusing to overwrite'):
            rollback.reverted_settings({'growth_exploration':60},delta)

    def test_unapproved_setting_cannot_be_restored_from_a_report(self):
        delta={'api_url':{'before_present':True,'before':'old','after_present':True,'after':'new'}}
        with self.assertRaisesRegex(ValueError,'unapproved setting'):
            rollback.reverted_settings({'api_url':'new'},delta)

    def test_report_targets_must_be_exact_independent_release_directories(self):
        parent=ROOT/'test-runs';parent.mkdir(exist_ok=True)
        root=Path(tempfile.mkdtemp(prefix='rollback-targets-',dir=parent))
        releases=root/'releases';releases.mkdir()
        for name in ('candidate','previous'):
            module=releases/name/'whalegirl_linux';module.mkdir(parents=True)
            (module/'runtime.py').write_text('# synthetic source\n',encoding='utf-8')
        report={'kind':'personality-life','current':str(releases/'candidate'),'previous':str(releases/'previous')}
        self.assertEqual(rollback.checked_targets(report,root),(releases/'candidate',releases/'previous'))
        outside=root/'outside'/'whalegirl_linux';outside.mkdir(parents=True)
        (outside/'runtime.py').write_text('# synthetic outside\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'direct child'):
            rollback.checked_targets({**report,'previous':str(outside.parent)},root)
        with self.assertRaisesRegex(ValueError,'kind'):
            rollback.checked_targets({**report,'kind':'other-deployment'},root)


if __name__=='__main__':unittest.main()

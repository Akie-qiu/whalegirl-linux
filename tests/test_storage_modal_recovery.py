"""Dismiss only the observed storage notice after storage has recovered."""
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock

path=Path(__file__).resolve().parents[1]/'deploy/native_recovery.py'
spec=importlib.util.spec_from_file_location('storage_modal_recovery',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class StorageModalTests(unittest.TestCase):
    def setUp(self):
        self.modal={'window':'42','X':499,'Y':245,'WIDTH':282,'HEIGHT':310}
        self.pixels=b'\0'*(282*310*3)
        self.ui=SimpleNamespace(run_process=Mock(return_value=SimpleNamespace(stdout=self.pixels)))

    def test_recognized_notice_on_healthy_disk_clicks_only_dismiss_button(self):
        with patch.object(module,'active_modal',side_effect=[self.modal,None]), \
             patch.object(module.shutil,'disk_usage',return_value=SimpleNamespace(free=2*1024**3)), \
             patch.object(module,'STORAGE_WARNING_RGB',hashlib.sha256(self.pixels).hexdigest()), \
             patch.object(module.time,'sleep'):
            self.assertTrue(module.resolve_modal(self.ui))
        calls=[c.args[0] for c in self.ui.run_process.call_args_list]
        self.assertEqual(calls[-1],['/opt/tools/click','640','506'])
        self.assertEqual(len(calls),2)

    def test_low_disk_does_not_hide_the_warning(self):
        with patch.object(module,'active_modal',return_value=self.modal), \
             patch.object(module.shutil,'disk_usage',return_value=SimpleNamespace(free=100*1024**2)):
            with self.assertRaisesRegex(ValueError,'磁盘空间仍不足'):module.resolve_modal(self.ui)
        self.ui.run_process.assert_not_called()

    def test_unknown_notice_is_not_clicked(self):
        with patch.object(module,'active_modal',return_value=self.modal), \
             patch.object(module.shutil,'disk_usage',return_value=SimpleNamespace(free=2*1024**3)):
            with self.assertRaisesRegex(ValueError,'未识别'):module.resolve_modal(self.ui)
        self.assertEqual(self.ui.run_process.call_count,1)

    def test_unknown_modal_size_is_not_clicked(self):
        with patch.object(module,'active_modal',return_value={**self.modal,'WIDTH':400}):
            with self.assertRaisesRegex(ValueError,'弹窗遮挡'):module.resolve_modal(self.ui)
        self.ui.run_process.assert_not_called()

    def test_no_modal_avoids_capture_and_clicks(self):
        with patch.object(module,'active_modal',return_value=None):self.assertFalse(module.resolve_modal(self.ui))
        self.ui.run_process.assert_not_called()

    def test_still_blocked_after_click_is_not_reported_as_recovered(self):
        with patch.object(module,'active_modal',side_effect=[self.modal,self.modal]), \
             patch.object(module.shutil,'disk_usage',return_value=SimpleNamespace(free=2*1024**3)), \
             patch.object(module,'STORAGE_WARNING_RGB',hashlib.sha256(self.pixels).hexdigest()), \
             patch.object(module.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'尚未关闭'):module.resolve_modal(self.ui)


if __name__=='__main__':unittest.main()

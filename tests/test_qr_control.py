"""Regression checks for the native QR refresh helper."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "qr_control", Path(__file__).resolve().parents[1] / "deploy" / "qr_control.py")
qr_control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qr_control)


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.value).encode()


class QrRefreshTests(unittest.TestCase):
    def refresh_with(self, qr):
        tree = {"role": "frame", "children": [
            {"role": "label", "name": "Scan to log in"},
            {"role": "push-button", "name": "QR Code",
             "bounds": {"x": 565, "y": 257, "width": 150, "height": 150}},
        ]}

        class Opener:
            def open(self, request, **kwargs):
                return Response({"tree": tree} if request.full_url.endswith("debug/a11y")
                                else {"qrDataUrl": qr})

        with patch.object(qr_control.Path, "read_text", return_value="test-token"), \
             patch.object(qr_control.urllib.request, "build_opener", return_value=Opener()), \
             patch.object(qr_control.subprocess, "run") as click, \
             patch.object(qr_control.time, "sleep"):
            try:
                return qr_control.refresh()
            finally:
                self.assertEqual(click.call_count, 1)

    def test_unchanged_decoded_qr_remains_available(self):
        result = self.refresh_with("data:image/png;base64,current-native-qr")
        self.assertTrue(result["ok"])
        self.assertFalse(result["refreshed"])
        self.assertEqual(result["qrDataUrl"], "data:image/png;base64,current-native-qr")

    def test_missing_qr_is_not_reported_as_ready(self):
        with self.assertRaisesRegex(RuntimeError, "未读取到客户端二维码"):
            self.refresh_with("")

    def test_signout_popup_is_dismissed_then_enter_weixin_triggers_phone_confirm(self):
        initial_tree = {"role": "desktop-frame", "children": [
            {"role": "frame", "name": "Weixin", "children": [
                {"role": "label", "name": "Current User沃不明白"},
                {"role": "push-button", "name": "Enter Weixin",
                 "bounds": {"x": 549, "y": 469, "width": 180, "height": 36}},
            ]},
            {"role": "filler", "children": [
                {"role": "label", "name": "Tip"},
                {"role": "label", "name": "You have signed out of Weixin"},
                {"role": "push-button", "name": "OK",
                 "bounds": {"x": 593, "y": 408, "width": 92, "height": 30}},
            ]},
        ]}
        dismissed_tree = {"role": "desktop-frame", "children": [
            {"role": "frame", "name": "Weixin", "children": [
                {"role": "label", "name": "Current User沃不明白"},
                {"role": "push-button", "name": "Enter Weixin",
                 "bounds": {"x": 549, "y": 469, "width": 180, "height": 36}},
            ]},
        ]}
        confirm_tree = {"role": "desktop-frame", "children": [
            {"role": "frame", "name": "Weixin", "children": [
                {"role": "label", "name": "Please confirm login on your phone"},
            ]},
        ]}
        trees = [initial_tree, dismissed_tree, confirm_tree]

        class Opener:
            def open(self, request, **kwargs):
                return Response({"tree": trees.pop(0) if len(trees) > 1 else trees[0]})

        with patch.object(qr_control.Path, "read_text", return_value="test-token"), \
             patch.object(qr_control.urllib.request, "build_opener", return_value=Opener()), \
             patch.object(qr_control.subprocess, "run") as click, \
             patch.object(qr_control.time, "sleep"):
            result = qr_control.refresh()
            self.assertTrue(result["ok"])
            self.assertEqual(result["mode"], "phone_confirmation")
            self.assertEqual(click.call_count, 2)

    def test_enter_weixin_transition_to_qr_page_returns_decoded_qr(self):
        account_tree = {"role": "desktop-frame", "children": [
            {"role": "frame", "name": "Weixin", "children": [
                {"role": "label", "name": "Current User沃不明白"},
                {"role": "push-button", "name": "Enter Weixin",
                 "bounds": {"x": 549, "y": 469, "width": 180, "height": 36}},
            ]},
        ]}
        qr_tree = {"role": "desktop-frame", "children": [
            {"role": "frame", "name": "Weixin", "children": [
                {"role": "label", "name": "Scan to log in"},
                {"role": "push-button", "name": "QR Code",
                 "bounds": {"x": 564, "y": 256, "width": 150, "height": 150}},
            ]},
        ]}
        trees = [account_tree, qr_tree]

        class Opener:
            def open(self, request, **kwargs):
                if request.full_url.endswith("debug/a11y"):
                    return Response({"tree": trees.pop(0) if len(trees) > 1 else trees[0]})
                return Response({"qrDataUrl": "data:image/png;base64,switched-to-qr"})

        with patch.object(qr_control.Path, "read_text", return_value="test-token"), \
             patch.object(qr_control.urllib.request, "build_opener", return_value=Opener()), \
             patch.object(qr_control.subprocess, "run") as click, \
             patch.object(qr_control.time, "sleep"):
            result = qr_control.refresh()
            self.assertTrue(result["ok"])
            self.assertTrue(result["refreshed"])
            self.assertEqual(result["qrDataUrl"], "data:image/png;base64,switched-to-qr")
            self.assertEqual(click.call_count, 1)

    def test_unknown_popup_is_still_refused(self):
        warning_tree = {"role": "desktop-frame", "children": [
            {"role": "filler", "children": [
                {"role": "label", "name": "Tip"},
                {"role": "label", "name": "Your account has security risks"},
                {"role": "push-button", "name": "OK",
                 "bounds": {"x": 593, "y": 408, "width": 92, "height": 30}},
            ]},
        ]}

        class Opener:
            def open(self, request, **kwargs):
                return Response({"tree": warning_tree})

        with patch.object(qr_control.Path, "read_text", return_value="test-token"), \
             patch.object(qr_control.urllib.request, "build_opener", return_value=Opener()), \
             patch.object(qr_control.subprocess, "run") as click, \
             patch.object(qr_control.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "客户端有提示框"):
                qr_control.refresh()
            self.assertEqual(click.call_count, 0)


if __name__ == "__main__":
    unittest.main()

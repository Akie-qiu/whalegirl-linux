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


if __name__ == "__main__":
    unittest.main()

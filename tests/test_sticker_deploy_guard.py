"""Release fingerprints detect concurrent changes outside the sticker patch."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sticker_speed_deploy', ROOT / 'deploy/deploy_sticker_picker_speed.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class StickerDeployGuardTests(unittest.TestCase):
    def folder(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        return Path(tempfile.mkdtemp(prefix='sticker-deploy-guard-', dir=runs))

    def test_unrelated_gemini_source_change_is_detected(self):
        root = self.folder()
        (root / 'personas.py').write_bytes(b'Gemini current persona')
        baseline = deploy.release_snapshot(root)
        (root / 'personas.py').write_bytes(b'Gemini newer persona')
        self.assertNotEqual(deploy.release_snapshot(root), baseline)
        (root / 'new-feature.py').write_bytes(b'new feature')
        self.assertIn('new-feature.py', deploy.release_snapshot(root))

    def test_expected_candidate_diff_allows_only_listed_patch_files(self):
        root = self.folder()
        (root / 'persona.py').write_bytes(b'Gemini persona')
        (root / 'delivery.py').write_bytes(b'old delivery')
        baseline = deploy.release_snapshot(root)
        after = hashlib.sha256(b'fixed delivery').hexdigest()
        expected = {**baseline, 'delivery.py': after}
        (root / 'delivery.py').write_bytes(b'fixed delivery')
        self.assertEqual(deploy.release_snapshot(root), expected)
        (root / 'persona.py').write_bytes(b'accidental overwrite')
        self.assertNotEqual(deploy.release_snapshot(root), expected)

    def test_test_output_does_not_change_source_baseline(self):
        root = self.folder()
        (root / 'main.py').write_bytes(b'main')
        baseline = deploy.release_snapshot(root)
        (root / 'test-runs').mkdir()
        (root / 'test-runs/result.log').write_bytes(b'test result')
        self.assertEqual(deploy.release_snapshot(root), baseline)


if __name__ == '__main__':
    unittest.main()

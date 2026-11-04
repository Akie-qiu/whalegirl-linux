"""Tests for deploy/tune_girlish_settings.py."""
import json
from pathlib import Path
import tempfile
import unittest

from deploy.tune_girlish_settings import tune_file_config, SETTINGS_UPDATES, TARGET_TEMPERATURE

ROOT = Path(__file__).resolve().parents[1]


class TuneGirlishSettingsTests(unittest.TestCase):
    def test_tune_file_config_updates_settings_and_profiles(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            settings_file = tmp / 'settings.json'
            profiles_file = tmp / 'model-profiles.json'

            initial_settings = {
                'chat_short_reply_chars': 48,
                'persona_density': 'low',
                'teasing': 'low',
                'sticker_usage_target': 0,
                'max_tokens': 220,
                'model_temperature': 0.55,
                'other_preserved': 'keep_me',
            }
            settings_file.write_text(json.dumps(initial_settings), 'utf-8')

            initial_profiles = {
                'default': {'api_url': 'http://example.com', 'model_temperature': 0.6},
                'flash': {'api_url': 'http://example.com/flash', 'model_temperature': 0.7},
            }
            profiles_file.write_text(json.dumps(initial_profiles), 'utf-8')

            report = tune_file_config(tmp)

            self.assertEqual(report['target_temperature'], 0.95)
            self.assertEqual(report['updated_settings']['reply_max_parts'], 3)
            self.assertEqual(report['updated_settings']['chat_short_reply_chars'], 56)
            self.assertEqual(report['updated_settings']['chat_short_reply_sentences'], 2)
            self.assertEqual(report['updated_settings']['persona_density'], 'normal')
            self.assertEqual(report['updated_settings']['teasing'], 'normal')
            self.assertEqual(report['updated_settings']['sticker_usage_target'], 45)
            self.assertEqual(report['updated_settings']['max_tokens'], 400)
            self.assertEqual(report['updated_settings']['model_temperature'], 0.95)

            saved_settings = json.loads(settings_file.read_text('utf-8'))
            self.assertEqual(saved_settings['other_preserved'], 'keep_me')
            for k, v in SETTINGS_UPDATES.items():
                self.assertEqual(saved_settings[k], v)

            saved_profiles = json.loads(profiles_file.read_text('utf-8'))
            self.assertEqual(saved_profiles['default']['model_temperature'], TARGET_TEMPERATURE)
            self.assertEqual(saved_profiles['flash']['model_temperature'], TARGET_TEMPERATURE)


if __name__ == '__main__':
    unittest.main()

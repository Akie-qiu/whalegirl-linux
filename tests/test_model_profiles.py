"""Independent model-card settings and encrypted-key isolation."""
from pathlib import Path
import tempfile
import unittest

from whalegirl_linux.settings import Config, ModelProfile, Settings


class ModelProfileIsolationTests(unittest.TestCase):
    def test_legacy_config_migrates_and_profiles_keep_separate_keys(self):
        with tempfile.TemporaryDirectory(prefix='model-profiles-') as raw:
            config = Config(Path(raw))
            config.save(Settings(enabled=False, account_id='account'), 'legacy-key')

            config.save_model_profile(
                'flash', ModelProfile(api_url='https://flash.example/v1', api_style='openai', model='flash-model'),
                'flash-key', activate=False)
            config.save_model_profile(
                'pro', ModelProfile(api_url='https://pro.example/v1', api_style='anthropic', model='pro-model'),
                'pro-key', activate=False)

            self.assertEqual(config.api_key('custom'), 'legacy-key')
            self.assertEqual(config.api_key('flash'), 'flash-key')
            self.assertEqual(config.api_key('pro'), 'pro-key')
            self.assertEqual(config.model_profiles_public()['flash']['api_url'], 'https://flash.example/v1')
            self.assertTrue(config.model_profiles_public()['pro']['api_key_configured'])
            self.assertNotIn('flash-key', str(config.public()))

    def test_activate_loads_only_the_selected_profile(self):
        with tempfile.TemporaryDirectory(prefix='model-profiles-active-') as raw:
            config = Config(Path(raw))
            config.save(Settings(enabled=False, account_id='account'), 'custom-key')
            profile = ModelProfile(api_url='https://relay.example/v2', api_style='openai', model='relay-model',
                                   max_tokens=333, reasoning_effort='low', model_stream_enabled=True)
            config.save_model_profile('flash', profile, 'flash-key', activate=True)

            self.assertEqual(config.active_model_profile, 'flash')
            self.assertEqual(config.settings.api_url, 'https://relay.example/v2')
            self.assertEqual(config.settings.model, 'relay-model')
            self.assertEqual(config.settings.max_tokens, 333)
            self.assertEqual(config.api_key(), 'flash-key')
            self.assertEqual(config.model_profiles_public()['custom']['api_url'], 'https://api.deepseek.com/chat/completions')

    def test_unconfigured_profile_never_borrows_legacy_key_after_restart(self):
        with tempfile.TemporaryDirectory(prefix='model-profiles-no-fallback-') as raw:
            folder = Path(raw)
            config = Config(folder)
            config.save(Settings(enabled=False), 'custom-key')
            config.save_model_profile('pro', ModelProfile(model='pro-model'), activate=True)
            self.assertEqual(config.api_key(), '')
            self.assertFalse(config.public()['api_key_configured'])
            reloaded = Config(folder)
            self.assertEqual(reloaded.active_model_profile, 'pro')
            self.assertEqual(reloaded.api_key(), '')
            self.assertEqual(reloaded.api_key('custom'), 'custom-key')

    def test_profiles_survive_restart_and_other_settings_save(self):
        with tempfile.TemporaryDirectory(prefix='model-profiles-persistence-') as raw:
            folder = Path(raw)
            config = Config(folder)
            config.save_search_key('search-key')
            for identity, style in [('flash', 'openai'), ('pro', 'anthropic'), ('responses', 'responses')]:
                config.save_model_profile(identity, ModelProfile(api_url=f'https://{identity}.example/v1',
                    api_style=style, model=identity+'-model'), identity+'-key', activate=True)
            config.save(config.settings.model_copy(update={'chat_cooldown': 7}))
            reloaded = Config(folder)
            self.assertEqual(reloaded.active_model_profile, 'responses')
            self.assertEqual(reloaded.settings.api_url, 'https://responses.example/v1')
            self.assertEqual(reloaded.search_api_key(), 'search-key')
            self.assertEqual(reloaded.settings.chat_cooldown, 7)
            for identity in ('flash', 'pro', 'responses'):
                self.assertEqual(reloaded.api_key(identity), identity+'-key')
                self.assertEqual(reloaded.public()['model_profiles'][identity]['api_url'], f'https://{identity}.example/v1')

    def test_general_config_accepts_readonly_profile_metadata(self):
        from whalegirl_linux.app import ConfigRequest
        config_request = ConfigRequest.model_validate({'settings': {
            'enabled': False, 'active_model_profile': 'pro', 'model_profiles': {'pro': {'model': 'ignored'}},
            'chat_cooldown': 7}})
        self.assertEqual(config_request.settings.chat_cooldown, 7)
        self.assertNotIn('model_profiles', config_request.settings.model_dump())

    def test_model_profile_api_returns_isolated_cards(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app

        with tempfile.TemporaryDirectory(prefix='model-profiles-api-') as raw:
            app = create_app(Path(raw), run_background=False)
            with TestClient(app) as client:
                headers = {'Authorization': 'Bearer ' + app.state.config.admin_token}
                response = client.get('/api/model-profiles', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.json()['profiles']), {'flash', 'pro', 'responses', 'custom'})
                response = client.post('/api/model-profiles', headers=headers, json={
                    'profile_id': 'pro',
                    'profile': {'api_url': 'https://pro.example/v1', 'api_style': 'openai', 'model': 'pro-model',
                                'max_tokens': 240, 'reasoning_effort': 'low', 'model_stream_enabled': False},
                    'api_key': 'pro-key', 'activate': True,
                })
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body['active_model_profile'], 'pro')
                self.assertEqual(body['model_profiles']['pro']['api_url'], 'https://pro.example/v1')
                self.assertTrue(body['model_profiles']['pro']['api_key_configured'])
                self.assertNotIn('pro-key', response.text)


if __name__ == '__main__':
    unittest.main()

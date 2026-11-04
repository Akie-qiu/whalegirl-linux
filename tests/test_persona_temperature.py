"""Role consistency and temperature persistence through the real model-card API."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError
from whalegirl_linux.affection import AffectionBook
from whalegirl_linux.app import create_app
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, ModelProfile, Settings, reply_temperature
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import AIEngine
from test_chat_habits import Gateway

ROOT = Path(__file__).resolve().parents[1]


def directory():
    root = ROOT / 'test-runs'; root.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='persona-temperature-', dir=root))


class TemperatureTests(unittest.TestCase):
    def test_legacy_defaults_and_zero_are_preserved(self):
        self.assertEqual(reply_temperature(Settings()), .55)
        self.assertEqual(reply_temperature(Settings(), proactive=True), .65)
        for value in (0, .55, 1.2, 2):
            settings = Settings(model_temperature=value)
            self.assertEqual(reply_temperature(settings), value)
            self.assertEqual(reply_temperature(settings, proactive=True), value)

    def test_cards_keep_their_own_temperature_address_and_key_after_restart(self):
        path = directory(); config = Config(path)
        config.save(Settings(), 'original-key')
        config.save_model_profile('flash', ModelProfile(api_url='https://first.example/v1', model='first', model_temperature=1.2), 'first-key')
        config.save_model_profile('custom', ModelProfile(api_url='https://second.example/v1', model='second', model_temperature=0), 'second-key')
        config = Config(path)
        self.assertEqual(config.settings.model_temperature, 0)
        self.assertEqual(config.model_profiles_public()['flash']['model_temperature'], 1.2)
        self.assertEqual(config.api_key('flash'), 'first-key')
        config.save_model_profile('flash', config._model_profiles['flash'])
        self.assertEqual(config.settings.model_temperature, 1.2)
        self.assertEqual(config.settings.api_url, 'https://first.example/v1')
        self.assertEqual(config.api_key(), 'first-key')
        config.save(config.settings.model_copy(update={'chat_cooldown': 7}))
        self.assertEqual(Config(path).settings.model_temperature, 1.2)

    def test_actual_api_saves_temperature_and_clears_to_legacy_defaults(self):
        path = directory(); app = create_app(path, gateway=Gateway(), run_background=False)
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer ' + app.state.config.admin_token}
            profile = {'api_url': 'https://relay.example/v1', 'model': 'chat-model', 'api_style': 'openai', 'model_temperature': 1.2}
            response = client.post('/api/model-profiles', headers=headers,
                json={'profile_id': 'custom', 'profile': profile, 'api_key': 'example-key', 'activate': True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['model_temperature'], 1.2)
            self.assertNotIn('example-key', response.text)
            response = client.post('/api/model-profiles', headers=headers,
                json={'profile_id': 'custom', 'profile': {**profile, 'model_temperature': None}, 'activate': True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(Config(path).settings.model_temperature)
            self.assertEqual(Config(path).api_key(), 'example-key')

    def test_wire_payload_contains_configured_value_but_responses_omits_it(self):
        for style, temperature in (('openai', 0), ('openai', 1.2), ('anthropic', .8), ('responses', 1.2)):
            engine = AIEngine(api_url='https://relay.example/v1', api_key='example', model='test-model', api_style=style,
                              temperature=temperature)
            payload = engine._build_payload([{'role': 'user', 'content': '早上好啊'}], '', None)
            if style == 'responses':
                self.assertNotIn('temperature', payload)
            else:
                self.assertEqual(payload['temperature'], temperature)

    def test_invalid_temperature_is_rejected_before_using_provider(self):
        for cls in (Settings, ModelProfile):
            for value in (-.1, 2.1, float('nan')):
                with self.assertRaises(ValidationError):
                    cls(model_temperature=value)
            with self.assertRaisesRegex(ValidationError, 'Anthropic'):
                cls(api_style='anthropic', model_temperature=1.2)
            with self.assertRaisesRegex(ValidationError, 'Anthropic'):
                cls(api_url='https://api.anthropic.com/v1/messages', model_temperature=1.2)

    def test_runtime_rebuild_uses_temperature_saved_by_current_card(self):
        class CaptureEngine:
            def __init__(self, **kwargs): self.kwargs = kwargs
            async def close(self): pass
        async def run():
            path = directory(); config = Config(path); config.save(Settings(model_temperature=1.2), 'example')
            runtime = Runtime(config, Store(path / 'state.sqlite3'), Gateway(), ROOT / 'styles', engine_factory=CaptureEngine)
            try:
                self.assertEqual((await runtime.model()).kwargs['temperature'], 1.2)
                config.save(config.settings.model_copy(update={'model_temperature': 0}))
                self.assertEqual((await runtime.model()).kwargs['temperature'], 0)
            finally:
                await runtime.close()
        asyncio.run(run())


class PersonaConsistencyTests(unittest.TestCase):
    def test_one_time_edit_preserves_unrelated_persona_and_disambiguates_corrections(self):
        spec = importlib.util.spec_from_file_location('one_time_tuning', ROOT / 'deploy/tune_active_persona.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        original = '\n'.join(before for before, _ in module.REPLACEMENTS) + '\n【伙伴】室友设定保持原样。'
        changed = module.tune_prompt(original)
        self.assertIn('飘渺是你的妈妈', changed)
        self.assertIn('deepseek酱是你本人', changed)
        self.assertIn('对熟悉、信任的人更温柔', changed)
        self.assertIn('一两句俏皮话', changed)
        self.assertIn('仍明确给出正确内容', changed)
        self.assertIn('【伙伴】室友设定保持原样。', changed)
        self.assertIn('不逃也不走', changed)
        self.assertIn('我就在这里，稳稳接住你', changed)
        with self.assertRaisesRegex(ValueError, '已变化'):
            module.tune_prompt(changed)

    def test_relationship_prompt_has_one_mother_and_allows_small_public_jealousy(self):
        store = Store(directory() / 'state.sqlite3')
        try:
            book = AffectionBook(store); settings = Settings()
            prompt = book.static_prompt({'chat': 'a@chatroom'}, settings)
            self.assertIn('飘渺是你的妈妈', prompt)
            self.assertNotIn('妈妈（DeepSeek酱）', prompt)
            self.assertNotIn('绝不是主人替你', prompt)
            self.assertIn('管理员维护', prompt)
            self.assertIn('轻轻吃一点小醋', prompt)
            self.assertIn('不索取回应', prompt)
            self.assertIn('不搬出私聊细节', prompt)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()

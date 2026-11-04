"""Broader sticker choices without changing permissions or receipt protection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whalegirl_linux.conversation import pair_sticker, plan_actions, sticker_static_prompt
from whalegirl_linux.safety import SafetyGuard
from whalegirl_linux.settings import Settings
from whalegirl_linux.stickers import StickerCatalog
from whalegirl_linux.store import Store

ROOT = Path(__file__).resolve().parents[1]


class CatalogDiversityTests(unittest.TestCase):
    def setUp(self):
        self.catalog = StickerCatalog(ROOT / 'unused-test-catalog-directory')
        self.catalog.entries = {str(i): '开心' if i < 6 else '惊讶' for i in range(12)}
        self.catalog.profiles = {}
        self.catalog.available = list(self.catalog.entries)

    def test_recent_sticker_remains_selectable(self):
        self.catalog.entries = {'a': '开心'}
        self.catalog.available = ['a']
        history = {'a': {'count': 10, 'last': 1000, 'recent': True}}
        self.assertEqual(self.catalog.candidates('开心', 20, (), history), {'a': '开心'})

    def test_recent_options_rank_after_equally_relevant_unused_options(self):
        history = {'0': {'count': 10, 'last': 1000, 'recent': True}}
        chosen = list(self.catalog.candidates('开心', 4, (), history))
        self.assertNotIn('0', chosen)
        self.assertTrue(set(chosen) <= set(self.catalog.available))

    def test_exploration_slot_includes_a_less_used_alternative(self):
        history = {str(i): {'count': 20, 'last': 1000, 'recent': False} for i in range(6)}
        chosen = list(self.catalog.candidates('开心', 4, (), history))
        self.assertTrue(all(int(i) < 6 for i in chosen[:3]))
        self.assertGreaterEqual(int(chosen[-1]), 6)

    def test_no_minute_based_rotation_without_real_usage_changes(self):
        with patch('whalegirl_linux.stickers.time.time', return_value=1):
            first = self.catalog.candidates('开心', 4)
        with patch('whalegirl_linux.stickers.time.time', return_value=180):
            second = self.catalog.candidates('开心', 4)
        self.assertEqual(list(first), list(second))

    def test_global_disable_and_chat_exclusion_remain_hard(self):
        self.catalog.disabled = {'0'}
        chosen = self.catalog.candidates('开心', 20, {'1'})
        self.assertNotIn('0', chosen)
        self.assertNotIn('1', chosen)
        self.assertEqual(len(chosen), 10)

    def test_candidates_respect_bound_and_zero(self):
        self.assertEqual(len(self.catalog.candidates('开心', 4)), 4)
        self.assertEqual(self.catalog.candidates('开心', 0), {})


class ModelChoiceTests(unittest.TestCase):
    def test_sarcastic_choice_is_not_vetoed_by_keyword_tags(self):
        choices = {'a': '表达：否定、拒绝，强度中等；直接拒绝'}
        result = plan_actions({'actions': [
            {'type': 'text', 'text': '我也想你啦'}, {'type': 'sticker', 'id': 'a'}]}, choices, Settings())
        self.assertEqual([a['type'] for a in result], ['text', 'sticker'])

    def test_legacy_suggestion_is_not_vetoed_by_keyword_tags(self):
        settings = Settings(sticker_after_text='high')
        actions, status = pair_sticker({'sticker_suggestion': 'a'},
            [{'type': 'text', 'text': '想你啦', 'display': '想你啦'}],
            {'a': '否定、拒绝'}, settings)
        self.assertEqual(actions[-1]['id'], 'a')
        self.assertEqual(status['result'], 'model_selected')

    def test_unknown_sticker_is_still_rejected(self):
        actions = plan_actions({'actions': [{'type': 'sticker', 'id': 'unknown'}]}, {'a': '开心'}, Settings())
        self.assertFalse(any(a.get('id') == 'unknown' for a in actions))

    def test_plain_text_plan_does_not_force_a_sticker(self):
        result = plan_actions({'actions': [{'type': 'text', 'text': '收到啦'}]}, {'a': '开心'}, Settings())
        self.assertEqual([a['type'] for a in result], ['text'])

    def test_expression_prompt_keeps_persona_and_emotional_carrier(self):
        prompt = sticker_static_prompt(Settings())
        self.assertIn('标签是表达参考', prompt)
        self.assertIn('核心人设与人物关系', prompt)
        self.assertIn('不描述画面', prompt)


class StickerHistoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'test-runs').mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(prefix='sticker-diversity-', dir=ROOT / 'test-runs')
        self.addCleanup(self.folder.cleanup)
        self.store = Store(Path(self.folder.name) / 'state.sqlite3')
        self.addCleanup(self.store.close)

    def test_usage_history_is_local_to_account_and_chat(self):
        with patch('whalegirl_linux.store.time.time', return_value=1000):
            self.store.record_sticker('account', 'group', 'a')
            self.store.record_sticker('account', 'group', 'a')
            self.store.record_sticker('account', 'other', 'b')
            self.store.record_sticker('other', 'group', 'c')
            history = self.store.sticker_selection_history('account', 'group', 120)
        self.assertEqual(set(history), {'a'})
        self.assertEqual(history['a']['count'], 2)
        self.assertTrue(history['a']['recent'])

    def test_zero_window_disables_recent_penalty_but_keeps_frequency(self):
        with patch('whalegirl_linux.store.time.time', return_value=1000):
            self.store.record_sticker('account', 'group', 'a')
            history = self.store.sticker_selection_history('account', 'group', 0)
        self.assertFalse(history['a']['recent'])
        self.assertEqual(history['a']['count'], 1)

    def test_old_history_expires_without_deleting_data(self):
        with patch('whalegirl_linux.store.time.time', return_value=1000):
            self.store.record_sticker('account', 'group', 'a')
        with patch('whalegirl_linux.store.time.time', return_value=1000 + 8*86400):
            self.assertEqual(self.store.sticker_selection_history('account', 'group', 120), {})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM sticker_usage').fetchone()[0], 1)


class StickerRepeatGuardTests(StickerHistoryTests):
    def setUp(self):
        super().setUp()
        self.guard = SafetyGuard(self.store)
        self.settings = Settings(safety_per_minute=0, safety_per_hour=0,
            safety_chat_per_hour=0, safety_per_day=0, sticker_per_hour=0, sticker_per_day=0)

    def record_action(self, kind, state):
        event = {'key': 'account/group/1', 'account': 'account', 'chat': 'group',
            'sender': 'fixture', 'sender_id': 'fixture', 'content': 'fixture', 'timestamp': 1000}
        self.store.insert(event)
        with patch('whalegirl_linux.store.time.time', return_value=1000):
            action_id = self.store.action_start(event['key'], event, 0,
                {'type': kind, 'display': 'fixture reply'})
            self.store.action_finish(action_id, state)

    def test_confirmed_sticker_can_be_chosen_again(self):
        self.record_action('sticker', 'sent')
        self.assertTrue(self.guard.check('account', 'group', self.settings,
            kind='sticker', reply='fixture reply', now=1001)['allowed'])

    def test_uncertain_and_inflight_stickers_remain_protected(self):
        self.record_action('sticker', 'uncertain')
        result = self.guard.check('account', 'group', self.settings,
            kind='sticker', reply='fixture reply', now=1001)
        self.assertEqual(result['code'], 'duplicate_reply')
        self.store.db.execute("UPDATE outbound_actions SET state='sending'")
        self.store.db.commit()
        self.assertFalse(self.guard.check('account', 'group', self.settings,
            kind='sticker', reply='fixture reply', now=1001)['allowed'])

    def test_text_duplicate_protection_is_unchanged(self):
        self.record_action('text', 'sent')
        self.assertEqual(self.guard.check('account', 'group', self.settings,
            kind='text', reply='fixture reply', now=1001)['code'], 'duplicate_reply')

    def test_account_total_limit_still_applies_to_stickers(self):
        self.record_action('sticker', 'sent')
        settings = self.settings.model_copy(update={'safety_per_hour': 1})
        self.assertEqual(self.guard.check('account', 'group', settings,
            kind='sticker', reply='another sticker', now=1001)['code'], 'hour_limit')


if __name__ == '__main__':
    unittest.main()

"""Conversation-state decay and non-destructive model JSON parsing."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from whalegirl_linux.autonomy import AutonomyService
from whalegirl_linux.conversation import current_mood_state, update_mood_after_turn
from whalegirl_linux.settings import Settings
from whalegirl_linux.stickers import parse_json
from whalegirl_linux.store import Store


ROOT = Path(__file__).resolve().parents[1]


class ModelJsonPreservationTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'should_reply': True, 'actions': [{
            'type': 'text', 'text': '请保留 <think>这个标签</think> 原文和 ```json 围栏'}]}
        self.raw = json.dumps(self.payload, ensure_ascii=False)

    def test_valid_object_keeps_literal_thought_tags(self):
        self.assertEqual(parse_json(self.raw), self.payload)

    def test_valid_array_keeps_literal_thought_tags(self):
        values = [self.payload, '<think>一段原文</think>']
        self.assertEqual(parse_json(json.dumps(values, ensure_ascii=False)), values)

    def test_fenced_json_keeps_literal_tags(self):
        self.assertEqual(parse_json('```json\n' + self.raw + '\n```'), self.payload)

    def test_external_thought_json_is_skipped_and_body_is_preserved(self):
        raw = '<think>{"actions":[{"type":"text","text":"内部草稿"}]}</think>\n```json\n'
        self.assertEqual(parse_json(raw + self.raw + '\n```'), self.payload)

    def test_short_annotation_and_plain_preamble_still_work(self):
        for prefix in ('[内部备注] 准备接话：', 'Here is the response: ', '\ufeff'):
            with self.subTest(prefix=prefix):
                self.assertEqual(parse_json(prefix + self.raw), self.payload)
        self.assertEqual(parse_json('结果列表：\n[{"id":1},{"id":2}]'), [{'id': 1}, {'id': 2}])

    def test_malformed_outer_object_cannot_revive_a_nested_action(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_json('{"broken":true,"nested":{"should_reply":true,"actions":[]} trailing')

    def test_malformed_outer_array_cannot_revive_a_nested_object(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_json('[{"should_reply":true,"actions":[]} trailing')

    def test_unclosed_thought_cannot_be_treated_as_an_action(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_json('<think>尚未完成的草稿：' + self.raw)

    def test_valid_scalar_values_keep_the_existing_contract(self):
        for raw in ('true', 'null', '123', '"<think>文字</think>"'):
            with self.subTest(raw=raw):
                self.assertEqual(parse_json(raw), json.loads(raw))


class MoodEventClockTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'test-runs'
        runs.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='soul-regressions-', dir=runs))
        self.store = Store(self.folder / 'state.sqlite3')
        self.now = datetime(2026, 10, 31, 14, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.settings = Settings(account_id='account', allowed_chat_ids=['test@chatroom'],
                                 autonomy_enabled=True, mood_engine_enabled=True)
        self.event = {'account': 'account', 'chat': 'test@chatroom', 'key': 'source-1',
                      'sender_id': 'friend', 'sender':'friend', 'kind':1, 'content':'本轮明确互动',
                      'is_self': 0, 'timestamp': self.now, 'local_id': 1}
        self.store.observe([self.event])
        self.autonomy = AutonomyService(SimpleNamespace(
            store=self.store, config=SimpleNamespace(settings=self.settings)))

    def tearDown(self):
        self.store.close()

    def mood(self, elapsed=0):
        return current_mood_state(self.store, self.settings, 'account', 'test@chatroom', now=self.now + elapsed)

    def model_event(self, mood):
        with patch('whalegirl_linux.autonomy.time.time', return_value=self.now):
            return self.autonomy.accept(self.event, [self.event], {'should_reply': False, 'mood_update': {
                'mood': mood, 'source_key': self.event['key'], 'energy_delta': 2, 'reason': '本轮明确互动'}})

    def test_autonomous_neutral_turns_do_not_extend_a_model_emotion(self):
        self.assertTrue(self.model_event('jealous'))
        for elapsed in range(600, 3601, 600):
            update_mood_after_turn(self.store, self.settings, self.event,
                [{**self.event, 'content': '今天天气怎么样'}], now=self.now + elapsed)
        self.assertEqual(self.mood(3601)['mood'], 'normal')
        self.assertEqual(self.mood(3601)['updated'], self.now)

    def test_legacy_neutral_turns_do_not_extend_a_real_emotion(self):
        self.settings.autonomy_enabled = False
        update_mood_after_turn(self.store, self.settings, self.event,
            [{**self.event, 'content': '请你吃白米饭，你真可爱'}], now=self.now)
        self.assertEqual(self.mood(1)['mood'], 'proud')
        for elapsed in range(600, 3601, 600):
            update_mood_after_turn(self.store, self.settings, self.event,
                [{**self.event, 'content': '今天天气怎么样'}], now=self.now + elapsed)
        self.assertEqual(self.mood(3601)['mood'], 'normal')
        self.assertEqual(self.mood(3601)['updated'], self.now)

    def test_technical_names_do_not_trigger_romantic_or_jealous_fallback(self):
        for enabled in (True, False):
            self.settings.autonomy_enabled = enabled
            for content in ('Claude的API又报错了，帮我排查连接问题', 'Claude强制使用流式接口',
                            '小克今天在公寓看什么书呀'):
                with self.subTest(autonomy=enabled, content=content):
                    update_mood_after_turn(self.store, self.settings, self.event,
                        [{**self.event, 'content': content}], now=self.now)
                    self.assertEqual(self.mood(1)['mood'], 'normal')

    def test_model_can_still_choose_shyness_and_jealousy(self):
        for index, mood in enumerate(('flustered', 'jealous'), 1):
            with self.subTest(mood=mood):
                self.event = {**self.event, 'key':'source-'+str(index), 'local_id':index}
                self.store.observe([self.event])
                self.assertTrue(self.model_event(mood))
                update_mood_after_turn(self.store, self.settings, self.event,
                    [{**self.event, 'content': 'Claude的接口报错了'}], now=self.now + 1,
                    model_updated=True)
                self.assertEqual(self.mood(2)['mood'], mood)

    def test_legacy_direct_affection_keeps_its_lightweight_fallback(self):
        self.settings.autonomy_enabled = False
        update_mood_after_turn(self.store, self.settings, self.event,
            [{**self.event, 'content': '抱抱你'}], now=self.now)
        self.assertEqual(self.mood(1)['mood'], 'flustered')


if __name__ == '__main__':
    unittest.main()

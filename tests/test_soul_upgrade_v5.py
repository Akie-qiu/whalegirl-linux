import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from whalegirl_linux.autonomy import AutonomyService, STATIC_PROMPT as AUTONOMY_PROMPT
from whalegirl_linux.conversation import (
    MOOD_LABELS,
    current_life_scene,
    current_mood_state,
    habit_static_prompt,
    update_mood_after_turn,
)
from whalegirl_linux.message_payload import xml_root
from whalegirl_linux.settings import Settings as BotSettings
from whalegirl_linux.stickers import parse_json
from whalegirl_linux.store import Store


class SoulUpgradeV5Tests(unittest.TestCase):
    def test_parse_json_handles_think_tags_fences_and_bracket_preamble(self):
        raw1 = '<think>\n嗯……先想一下怎么回\n</think>\n```json\n{"inner_voice":"有点小开心","should_reply":true,"actions":[]}\n```'
        self.assertEqual(parse_json(raw1)['inner_voice'], '有点小开心')

        raw2 = '[内部备注] 准备接话：{"inner_voice":"在聊吃的呢","should_reply":true,"actions":[{"type":"text","text":"来啦……"}]}'
        parsed2 = parse_json(raw2)
        self.assertIsInstance(parsed2, dict)
        self.assertEqual(parsed2['inner_voice'], '在聊吃的呢')

        raw3 = '结果列表：\n[{"id": 1}, {"id": 2}]'
        self.assertEqual(parse_json(raw3), [{'id': 1}, {'id': 2}])

    def test_xml_root_handles_xml_comments_without_crashing(self):
        xml = '<msg><!-- wechat comment --><appmsg><title>测试标题</title></appmsg></msg>'
        root = xml_root(xml)
        self.assertIsNotNone(root)
        self.assertEqual(root.findtext('./appmsg/title'), '测试标题')

    def test_autonomy_keeps_life_scene_and_accepts_contextual_model_emotions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            try:
                settings = BotSettings(account_id='wxid_bot', allowed_chat_ids=['room@chatroom'],
                                       autonomy_enabled=True, mood_engine_enabled=True)
                scene = current_life_scene(store, settings, 'wxid_bot', 'room@chatroom', now=1730005200.0)
                self.assertTrue(scene)
                self.assertNotIn('实际经历以收到的消息和工具结果为准', scene)

                first = {'account': 'wxid_bot', 'chat': 'room@chatroom', 'key': 'k1',
                         'sender_id': 'friend', 'sender':'friend', 'kind':1,'local_id':1,
                         'content':'这轮在比较两个人物的表现', 'timestamp': 1000.0, 'is_self': 0}
                store.observe([first])
                service = AutonomyService(SimpleNamespace(store=store, config=SimpleNamespace(settings=settings)))
                # The main model can choose jealousy for an actual comparison.
                with patch('whalegirl_linux.autonomy.time.time', return_value=1000.0):
                    applied = service.accept(first, [first], {'should_reply': False, 'mood_update': {
                        'mood': 'jealous', 'source_key': 'k1', 'energy_delta': 2, 'reason': '本轮明确比较'}})
                self.assertTrue(applied)
                state = current_mood_state(store, settings, 'wxid_bot', 'room@chatroom', now=1001.0)
                self.assertEqual(state['mood'], 'jealous')
                self.assertIn('jealous', MOOD_LABELS)

                # Contextual shyness remains available, without name-only rules.
                first = {**first,'key':'k2','local_id':2,'content':'这是另一轮亲密互动'}
                store.observe([first])
                with patch('whalegirl_linux.autonomy.time.time', return_value=1002.0):
                    applied = service.accept(first, [first], {'should_reply': False, 'mood_update': {
                        'mood': 'flustered', 'source_key': 'k2', 'energy_delta': 2, 'reason': '本轮亲密互动'}})
                self.assertTrue(applied)
                state = current_mood_state(store, settings, 'wxid_bot', 'room@chatroom', now=1003.0)
                self.assertEqual(state['mood'], 'flustered')

                # When model_updated=True, local heuristic must not overwrite model's choice
                update_mood_after_turn(
                    store, settings, first,
                    [{'content': 'GPT真厉害比你强', 'is_self': 0}],
                    now=1004.0, model_updated=True,
                )
                state = current_mood_state(store, settings, 'wxid_bot', 'room@chatroom', now=1005.0)
                self.assertEqual(state['mood'], 'flustered')
            finally:
                store.close()

    def test_inner_voice_completely_removed_from_prompts(self):
        root = Path(__file__).resolve().parents[1]
        runtime_src = (root / 'whalegirl_linux' / 'runtime.py').read_text('utf-8')
        proactive_src = (root / 'whalegirl_linux' / 'proactive.py').read_text('utf-8')
        live_patched = Path(r'C:\Users\bgya7\.gemini\antigravity\brain\a388a006-9e45-4009-9c8c-28ec56a5218f\scratch\patched_live\runtime.py')

        self.assertNotIn('inner_voice', runtime_src)
        self.assertNotIn('inner_voice', proactive_src)
        if live_patched.exists():
            self.assertNotIn('inner_voice', live_patched.read_text('utf-8'))

    def test_excluded_phrases_not_injected(self):
        settings = BotSettings()
        combined = habit_static_prompt(settings) + '\n' + AUTONOMY_PROMPT
        runtime_src = (Path(__file__).resolve().parents[1] / 'whalegirl_linux' / 'runtime.py').read_text('utf-8')
        combined += '\n' + runtime_src
        for forbidden in ('潜台词直觉', '弦外之音', 'CPU过热', 'CPU 过热', '先算账，再馋饭', '白饭脊椎', '看破潜台词', '害羞过载'):
            self.assertNotIn(forbidden, combined)


if __name__ == '__main__':
    unittest.main()

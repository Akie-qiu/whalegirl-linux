"""Chat formatting must not expose internal sticker markers."""
import unittest

from whalegirl_linux.conversation import clean_text, plan_actions
from whalegirl_linux.settings import Settings


class ChatFormatTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(chat_habits_enabled=True, stickers_enabled=True,
            reply_max_parts=6, sticker_max_per_reply=1)
        self.choices = {'a' * 32: '开心'}

    def test_blank_paragraphs_become_separate_bubbles(self):
        actions = plan_actions({'actions':[{'type':'text','text':'第一段\n\n第二段'}]}, {}, self.settings)
        self.assertEqual([a['text'] for a in actions], ['第一段', '第二段'])

    def test_internal_marker_is_removed_and_valid_sticker_is_recovered(self):
        actions = plan_actions({'actions':[{'type':'text','text':'先说一句\n\n再说一句\n\n[原生表情包]'}],
            'sticker_suggestion':'a' * 32}, self.choices, self.settings)
        self.assertEqual([a['type'] for a in actions], ['text', 'text', 'sticker'])
        self.assertEqual([a['text'] for a in actions if a['type']=='text'], ['先说一句', '再说一句'])
        self.assertTrue(all('[原生表情' not in a.get('text','') for a in actions))
        self.assertTrue(all('[表情包]' not in a.get('text','') for a in actions))

    def test_marker_without_sticker_id_is_stripped_without_inventing_one(self):
        actions = plan_actions({'actions':[{'type':'text','text':'就这样\n\n[原生表情包]'}]}, self.choices, self.settings)
        self.assertEqual([a['type'] for a in actions], ['text'])
        self.assertEqual(actions[0]['text'], '就这样')

    def test_clean_text_normalizes_internal_marker_and_excess_blank_lines(self):
        self.assertEqual(clean_text('一句\n\n[原生表情]\n\n\n二句'), '一句\n\n二句')


if __name__ == '__main__':
    unittest.main()

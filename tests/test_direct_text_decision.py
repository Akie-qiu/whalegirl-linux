"""Plain explicit answers can avoid a model-only JSON conversion."""
import unittest

from whalegirl_linux.model_reply import direct_text_decision


class DirectTextDecisionTests(unittest.TestCase):
    def test_explicit_plain_answer_preserves_model_text(self):
        raw=' 好呀\n这就陪你聊会儿 '
        self.assertEqual(direct_text_decision(raw,explicit=True),{
            'should_reply':True,'actions':[{'type':'text','text':raw}]})

    def test_natural_image_tool_and_resumed_turns_keep_structured_path(self):
        for options in ({},{'explicit':False},{'explicit':True,'image_keys':['photo']},
                        {'explicit':True,'tool_calls':[{'id':'call'}]},
                        {'explicit':True,'resumed':True}):
            with self.subTest(options=options):
                self.assertIsNone(direct_text_decision('好呀',**options))

    def test_malformed_json_and_code_fences_are_not_chat_text(self):
        for raw in ('{"actions":', '\ufeff {"actions":', '[{"type":"text"}', '```json\n{"actions":[]}',
                    '这是结果 {"should_reply":true,', '"好呀"'):
            with self.subTest(raw=raw):
                self.assertIsNone(direct_text_decision(raw,explicit=True))

    def test_empty_protocol_and_silence_markers_are_not_sent(self):
        for raw in ('','  ',None,{},'<think>还在思考','<|im_end|>',
                    'analysis: 思考中','NO_REPLY','[沉默]','保持沉默。','不回复'):
            with self.subTest(raw=raw):
                self.assertIsNone(direct_text_decision(raw,explicit=True))


if __name__=='__main__':unittest.main()

import time
import unittest

from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Settings


class SilenceCommandTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            account_id='wxid-bot',
            keywords=['鲸鱼娘'],
            natural_group_replies=True,
        )

    def message(self, text, *, chat='friend', mentioned=False, reply=None, kind=1):
        raw = {
            'isSelf': False,
            'type': kind,
            'content': text,
            'serverId': '42',
            'localId': 42,
            'timestamp': time.time(),
            'isMentioned': mentioned,
        }
        if reply is not None:
            raw['reply'] = reply
        event = Runtime.event('wxid-bot', chat, raw)
        return raw, event

    def test_private_silence_command_is_not_answered(self):
        for text in ('闭嘴', '别回复了', '你先安静点', '让AI闭嘴'):
            with self.subTest(text=text):
                raw, event = self.message(text)
                self.assertFalse(Runtime.should_reply(raw, event, self.settings))

    def test_group_silence_command_only_applies_when_addressed_to_bot(self):
        raw, event = self.message('@鲸鱼娘 闭嘴', chat='room@chatroom', mentioned=True)
        self.assertFalse(Runtime.should_reply(raw, event, self.settings))

        raw, event = self.message('鲸鱼娘，别回复我', chat='room@chatroom')
        self.assertFalse(Runtime.should_reply(raw, event, self.settings))

        raw, event = self.message('闭嘴', chat='room@chatroom')
        self.assertFalse(Runtime.silence_requested(raw, event, self.settings))
        self.assertTrue(Runtime.should_reply(raw, event, self.settings))

    def test_reply_to_bot_can_use_plain_shut_up_command(self):
        raw, event = self.message(
            '你闭嘴',
            chat='room@chatroom',
            reply={'senderId': 'wxid-bot', 'serverId': '42'},
            kind=49,
        )
        self.assertFalse(Runtime.should_reply(raw, event, self.settings))

    def test_discussion_about_the_phrase_is_not_a_silence_command(self):
        for text in ('我朋友让我闭嘴', '闭嘴这个梗笑死我了', '你知道闭嘴怎么说吗'):
            with self.subTest(text=text):
                raw, event = self.message(text)
                self.assertFalse(Runtime.silence_requested(raw, event, self.settings))
                self.assertTrue(Runtime.should_reply(raw, event, self.settings))


if __name__ == '__main__':
    unittest.main()

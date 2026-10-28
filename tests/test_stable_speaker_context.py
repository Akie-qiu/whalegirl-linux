"""Renamed and duplicate nicknames must retain their actual account identities."""
import unittest

from whalegirl_linux.context import build_context
import test_message_time_context as message_times


class StableSpeakerContextTests(unittest.IsolatedAsyncioTestCase):
    setUp = message_times.MessageTimeContextTests.setUp
    tearDown = message_times.MessageTimeContextTests.tearDown
    event = message_times.MessageTimeContextTests.event

    async def prompt(self, current, batch):
        messages, _, _ = await build_context(self.store, None, self.settings, current, batch, {})
        return messages[0]['content']

    async def test_rename_preserves_identity_in_history_and_current_message(self):
        previous = self.event(1, 20, '这个条件是奶茶不加糖')
        previous.update(sender='旧昵称', sender_id='wxid_same_person')
        current = self.event(2, 3, '这个条件里的奶茶可以换成红茶吗')
        current.update(sender='新昵称', sender_id='wxid_same_person')
        self.store.observe([previous, current])
        prompt = await self.prompt(current, [current])
        self.assertIn('旧昵称｜账号ID=wxid_same_person', prompt)
        self.assertIn('新昵称｜账号ID=wxid_same_person', prompt)

    async def test_same_nickname_keeps_different_people_distinct(self):
        first = self.event(1, 10, '我喜欢喝红茶')
        first.update(sender='同名用户', sender_id='wxid_first_person')
        second = self.event(2, 4, '我喜欢喝绿茶')
        second.update(sender='同名用户', sender_id='wxid_second_person')
        self.store.observe([first, second])
        prompt = await self.prompt(first, [first, second])
        self.assertIn('同名用户｜账号ID=wxid_first_person] 我喜欢喝红茶', prompt)
        self.assertIn('同名用户｜账号ID=wxid_second_person] 我喜欢喝绿茶', prompt)

    async def test_missing_identity_is_not_inferred_from_a_matching_nickname(self):
        first = self.event(1, 10, '我喜欢喝红茶')
        first.update(sender='同名用户', sender_id='wxid_known_person')
        second = self.event(2, 4, '我也喜欢喝红茶')
        second.update(sender='同名用户', sender_id='')
        self.store.observe([first, second])
        prompt = await self.prompt(first, [first, second])
        self.assertIn('同名用户｜账号ID=wxid_known_person] 我喜欢喝红茶', prompt)
        self.assertIn('同名用户｜账号ID未取得] 我也喜欢喝红茶', prompt)


if __name__ == '__main__':
    unittest.main()

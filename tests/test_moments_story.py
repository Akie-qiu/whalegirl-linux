"""Story choices reach the existing Moments and image pipelines without extra calls."""
import json
import unittest

import test_moments as moments_fixture
import test_moments_references as reference_fixture
from whalegirl_linux.moments import parse_moments_json
from whalegirl_linux.moments_creativity import STORY_CARDS, publication_references, story_brief, story_metadata


class StoryBriefTests(unittest.TestCase):
    def test_model_history_is_compact_while_local_history_keeps_image_details(self):
        history = [{'id': str(i), 'text': '文案' * 100, 'topic': '新选题',
                    'visual_concept': '旧场景', 'image_prompt': '旧生图细节' * 100,
                    'assets': ['old-asset'], 'story_progress': '有了进展'} for i in range(20)]
        refs = publication_references(history)
        self.assertEqual(len(refs), 8)
        self.assertEqual(len(refs[0]['text']), 100)
        self.assertEqual(refs[0]['topic'], '新选题')
        self.assertNotIn('image_prompt', refs[0])
        self.assertNotIn('visual_concept', refs[0])
        self.assertTrue(history[0]['image_prompt'])
        self.assertEqual(history[0]['assets'], ['old-asset'])

    def test_repeated_elements_are_advice_and_not_a_publish_filter(self):
        history = [{'text': '群里聊完又想吃米饭', 'visual_concept': '窗边桌前拿手机'} for _ in range(8)]
        brief = story_brief(history, 'first')
        self.assertEqual(brief['recent_count'], 8)
        self.assertEqual({v['element'] for v in brief['overused_content']}, {'围观群聊', '米饭吃饭'})
        self.assertEqual({v['element'] for v in brief['overused_visual']}, {'窗边', '桌边', '拿着手机'})
        self.assertEqual(len(brief['directions']), 3)
        self.assertEqual(brief, story_brief(history, 'first'))

    def test_used_story_directions_make_room_for_different_ones(self):
        original = story_brief([], 'same')['directions']
        history = [{'story_seed': card['id']} for card in original]
        offered = story_brief(history, 'same')['directions']
        self.assertFalse({v['id'] for v in original} & {v['id'] for v in offered})
        self.assertTrue({v['id'] for v in offered} <= {v['id'] for v in STORY_CARDS})

    def test_only_published_progress_is_offered_as_a_continuation(self):
        history = [{'state': 'draft', 'story_progress': '还没有发布'},
                   {'state': 'sent', 'topic': '配色练习', 'story_hook': '想威风却选了软萌色',
                    'story_progress': '完成了第一版'}]
        brief = story_brief(history, 'next')
        self.assertEqual(brief['continuation_candidates'], [
            {'topic': '配色练习', 'hook': '想威风却选了软萌色', 'progress': '完成了第一版'}])
        self.assertEqual(story_metadata({'story_seed': 'unknown', 'story_hook': {}}, brief),
                         {'story_seed': 'original', 'story_hook': '', 'story_progress': ''})

    def test_story_fields_keep_the_existing_local_json_quote_repair(self):
        result = parse_moments_json('{"participate":true,"text":"选好了",'
                                    '"story_seed":"original","story_hook":"选了"威风"的颜色",'
                                    '"story_progress":"完成第一版"}')
        self.assertEqual(result['story_hook'], '选了"威风"的颜色')


class StoryGenerationTests(unittest.IsolatedAsyncioTestCase):
    tearDown = moments_fixture.MomentsTests.tearDown
    generation_fixture = moments_fixture.MomentsTests.generation_fixture

    def setUp(self):
        (moments_fixture.ROOT / 'test-runs').mkdir(exist_ok=True)
        moments_fixture.MomentsTests.setUp(self)

    async def test_single_generation_keeps_story_and_passes_the_selected_scene_to_image_job(self):
        self.generation_fixture({'participate': True, 'text': '给自己挑了个新配色，威严的事情下次再说',
            'media_mode': 'generate', 'image_prompt': '少女比较两张配色小卡片，最终举起柔和的一张，俯拍近景',
            'story_seed': 'original', 'story_hook': '想威严却偏爱柔和色', 'story_progress': '选定第一张卡片'})
        row = await self.service.generate('publish')
        self.assertEqual(row['state'], 'awaiting_image')
        self.assertEqual(len(self.model_inputs), 1)
        self.assertEqual(len(self.jobs), 1)
        data, options = self.model_inputs[0]
        self.assertEqual(len(data['story_brief']['directions']), 3)
        self.assertIn('不是亲历记忆', options['system_prompt'])
        self.assertEqual(self.jobs[0]['prompt'], json.loads(row['payload'])['image_prompt'])
        history = self.service.book.publications('account', 7)
        self.assertEqual(history[0]['story_progress'], '选定第一张卡片')
        self.assertFalse(self.calls)

    async def test_comment_reply_does_not_get_story_directions(self):
        self.generation_fixture({'participate': True, 'text': '对，就是这个配色'}, global_memory=False)
        row = await self.service.generate('reply_own', {
            'post': {'id': 'post', 'author': 'account', 'own': True, 'text': '一张配色练习'},
            'comment': {'id': 'comment', 'author': 'friend', 'text': '你说的是这个吗'}})
        self.assertEqual(row['state'], 'draft')
        data, options = self.model_inputs[0]
        self.assertNotIn('story_brief', data)
        self.assertNotIn('story_seed', options['system_prompt'])
        self.assertFalse(self.jobs)


class StoryImagePromptTests(unittest.IsolatedAsyncioTestCase):
    setUp = reference_fixture.MomentsReferenceTests.setUp
    tearDown = reference_fixture.MomentsReferenceTests.tearDown
    make_image = reference_fixture.MomentsReferenceTests.make_image

    def test_actual_image_prompt_gets_story_direction_for_moments_only(self):
        self.service.references.add(self.make_image(), 'girl', '少女图', '外观参考')
        for destination in ('moments', 'chat'):
            identity = self.service.enqueue(kind='generate', prompt='比较两张配色卡片的少女',
                destination=destination, chat='allowed' if destination == 'chat' else '',
                character=True, moment_id='post' if destination == 'moments' else '')
            plan = json.loads(self.service.get(identity)['reference_json'])
            refs = [{'name': item['name'], 'role': item['role'], 'source': 'character'} for item in plan['inputs']]
            prompt = self.service.references.request_prompt('比较两张配色卡片的少女', plan, refs)
            self.assertEqual(plan['destination'], destination)
            self.assertIn('比较两张配色卡片', prompt)
            self.assertEqual('【朋友圈画面叙事】' in prompt, destination == 'moments')
            self.assertEqual('剧情、动作、物件关系与构图按本次朋友圈要求重新创作' in prompt,
                             destination == 'moments')


if __name__ == '__main__':
    unittest.main()

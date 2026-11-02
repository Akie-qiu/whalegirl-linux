"""Unified model-owned plans and chronological initiative context; no live sends."""
import base64
from io import BytesIO
import json
import time
import unittest
from unittest.mock import AsyncMock

from PIL import Image
import test_chat_habits as habits
import test_proactive as proactive
from whalegirl_linux.conversation import pair_sticker, plan_actions
from whalegirl_linux.settings import Settings


class UnifiedReplyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue
    answer=habits.ChatHabitTests.answer

    async def test_text_choice_is_not_overridden_by_old_suggestion(self):
        self.queue(self.event())
        engine=await self.answer([{'type':'text','text':'我听着呢'}],sticker_suggestion='a'*32)
        self.assertEqual(self.gateway.sent,[('text','我听着呢')])
        self.assertEqual(engine.calls,1)

    async def test_sticker_can_be_the_entire_reply(self):
        self.queue(self.event())
        engine=await self.answer([{'type':'sticker','id':'a'*32}])
        self.assertEqual(self.gateway.sent,[('sticker','a'*32)])
        self.assertEqual(engine.calls,1)

    async def test_one_inference_preserves_longer_ordered_burst(self):
        self.config.save(self.config.settings.model_copy(update={'reply_max_parts':6,'reply_part_interval':.3}))
        self.queue(self.event())
        actions=[{'type':'text','text':'别光顾着熬夜'}, {'type':'text','text':'明天又喊困'},
                 {'type':'sticker','id':'a'*32}, {'type':'text','text':'先去歇会儿吧'}]
        engine=await self.answer(actions)
        self.assertEqual(self.gateway.sent,[('text','别光顾着熬夜'),('text','明天又喊困'),
                                            ('sticker','a'*32),('text','先去歇会儿吧')])
        self.assertEqual(engine.calls,1)

    def test_empty_plan_stays_empty_even_with_legacy_reply(self):
        settings=Settings(sticker_pair_probability=100)
        decision={'actions':[],'reply':'不应发送','sticker_suggestion':'a'*32}
        actions=plan_actions(decision,{'a'*32:'开心'},settings)
        self.assertEqual(pair_sticker(decision,actions,{'a'*32:'开心'},settings)[0],[])

    async def test_empty_final_plan_is_silence_instead_of_a_send_failure(self):
        row=self.event();self.queue(row)
        engine=await self.answer([],reply='不该被兼容层重新发送')
        self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.store.get(row['key'])['state'],'ignored')
        self.assertEqual(engine.calls,1)


class InitiativeContextTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=proactive.ProactiveTests.asyncSetUp
    asyncTearDown=proactive.ProactiveTests.asyncTearDown
    message=proactive.ProactiveTests.message
    configure=proactive.ProactiveTests.configure

    def capture(self,answer=None):
        calls=[]
        class Engine:
            last_error=''
            async def chat(self,messages,**options):
                calls.append((messages,options))
                return json.dumps(answer or {'should_reply':False,'actions':[]},ensure_ascii=False)
            async def close(self):pass
        self.runtime.engine_factory=lambda **kwargs:Engine()
        return calls

    async def test_full_recent_context_and_timing_replace_short_json_summaries(self):
        row={**self.message(2),'kind':49,'content':'引用作者说的话'+('长聊天资料'*150)+'末尾仍要保留的引用',
             'timestamp':time.time()-1900}
        self.store.observe([row]);calls=self.capture()
        self.assertTrue(await self.service.run_once())
        text=str(calls[0][0])
        self.assertIn('末尾仍要保留的引用',text)
        self.assertIn(row['key'],text)
        self.assertIn('账号ID=person',text)
        self.assertIn('last_human_age_seconds',text)
        self.assertNotIn('【本次需要回复的消息】',text)
        self.assertNotIn('recent_messages',text)
        self.assertIn('没有人刚向你提问',text)
        self.assertEqual(len(calls),1)

    async def test_cached_image_is_attached_and_bound_description_saved(self):
        # Proactive image reads cover recent photos; old messages remain text
        # context. An idle delay must not hide this recent-photo fixture.
        self.configure(proactive_idle_minutes=0)
        row={**self.message(2),'kind':3,'content':'[图片]','timestamp':time.time()-60}
        self.store.observe([row]);stream=BytesIO();Image.new('RGB',(32,32),'blue').save(stream,'PNG')
        self.store.cache_image(row['key'],{'data':base64.b64encode(stream.getvalue()).decode(),'format':'png'})
        calls=self.capture({'should_reply':False,'actions':[],
                            'image_descriptions':[{'key':row['key'],'description':'蓝色方块'}]})
        self.assertTrue(await self.service.run_once())
        content=calls[0][0][0]['content']
        self.assertIsInstance(content,list)
        self.assertEqual(sum(b['type']=='image_url' for b in content),1)
        self.assertIn(row['key'],str([b for b in content if b['type']=='text']))
        saved=self.store.find_message('account',proactive.CHAT,'2')
        self.assertEqual(saved['description'],'蓝色方块')

    async def test_missing_cached_image_does_not_open_or_scroll_wechat(self):
        row={**self.message(2),'kind':3,'content':'[图片]','timestamp':time.time()-1900}
        self.store.observe([row]);self.gateway.media=AsyncMock(side_effect=AssertionError('No UI navigation'))
        calls=self.capture();self.assertTrue(await self.service.run_once())
        self.gateway.media.assert_not_called()
        self.assertIn('不能视为已经看过',str(calls[0][0]))
        self.assertIsInstance(calls[0][0][0]['content'],str)

    async def test_changing_history_does_not_change_the_static_prompt(self):
        self.configure(sticker_usage_target=75)
        calls=self.capture();await self.service.run_once()
        self.store.observe([{**self.message(2),'content':'现在聊另外一件事','timestamp':time.time()-1900}])
        self.service.touch(proactive.CHAT,last_decision=0)
        await self.service.run_once()
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[0][1]['system_prompt'],calls[1][1]['system_prompt'])
        self.assertNotEqual(calls[0][0],calls[1][0])

    async def test_initiative_uses_the_same_frequency_reference_in_one_request(self):
        self.configure(sticker_usage_target=75,sticker_aggressiveness='very_high')
        self.runtime.stickers.entries={'a'*32:'开心'};self.runtime.stickers.available=['a'*32]
        calls=self.capture();self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),1)
        self.assertNotIn('期望约75%',str(calls[0][0]))
        self.assertIn('很愿意',calls[0][1]['system_prompt'])
        self.assertNotIn('【表情节奏参考】',calls[0][1]['system_prompt'])

    async def test_unanswered_opener_is_not_repeated_after_midnight(self):
        old=time.time()-3*86400
        self.store.db.execute('UPDATE message_log SET timestamp=?',(old,));self.store.db.commit()
        self.service.touch(proactive.CHAT,waiting_after=old+60)
        self.assertFalse(self.service.eligible(proactive.CHAT,time.time()))
        self.store.observe([{**self.message(2),'timestamp':time.time()-1900}])
        self.assertTrue(self.service.eligible(proactive.CHAT,time.time()))

    async def test_proactive_sticker_only_and_text_only_use_the_same_path(self):
        self.runtime.stickers.entries={'a'*32:'开心'};self.runtime.stickers.available=['a'*32]
        self.reply={'should_reply':True,'actions':[{'type':'sticker','id':'a'*32}]}
        self.assertTrue(await self.service.run_once())
        self.assertEqual(self.gateway.sent,[(proactive.CHAT,'a'*32)])


if __name__=='__main__':unittest.main()

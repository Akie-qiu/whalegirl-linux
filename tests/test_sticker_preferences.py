"""Manual expression controls and a curated note; no extra model calls."""
import json
import unittest
from whalegirl_linux.conversation import plan_actions,sticker_static_prompt
from whalegirl_linux.settings import Settings
from fastapi.testclient import TestClient
import test_chat_habits as habits


class StickerPreferenceTests(unittest.TestCase):
    def test_prompt_contains_independent_preferences_and_the_curated_note(self):
        s=Settings(sticker_text_pair_eagerness=95,sticker_only_eagerness=90,
                   sticker_reaction_eagerness=0,sticker_style_note='用表情接梗，不凑一句解释')
        prompt=sticker_static_prompt(s)
        for word in ['文字搭配表情 95/100','只发表情代替文字 90/100','接表情或拍一拍 0/100',
                     '不用另外提供sticker_suggestion','只有sticker是完整有效的回复','用表情接梗，不凑一句解释']:
            self.assertIn(word,prompt)
        self.assertEqual(sticker_static_prompt(s.model_copy(update={'stickers_enabled':False})),'')

    def test_per_reply_sticker_limit_preserves_order_and_avoids_duplicate_ids(self):
        choices={'a'*32:'开心','b'*32:'得意','c'*32:'害羞'}
        decision={'actions':[{'type':'sticker','id':'a'*32},{'type':'text','text':'接一句'},
                             {'type':'sticker','id':'a'*32},{'type':'sticker','id':'b'*32},
                             {'type':'sticker','id':'c'*32}]}
        actions=plan_actions(decision,choices,Settings(sticker_max_per_reply=2,reply_max_parts=6))
        self.assertEqual([(a['type'],a.get('id')) for a in actions],
                         [('sticker','a'*32),('text',None),('sticker','b'*32)])
        self.assertEqual(len(plan_actions(decision,choices,Settings(sticker_max_per_reply=3,reply_max_parts=2))),2)


class StickerPreferenceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_manual_note_is_in_the_single_main_request_and_plan_is_not_augmented(self):
        self.config.save(self.config.settings.model_copy(update={'sticker_style_note':'只发表情也是完整回应',
            'sticker_text_pair_eagerness':95,'sticker_only_eagerness':90,'sticker_reaction_eagerness':95}))
        row=self.event();self.queue(row);engine=await self.runtime.model();seen=[]
        async def chat(messages,**options):
            engine.calls+=1;seen.append(options['system_prompt'])
            return json.dumps({'should_reply':True,'actions':[{'type':'sticker','id':'a'*32}]})
        engine.chat=chat;await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertIn('只发表情也是完整回应',seen[0])
        self.assertEqual(self.gateway.sent,[('sticker','a'*32)])

    async def test_controls_and_note_save_together_without_changing_permissions(self):
        app=create_app(self.folder/'api',gateway=habits.Gateway(),run_background=False)
        c=app.state.config;c.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom']))
        values={'sticker_text_pair_eagerness':95,'sticker_only_eagerness':90,'sticker_reaction_eagerness':95,
                'sticker_max_per_reply':2,'sticker_style_note':'根据记录整理的表达参考'}
        with TestClient(app) as client:
            response=client.post('/api/proactive/config',headers={'Authorization':'Bearer '+c.admin_token},json={'values':values})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual({k:response.json()[k] for k in values},values)
            self.assertEqual(response.json()['allowed_chat_ids'],['group@chatroom'])
            self.assertFalse(response.json()['proactive_enabled'])


from whalegirl_linux.app import create_app
if __name__=='__main__':unittest.main()

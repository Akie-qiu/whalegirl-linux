"""Manual expression controls and a curated note; no extra model calls."""
import json
import unittest
from whalegirl_linux.conversation import plan_actions,sticker_static_prompt,sticker_usage_prompt
from whalegirl_linux.settings import Settings
from fastapi.testclient import TestClient
import test_chat_habits as habits


class StickerPreferenceTests(unittest.TestCase):
    def test_stronger_presets_keep_expression_model_owned(self):
        for level,phrase in [('very_high','默认先在目录里找'),('sticker_first','表情优先')]:
            prompt=sticker_static_prompt(Settings(sticker_aggressiveness=level))
            self.assertIn(phrase,prompt)
            self.assertIn('不为凑频率制造回复',prompt)
            self.assertIn('不用另外提供sticker_suggestion',prompt)

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

    def test_frequency_counts_turns_not_bubbles_and_never_mixes_chats(self):
        first=self.event();settings=self.config.settings.model_copy(update={'sticker_usage_target':75})
        def action(key,index,kind,state,chat='group@chatroom'):
            event={**first,'key':key,'chat':chat}
            identity=self.store.action_start(key,event,index,{'type':kind,'display':'验收'})
            self.store.action_finish(identity,state)
        action('turn-a',0,'text','sent');action('turn-a',1,'text','sent')
        action('turn-b',0,'text','sent');action('turn-b',1,'sticker','uncertain')
        action('turn-failed',0,'sticker','failed')
        action('other-chat',0,'sticker','sent','another@chatroom')
        stats={};prompt=sticker_usage_prompt(self.store,settings,first,stats)
        self.assertEqual(stats,{'target':75,'recent_turns':2,'sticker_turns':1})
        self.assertIn('近期偏少',prompt);self.assertIn('待确认的表情不重发',prompt)
        self.assertEqual(sticker_usage_prompt(self.store,settings.model_copy(update={'sticker_usage_target':0}),first),'')
        self.assertEqual(sticker_usage_prompt(self.store,settings.model_copy(update={'stickers_enabled':False}),first),'')

    async def test_frequency_reference_uses_one_request_and_does_not_force_a_sticker(self):
        self.config.save(self.config.settings.model_copy(update={'sticker_usage_target':75,'sticker_aggressiveness':'very_high'}))
        row=self.event();self.queue(row);engine=await self.runtime.model();seen=[]
        async def chat(messages,**options):
            engine.calls+=1;seen.append(options['system_prompt'])
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'我听着呢'}]})
        engine.chat=chat;await self.runtime.process_next()
        self.assertEqual(engine.calls,1);self.assertEqual(self.gateway.sent,[('text','我听着呢')])
        self.assertIn('期望约75%',seen[0])
        self.assertGreater(seen[0].index('【表情节奏参考】'),seen[0].index('【本轮动态上下文】'))

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
                'sticker_max_per_reply':2,'sticker_style_note':'根据记录整理的表达参考',
                'sticker_usage_target':75,'sticker_aggressiveness':'very_high'}
        with TestClient(app) as client:
            response=client.post('/api/proactive/config',headers={'Authorization':'Bearer '+c.admin_token},json={'values':values})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual({k:response.json()[k] for k in values},values)
            self.assertEqual(response.json()['allowed_chat_ids'],['group@chatroom'])
            self.assertFalse(response.json()['proactive_enabled'])
            invalid=client.post('/api/proactive/config',headers={'Authorization':'Bearer '+c.admin_token},json={'values':{'sticker_usage_target':101}})
            self.assertEqual(invalid.status_code,400)

    async def test_incoming_sticker_treated_as_auxiliary_mood_and_selectively_replied(self):
        self.config.save(self.config.settings.model_copy(update={
            'sticker_aggressiveness': 'normal',
            'sticker_usage_target': 25,
        }))
        self.runtime.stickers.entries['b' * 32] = '开心，轻快雀跃'
        self.runtime.stickers.profiles['b' * 32] = {'emotion': '开心', 'tone': '轻快', 'intensity': '低'}
        self.runtime.stickers.available = ['b' * 32]
        self.store.observe([
            self.event(mid=10, content='今天账怎么对不上呀'),
        ])
        sticker_row = {
            **self.event(
                mid=11,
                content='[原生表情包]（辅助情绪：开心；轻快；低）',
            ),
            'kind': 47,
            'payload_json': json.dumps({'sticker': {'md5': 'b' * 32}}, ensure_ascii=False),
        }
        self.queue(sticker_row)
        engine = await self.runtime.model()
        seen = []
        async def chat(messages, **options):
            engine.calls += 1
            seen.append(options['system_prompt'])
            return json.dumps({
                'should_reply': True,
                'actions': [{'type': 'text', 'text': '说不定剩下那截账还在路上呢'}],
            })
        engine.chat = chat
        await self.runtime.process_next()
        self.assertEqual(engine.calls, 1)
        self.assertIn('【表情只作情感辅助·不作聊天主体】', seen[0])
        self.assertIn('并非收到表情就不回', seen[0])
        self.assertIn('在没有着重提到的情况下不作为聊天主体', seen[0])
        self.assertEqual(self.gateway.sent, [('text', '说不定剩下那截账还在路上呢')])


from whalegirl_linux.app import create_app
if __name__=='__main__':unittest.main()

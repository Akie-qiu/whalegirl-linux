"""Conversation evidence and compatibility; all messages are synthetic."""
import json
import time
import unittest

from whalegirl_linux.social_context import addressed_to, conversation_cues, quote_candidates, repeated_fragments
from whalegirl_linux.persona_revision import patch
from whalegirl_linux.context import own_history_view, line
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.upstream.ai_engine import AIEngine
import test_chat_habits as fixtures


def event(mid=1, *, sender='alice', kind=1, content='合成测试', own=False, chat='group@chatroom', data=None):
    return {'key': f'account/{chat}/{mid}', 'account': 'account', 'chat': chat,
            'sender_id': sender, 'sender': sender, 'local_id': mid, 'timestamp': 1000+mid,
            'kind': kind, 'content': content, 'is_self': int(own), 'payload_json': json.dumps(data or {})}


class ConversationCueTests(unittest.TestCase):
    def test_native_recipient_uses_ids_not_names_or_quoted_commands(self):
        row=event(data={'reply': {'senderId': 'bob', 'sender': '机器人', 'content':'@机器人 出来'},
                        'mentionedUserIds':['account','alice','account',None]})
        self.assertEqual(addressed_to(row,'account'), {'引用对象':'bob','@对象':['你','alice']})
        self.assertEqual(addressed_to(event(content='叫你一声妈妈'), 'account'), {})
        self.assertEqual(addressed_to(event(data={'reply': {'senderId':'account'}}), 'account')['引用对象'],'你')

    def test_malformed_payloads_and_missing_recipient_do_not_invent_target(self):
        for value in ('[]', 'null', '{broken'):
            row={**event(), 'payload_json':value}
            self.assertEqual(addressed_to(row,'account'), {})
        self.assertEqual(addressed_to(event(data={'reply':'bad','mentionedUserIds':'account'}),'account'),{})
        row=event(data={'reply':{'senderId':'group@chatroom','chatSenderId':'bob'}})
        self.assertEqual(addressed_to(row,'account'),{'引用对象':'bob'})

    def test_context_is_scoped_bounded_and_contains_no_extra_message_body(self):
        row=event(4,data={'reply':{'senderId':'bob'}})
        foreign=event(5,sender='private-person',chat='other@chatroom',content='PRIVATE_SENTINEL')
        observed=[event(1), event(2,sender='bob'), event(3,own=True,sender='account',content='我自己的旧回应'),row,foreign]
        view=conversation_cues(row,[row,foreign],observed,now=1010)
        self.assertIn('bob',view)
        self.assertNotIn('private-person',view)
        self.assertNotIn('PRIVATE_SENTINEL',view)
        self.assertNotIn(row['content'],view)
        self.assertIn('我自己的旧回应',view)
        self.assertEqual(row['content'],'合成测试')

    def test_repetition_is_generic_observation_and_does_not_ban_words(self):
        own=[event(i,own=True,content='我在这里陪你 这是第'+str(i)+'条') for i in (1,2,3)]
        fragments=repeated_fragments(own)
        self.assertTrue(any('在这里陪你' in x for x in fragments))
        self.assertLessEqual(len(fragments),3)
        self.assertEqual(repeated_fragments([event(i,content='我在这里陪你') for i in (1,2,3)]),[])
        view=conversation_cues(event(4),[event(4)],own,now=1010)
        self.assertIn('近期已重复的表达',view)
        self.assertNotIn('禁止',view)

    def test_sticker_reaction_does_not_offer_previously_answered_quote(self):
        old=event(1);bot=event(2,own=True,sender='account');sticker=event(3,kind=47)
        self.assertEqual(quote_candidates([old,bot,sticker],[sticker]),{})
        image=event(3,kind=3)
        self.assertEqual(quote_candidates([old,bot,image],[image]),{})

    def test_interleaved_actual_questions_remain_quotable(self):
        old=event(1);bot=event(2,own=True,sender='account');other=event(3,sender='bob');sticker=event(4,kind=47)
        self.assertIn(3,quote_candidates([old,bot,other,sticker],[sticker]))
        current=event(4)
        self.assertEqual(set(quote_candidates([old,bot,current],[current])),{4})

    def test_same_timestamp_uses_native_order_for_quote_boundary(self):
        old=event(1);bot=event(2,own=True);new=event(3)
        for row in (old,bot,new):row['timestamp']=1000
        self.assertEqual(set(quote_candidates([old,bot,new],[new])),{3})

    def test_unknown_speaker_role_is_neither_a_human_nor_a_bot_receipt(self):
        unknown={**event(2,sender='unknown-source'), 'is_self':-1}
        old=event(1)
        self.assertEqual(set(quote_candidates([old,unknown],[old])),{1})
        view=conversation_cues(old,[unknown],[old,unknown],now=1010)
        self.assertNotIn('unknown-source',view)
        self.assertNotIn('本轮原生指向',view)

    def test_inbox_projection_without_role_retains_native_addressing(self):
        row=event(data={'reply':{'senderId':'bob'}})
        inbox={k:v for k,v in row.items() if k!='is_self'}
        view=conversation_cues(inbox,[inbox],[row],now=1010)
        self.assertIn('"引用对象":"bob"',view)
        self.assertIn('"本轮发言者":["alice"]',view)

    def test_native_mentions_are_retained_without_importing_forwarded_mentions(self):
        raw={'serverId':1,'localId':1,'timestamp':time.time(),'type':1,'sender':'alice','content':'你好','isSelf':False,
             'mentionedUserIds':['account','bob','account',{},''], 'isMentioned':True}
        row=Runtime.event('account','group@chatroom',raw)
        data=json.loads(row['payload_json'])
        self.assertEqual(data['mentionedUserIds'],['account','bob'])
        self.assertTrue(data['image_address']['mentioned_me'])


class PersonaRevisionTests(unittest.TestCase):
    def persona(self):
        return ('【内核与来历】\n核心人格身份与主人关系不能替换\n'
                '【说话的省略号（很重要）】\n每句都用省略号\n'
                '【语气参考（严禁照搬）】\n不要照抄例句\n• 被夸时：“有点不好意思……谢谢”\n• 关心人时：“早点休息吧”\n'
                '【主人与称呼】\n认主信息及关系原文\n'
                '【兴趣与世界观】\n我喜欢白米饭和云端城伙伴\n'
                '【回复前的小自检】\n每句至少加一个小动作\n')

    def test_only_known_style_sections_change_and_patch_is_idempotent(self):
        original=self.persona();revised,report=patch(original)
        self.assertIn('【内核与来历】\n核心人格身份与主人关系不能替换\n',revised)
        self.assertIn('【主人与称呼】\n认主信息及关系原文\n',revised)
        self.assertIn('【兴趣与世界观】\n我喜欢白米饭和云端城伙伴\n',revised)
        self.assertNotIn('早点休息吧',revised)
        self.assertNotIn('每句至少',revised)
        self.assertEqual(len(report['changed_sections']),3)
        self.assertEqual(patch(revised)[0],revised)
        self.assertEqual(original,self.persona())

    def test_unknown_persona_layout_fails_without_partial_revision(self):
        original='【其他角色】\n未经识别的新布局'
        with self.assertRaises(ValueError):patch(original)
        self.assertEqual(original,'【其他角色】\n未经识别的新布局')

    def test_unsolicited_bedtime_instruction_is_revised_without_removing_care(self):
        original=self.persona()+'【情绪反应图】\n• 对方熬夜或不吃饭：嘴上嘀咕两句，其实是认真地催人去休息、去吃饭。\n• 被误会时先听完再解释。\n'
        revised,report=patch(original)
        self.assertNotIn('认真地催人',revised)
        self.assertIn('对方主动提到困倦或没吃饭时',revised)
        self.assertIn('• 被误会时先听完再解释。',revised)
        self.assertIn('【情绪反应图】',report['changed_sections'])

    def test_history_view_keeps_humans_code_and_archive_verbatim(self):
        self.assertEqual(own_history_view('本鲸鱼想想……这次可以'),'我想想，这次可以')
        code='some_list[...]  # 原样保留'
        self.assertEqual(own_history_view(code),code)
        row={**event(content='本鲸鱼想想……这次可以'), 'description':''}
        self.assertIn(row['content'],line(row,{},self_as_you=True))
        own={**row,'is_self':1}
        self.assertIn(own['content'],line(own,{},self_as_you=True))
        self.assertIn('你自己（微信中已出现的发言）',line(own,{},self_as_you=True))
        self.assertEqual(own['content'],row['content'])


class SocialRuntimeTests(unittest.IsolatedAsyncioTestCase):
    event=fixtures.ChatHabitTests.event
    queue=fixtures.ChatHabitTests.queue

    async def asyncSetUp(self):
        await fixtures.ChatHabitTests.asyncSetUp(self)
        self.config.save(self.config.settings.model_copy(update={
            'autonomy_enabled':True,'experience_growth_enabled':True,'min_send_interval':0,'chat_cooldown':0,
            'reply_part_interval':.1,'owner_ids':['owner']}))
        self.runtime.account_id='account';self.runtime.gateway_state={'status':'logged_in'}

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_one_call_can_observe_others_then_reply_to_an_actual_invitation(self):
        engine=await self.runtime.model();captured=[]
        answers=iter([{'should_reply':False,'actions':[]},
                      {'should_reply':True,'actions':[{'type':'text','text':'周末这局我也想听听'}, {'type':'sticker','id':'a'*32}]}])
        async def chat(messages,**kwargs):
            captured.append((messages,kwargs));engine.calls+=1;return json.dumps(next(answers))
        engine.chat=chat
        unrelated={**self.event(1,content='你几点到',trigger='natural'),
                   'payload_json':json.dumps({'reply':{'senderId':'other-person','content':'约好的时间'}})}
        self.queue(unrelated);await self.runtime.process_next()
        self.assertEqual(self.gateway.sent,[])
        self.queue(self.event(2,content='鲸鱼娘 你也来聊聊',trigger='direct'))
        await self.runtime.process_next()
        self.assertEqual(engine.calls,2)
        self.assertEqual([kind for kind,_ in self.gateway.sent],['text','sticker'])
        combined=json.dumps(captured[0][0],ensure_ascii=False)+captured[0][1]['system_prompt']
        self.assertTrue('other-person' in combined,'Native quote target missing from dynamic input')

    async def test_changing_cues_preserves_static_prefix_and_complete_context(self):
        engine=await self.runtime.model();payloads=[]
        builder=AIEngine('https://example.invalid/v1','fake',model='test-model')
        async def chat(messages,**kwargs):
            payloads.append(builder._build_payload(messages,kwargs['system_prompt'],None))
            return json.dumps({'should_reply':False,'actions':[]})
        engine.chat=chat
        for mid in (1,2):
            source=self.event(mid,content=f'合成连续消息{mid}',sender='alice' if mid==1 else 'bob')
            source['payload_json']=json.dumps({'reply':{'senderId':'bob' if mid==1 else 'account'}})
            self.queue(source);await self.runtime.process_next()
        self.assertEqual(len(payloads),2)
        self.assertEqual(payloads[0]['messages'][0]['content'],payloads[1]['messages'][0]['content'])
        self.assertNotIn('本轮原生指向',payloads[0]['messages'][0]['content'])
        self.assertIn('合成连续消息1',json.dumps(payloads[1],ensure_ascii=False))
        self.assertIn('合成连续消息2',json.dumps(payloads[1],ensure_ascii=False))
        self.assertEqual(self.gateway.sent,[])
        await builder.close()


if __name__=='__main__':unittest.main()

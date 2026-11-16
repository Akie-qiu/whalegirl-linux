"""Actual subtitle sequences, auditable speech learning and private facts isolation."""
import json
import unittest

import test_chat_habits as fixtures
import test_internet_growth as growth_fixtures
from whalegirl_linux.expression_reference import reaction_evidence
from whalegirl_linux.growth import GrowthService


CONTEXT='这个手柄买来三个月，我居然还没拆封'
REACTION='盒子都比你玩得久'


def material():
    return {'subtitles':[{'start':2,'end':5,'text':CONTEXT},
                         {'start':5.5,'end':7,'text':REACTION}],
            'comments':[], 'danmaku':[], 'scenes':[]}


def offered(body):
    return GrowthService.source_excerpt({'kind':'bilibili','body':json.dumps(body,ensure_ascii=False)})


def reaction(**updates):
    return {'reaction_kind':'subtitle','context_line_index':0,'reaction_line_index':1,
            'context_evidence':CONTEXT,'reaction_evidence':REACTION,**updates}


class SubtitleEvidenceTests(unittest.TestCase):
    def test_sequential_words_and_actual_timestamps_are_verified_without_inventing_speakers(self):
        verified=reaction_evidence(reaction(),{'id':'video-source','kind':'bilibili'},offered(material()))
        self.assertEqual(verified['scope'],'subtitle_sequence')
        self.assertEqual(verified['range'],[2,7])
        self.assertEqual(verified['time'],5.5)
        self.assertEqual(verified['speaker_verification'],'unverified')

    def test_short_utterances_and_json_escaped_quotes_are_valid_source_text(self):
        for left,right in [('好累','歇会儿'),('我说"最后一把"，再玩一会儿','你这最后一把可真长')]:
            body=material();body['subtitles'][0]['text']=left;body['subtitles'][1]['text']=right
            verified=reaction_evidence(reaction(context_evidence=left,reaction_evidence=right),
                {'id':'video-source','kind':'bilibili'},offered(body))
            self.assertIsNotNone(verified)

    def test_unseen_words_skipped_lines_reversed_order_and_large_time_gaps_are_rejected(self):
        source={'id':'video-source','kind':'bilibili'};raw=offered(material())
        for change in ({'reaction_evidence':'模型现编的台词'}, {'reaction_line_index':20},
                       {'context_line_index':1,'reaction_line_index':0}, {'reaction_line_index':True}):
            with self.subTest(change=change):self.assertIsNone(reaction_evidence(reaction(**change),source,raw))
        for start in (45,1):
            body=material();body['subtitles'][1].update(start=start,end=start+2)
            self.assertIsNone(reaction_evidence(reaction(),source,offered(body)))

    def test_uniform_sampling_cannot_fake_a_neighbouring_reply(self):
        body=material();body['subtitles'].insert(1,{'start':5,'end':5.4,'text':'中间还有一句没提供'})
        parsed=json.loads(offered(body));parsed['subtitles'].pop(1)
        self.assertIsNone(reaction_evidence(reaction(reaction_line_index=2),
            {'id':'video-source','kind':'bilibili'},json.dumps(parsed,ensure_ascii=False)))

    def test_bounded_excerpt_keeps_small_continuous_sequences_and_valid_json(self):
        body=material();body['subtitles']=[{'start':i*3,'end':i*3+2,'text':f'真实第{i}句：'+CONTEXT}
                                         for i in range(120)]
        source={'kind':'bilibili','body':json.dumps(body,ensure_ascii=False)}
        for cap in (18000,2200,300,128):
            raw=GrowthService.source_excerpt(source,cap=cap);data=json.loads(raw)
            self.assertLessEqual(len(raw),cap)
            rows=data.get('subtitles',[])
            self.assertTrue(all(row['text'] in body['subtitles'][row['line_index']]['text'] for row in rows))
            if cap>=2200:self.assertTrue(any(b['line_index']==a['line_index']+1 for a,b in zip(rows,rows[1:])))

    def test_comment_excerpt_keeps_parent_and_reply_as_a_unit(self):
        body={'subtitles':[],'scenes':[],'danmaku':[],
            'comments':[{'id':'root','parent_id':'','text':'父楼提出一个具体问题'},
                        {'id':'reply','parent_id':'root','text':'楼中楼接住父楼的问题'},
                        {'id':'other','parent_id':'','text':'另一个全视频讨论'}]}
        rows=json.loads(offered(body))['comments']
        pair=[row for row in rows if row.get('id') in ('root','reply')]
        self.assertEqual([row['id'] for row in pair],['root','reply'])


class VideoSpeechIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown

    async def learn(self,body=None,**event_changes):
        self.config.save(self.config.settings.model_copy(update={'bilibili_enabled':True,'growth_auto_apply':True,
            'growth_bilibili_expression_shared':True,'growth_internet_model_profile':'current'}))
        g=self.runtime.growth;role=self.runtime.memory_hub.role()
        source=g.save_source({'kind':'bilibili','url':'https://www.bilibili.com/video/BV1234567890/',
            'title':'来源作者 PRIVATE_VIDEO_FACT','content':json.dumps(material() if body is None else body,ensure_ascii=False)},role)
        calls=[]
        class Engine:
            last_error=''
            async def chat(inner,messages,**options):
                calls.append((json.loads(messages[0]['content']),options))
                return json.dumps({'encounter':'读取了实际提供的字幕片段',
                    'speech_summary':'轻松自嘲时，对方顺着物品闲置的反差说一句，笑点成立后不用再解释。',
                    'reaction_events':[{**reaction(**event_changes),'source_id':source['id'],
                        'text':'轻松自嘲东西闲置时，短句接住反差，不补充说教',
                        'scene':'轻松聊天中的荒诞反转','emotion':'好笑','intent':'简短调侃',
                        'examples':['它已经开始在盒子里养老了'],
                        'conditions':'轻松自嘲时','counterexamples':'对方认真难过或求助时不适用'}]},ensure_ascii=False)
        result=await g.reflect_sources(Engine(),self.config.generation,role,[source],'自嘲与接话')
        return g,source,calls,result

    async def test_subtitle_learning_uses_one_call_saves_summary_and_only_shares_general_expression(self):
        g,source,calls,result=await self.learn()
        self.assertEqual(len(calls),1)
        self.assertEqual(result['learned'],1)
        journal=g.journal(result['journal_id'])
        self.assertEqual(journal['body']['speech_summary'],'轻松自嘲东西闲置时，短句接住反差，不补充说教')
        self.assertIn('笑点成立后',journal['body']['expression_summary'])
        self.assertEqual(result['spoken_expressions'],1)
        self.assertIn('PRIVATE_VIDEO_FACT',journal['sources'][0]['title'])
        row=g.memories(chat=True)[0]
        receipt=json.loads(self.store.db.execute('SELECT receipt FROM memory_evidence WHERE memory_id=?',(row['id'],)).fetchone()[0])
        self.assertEqual(receipt['reaction_event']['range'],[2,7])
        shared=g.prompt({'account':'account','chat':'group@chatroom'},[{'content':'笑死，我的新相机也没拆封'}])
        self.assertIn('盒子里养老',shared)
        for forbidden in (CONTEXT,REACTION,'PRIVATE_VIDEO_FACT','bilibili.com','speech_summary'):
            self.assertNotIn(forbidden,shared)
        coverage=g.source(source['id'])['coverage']['reflection_input']
        self.assertEqual(coverage['spoken_basis'],'subtitle_text')
        self.assertEqual(coverage['speaker_verification'],'unverified')
        self.assertIn('reaction_kind=subtitle',calls[0][1]['system_prompt'])

    async def test_shared_expression_remains_subject_to_source_and_individual_controls(self):
        g,source,calls,result=await self.learn();row=g.memories(chat=True)[0]
        self.config.save(self.config.settings.model_copy(update={'growth_bilibili_expression_shared':False}))
        self.assertFalse(g.chat_allowed(row))
        self.config.save(self.config.settings.model_copy(update={'growth_bilibili_expression_shared':True}))
        self.assertTrue(g.chat_allowed(row))
        changed=g.edit(row['id'],{'version':row['version'],'chat_enabled':False})
        self.assertFalse(g.chat_allowed(changed))
        self.assertTrue(g.journal(result['journal_id'])['body']['speech_summary'])

    async def test_without_subtitles_comments_cannot_become_a_video_speech_summary(self):
        g,source,calls,result=await self.learn({'subtitles':[],'comments':[{'id':'1','text':CONTEXT+REACTION}],
                                             'danmaku':[],'scenes':[]})
        self.assertEqual(len(calls),1)
        self.assertEqual(result['learned'],0)
        body=g.journal(result['journal_id'])['body']
        self.assertEqual(body['speech_summary'],'')
        self.assertIn('未形成视频口语总结',body['missing'])

    async def test_having_subtitles_does_not_turn_a_comment_into_a_spoken_learning_result(self):
        body=material();body['comments']=[{'id':'root','text':CONTEXT,'parent_id':''},
            {'id':'reply','text':REACTION,'parent_id':'root'}]
        g,source,calls,result=await self.learn(body,reaction_kind='comment',reaction_id='reply')
        self.assertEqual(result['learned'],1)
        self.assertEqual(result['spoken_expressions'],0)
        journal=g.journal(result['journal_id'])['body']
        self.assertEqual(journal['speech_summary'],'')
        self.assertTrue(journal['expression_summary'])
        self.assertIn('讨论表达另计',journal['missing'])

    async def test_standalone_comment_is_not_saved_as_interpersonal_expression_evidence(self):
        body=material();body['comments']=[{'id':'reply','text':REACTION,'parent_id':''}]
        g,source,calls,result=await self.learn(body,reaction_kind='comment',reaction_id='reply')
        self.assertEqual(result['learned'],0)
        self.assertEqual(g.memories(chat=True),[])

    async def test_style_card_is_saved_after_three_verified_interactions_without_an_extra_call(self):
        self.config.save(self.config.settings.model_copy(update={'bilibili_enabled':True,'growth_auto_apply':True,
            'growth_bilibili_expression_shared':True,'growth_internet_model_profile':'current'}))
        g=self.runtime.growth;role=self.runtime.memory_hub.role()
        helper=growth_fixtures.InternetGrowthTests();helper.config=self.config;helper.runtime=self.runtime;helper.store=self.store
        for _ in range(3):helper.insight('bilibili','expression')
        # Seed three existing, permissioned evidence records as if earlier
        # reflection calls had independently verified parent/reply pairs.
        for memory in g.memories(include_inactive=True)[:3]:
            event={'scope':'reply_thread','reaction_kind':'comment','context':'父楼内容'+str(memory['id']),
                'reaction':'回复内容'+str(memory['id'])}
            self.store.db.execute('UPDATE memory_evidence SET receipt=? WHERE memory_id=?',
                (json.dumps({'reaction_event':event},ensure_ascii=False),memory['id']))
        source=g.save_source({'kind':'bilibili','url':'https://www.bilibili.com/video/BV1234567890/',
            'title':'合成测试材料','content':json.dumps(material(),ensure_ascii=False)},role)
        calls=[]
        class Engine:
            last_error=''
            async def chat(inner,*args,**kwargs):
                calls.append(1)
                return json.dumps({'encounter':'读取合成测试材料','insights':[],'reaction_events':[],
                    'style_cards':[{'source_kind':'bilibili','summary':'轻松聊天的简短接话规律',
                        'patterns':[{'situation':'playful','rhythm':'短句先接住反差',
                            'conversation_moves':['顺着刚才的话回应'],'emotion_response':'共享轻松感，不解释笑点',
                            'avoid_when':['distress','technical']}]}]},ensure_ascii=False)
        result=await g.reflect_sources(Engine(),self.config.generation,role,[source],'表达习惯卡测试',bilibili={'coverage':{}})
        self.assertEqual(len(calls),1)
        self.assertEqual(len(g.style_cards()),1)
        self.assertEqual(g.style_cards()[0]['evidence_count'],3)

    async def test_quote_escaped_utterance_can_be_saved_after_structured_evidence_verification(self):
        body=material();body['subtitles'][1]['text']='你这"最后一把"可真长'
        g,source,calls,result=await self.learn(body,reaction_evidence=body['subtitles'][1]['text'])
        self.assertEqual(result['learned'],1)
        row=g.memories(chat=True)[0]
        excerpt=self.store.db.execute('SELECT excerpt FROM memory_evidence WHERE memory_id=?',(row['id'],)).fetchone()[0]
        self.assertEqual(excerpt,body['subtitles'][1]['text'])


if __name__=='__main__':unittest.main()

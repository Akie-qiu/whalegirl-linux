"""Synthetic held-out situations exercise reference choice, not canned replies."""
import json
import unittest

from whalegirl_linux.expression_reference import reference_score,select_examples,reaction_evidence


class ReferenceTests(unittest.TestCase):
    def test_distress_never_loads_a_comic_reference_from_another_context(self):
        pool=[{'key':'joke','text':'你这操作真是离谱','context':'抽象反转，笑死了'},
              {'key':'care','text':'先歇一会儿嘛','context':'刚忙完有点累，好难过'}]
        selected=select_examples(pool,'今天考砸了，有点难过')
        self.assertEqual([row['key'] for row in selected],['care'])
        self.assertEqual(reference_score('认真的，日志报错怎么办','轻松接梗吐槽反转'),0)

    def test_unrelated_greeting_does_not_force_a_corpus_example(self):
        pool=[{'key':'game','text':'再来一局嘛','context':'这游戏终于打过了'}]
        self.assertEqual(select_examples(pool,'早上好呀'),[])

    def test_counterexample_veto_and_unknown_context_can_select_nothing(self):
        self.assertEqual(reference_score('我很难过','用短句惊讶接梗',counterexamples='对方难过或认真求助时不适用'),0)
        self.assertEqual(reference_score('今天的窗帘是蓝色的','轻松荒诞反转'),0)

    def test_global_comments_cannot_be_falsely_timestamped(self):
        context='演奏者认真弹完一小段旋律'
        material={'subtitles':[{'start':0,'end':5,'text':context}],
                  'comments':[{'id':'c1','text':'好耶','parent_id':''}],
                  'danmaku':[{'time':90,'text':'好耶'}]}
        raw=json.dumps(material,ensure_ascii=False)
        event={'context_evidence':context,'reaction_evidence':'好耶','reaction_kind':'comment','reaction_id':'c1','reaction_time':2}
        self.assertIsNone(reaction_evidence(event,{'id':'source','kind':'bilibili'},raw))
        material['comments']=[{'id':'root','text':context,'parent_id':''},{'id':'c1','text':'好耶','parent_id':'root'}]
        verified=reaction_evidence(event,{'id':'source','kind':'bilibili'},json.dumps(material,ensure_ascii=False))
        self.assertEqual(verified['scope'],'reply_thread')
        self.assertNotIn('time',verified)
        event.update(reaction_kind='danmaku',reaction_time=90)
        self.assertIsNone(reaction_evidence(event,{'id':'source','kind':'bilibili'},raw))
        material['danmaku'][0]['time']=3;event['reaction_time']=3
        verified=reaction_evidence(event,{'id':'source','kind':'bilibili'},json.dumps(material,ensure_ascii=False))
        self.assertEqual(verified['scope'],'timed_window')

    def test_style_reference_requires_a_shared_social_situation_signal(self):
        self.assertEqual(reference_score('早上好呀','轻松聊天中的荒诞反转'),0)
        self.assertGreater(reference_score('笑死，这也太离谱了','轻松聊天中的荒诞反转'),0)

    def test_missing_or_fabricated_short_reaction_is_rejected(self):
        raw='演奏者认真弹完一小段旋律。观众说：好耶。'
        source={'id':'source','kind':'tieba'}
        event={'context_evidence':'演奏者认真弹完一小段旋律','reaction_evidence':'好耶'}
        self.assertTrue(reaction_evidence(event,source,raw))
        event['reaction_evidence']='神了'
        self.assertIsNone(reaction_evidence(event,source,raw))

    def test_nested_reply_must_match_its_actual_immediate_parent(self):
        material={'subtitles':[],'scenes':[],'danmaku':[],'comments':[
            {'id':'root','parent_id':'','root_id':'','text':'父楼内容'},
            {'id':'reply','parent_id':'root','root_id':'root','text':'第一层回复内容'},
            {'id':'nested','parent_id':'reply','root_id':'root','text':'第二层接话内容'}]}
        source={'id':'source','kind':'bilibili'};submitted=json.dumps(material,ensure_ascii=False)
        event={'reaction_kind':'comment','reaction_id':'nested','context_evidence':'第一层回复内容',
            'reaction_evidence':'第二层接话内容'}
        self.assertEqual(reaction_evidence(event,source,submitted)['parent_id'],'reply')
        event['context_evidence']='父楼内容'
        self.assertIsNone(reaction_evidence(event,source,submitted))

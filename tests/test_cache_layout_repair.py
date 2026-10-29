"""Cache layout preserves content, pictures, protocols and request count."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock,patch
import test_chat_habits as habits
from whalegirl_linux.upstream.ai_engine import AIEngine
from whalegirl_linux.memory import consolidation_data


class CacheLayoutTests(unittest.TestCase):
    def test_dynamic_data_never_changes_system_for_old_new_and_missing_headings(self):
        headings=['\n【本次需要回复的消息】\n','\n【当前需要回复的消息｜本轮主题优先】\n','\n']
        for protocol in ('openai','responses','anthropic'):
            for heading in headings:
                for vision in (False,True):
                    with self.subTest(protocol=protocol,heading=heading,vision=vision):
                        text='有时间与人物ID的历史'+heading+'现在问的事'
                        content=[{'type':'text','text':text},{'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}] if vision else text
                        messages=[{'role':'user','content':content},{'role':'assistant','content':'已有工具请求'}]
                        original=copy.deepcopy(messages)
                        engine=AIEngine('https://example.test/v1','fake',api_style=protocol)
                        with patch('whalegirl_linux.time_context.time.time',return_value=1793250000):
                            a=engine._build_payload(messages,'固定规则\n【本轮动态上下文】动态资料甲',None)
                        with patch('whalegirl_linux.time_context.time.time',return_value=1793250060):
                            b=engine._build_payload(messages,'固定规则\n【本轮动态上下文】动态资料乙',None)
                        system=lambda p:p['messages'][0]['content'] if protocol=='openai' else p['instructions' if protocol=='responses' else 'system']
                        self.assertEqual(system(a),system(b));self.assertNotIn('动态资料',system(a))
                        raw=json.dumps(a,ensure_ascii=False)
                        for value in ['有时间与人物ID的历史','现在问的事','动态资料甲','已有工具请求']:self.assertIn(value,raw)
                        self.assertEqual(raw.count('【服务器当前时间】'),1)
                        if vision:self.assertIn('data:image/png;base64,AA==',raw)
                        self.assertEqual(messages,original)

    def test_no_user_message_does_not_drop_dynamic_data(self):
        engine=AIEngine('https://example.test/v1','fake',api_style='openai')
        payload=engine._build_payload([{'role':'assistant','content':'前文'}],'规则\n【本轮动态上下文】保留资料',None)
        self.assertIn('保留资料',payload['messages'][0]['content'])

    def test_live_clock_does_not_split_reusable_image_input(self):
        from whalegirl_linux.time_context import clock_messages
        source=[{'role':'user','content':[{'type':'text','text':'识图问题'},
                 {'type':'image_url','image_url':{'url':'data:image/png;base64,AA=='}}]}]
        with patch('whalegirl_linux.time_context.time.time',return_value=1793250000):a=clock_messages(source)
        with patch('whalegirl_linux.time_context.time.time',return_value=1793250060):b=clock_messages(source)
        self.assertEqual(a[0]['content'][:-1],b[0]['content'][:-1])
        self.assertIn('【服务器当前时间】',a[0]['content'][-1]['text'])
        self.assertEqual(len(source[0]['content']),2)

    def test_consolidation_reorders_without_losing_any_selected_information(self):
        rows=[{'id':2,'version':1,'content':'喜欢茶','subject':'a','chat':'g','kind':'preference','scope':'profile','triggers':'茶','salience':3},
              {'id':1,'version':4,'content':'记忆事实','subject':'b','chat':'g','kind':'fact','scope':'chat','triggers':'','salience':5}]
        messages=[{'text':'本批原始消息','key':'source'}];original=copy.deepcopy((rows,messages))
        a=consolidation_data('原来的摘要',messages,rows)
        b=consolidation_data('更新后的摘要',[{'text':'下一批消息'}],list(reversed(rows)))
        self.assertEqual(list(a),['existing_memories','messages','previous_summary'])
        self.assertEqual(a['existing_memories'],b['existing_memories'])
        self.assertEqual(a['existing_memories'],sorted(rows,key=lambda r:r['id']))
        self.assertEqual(a['messages'],messages);self.assertEqual(a['previous_summary'],'原来的摘要')
        self.assertEqual((rows,messages),original)

    def test_usage_tags_only_change_local_metadata_keywords(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'))
        from cache_operation_tags import tag_chat_calls
        source="async def digest(engine):\n    return await engine.chat([],system_prompt='资料')\nasync def normal(engine):\n    return await engine.chat([])\n"
        tagged,count=tag_chat_calls(source,'memory',function='digest')
        self.assertEqual(count,1);self.assertIn("system_prompt='资料', operation='memory'",tagged)
        self.assertIn('return await engine.chat([])',tagged)
        self.assertEqual(tag_chat_calls(tagged,'memory',function='digest'),(tagged,0))


class CommonPrefixRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    async def test_gallery_changes_do_not_break_shared_format_and_wait_rules(self):
        entry={'id':'asset-a','name':'甲图','source':'uploaded'}
        self.runtime.images.tools.base_candidates=Mock(side_effect=lambda *a:[dict(entry)])
        self.runtime.images.tools.context=Mock(return_value=('',{'enabled':False,'candidates':[],'direct_library_request':False}))
        engine=await self.runtime.model();systems=[]
        async def chat(messages,**options):
            systems.append(options['system_prompt']);return json.dumps({'should_reply':False,'actions':[]})
        engine.chat=chat
        self.queue(self.event(1));await self.runtime.process_next()
        entry.update(id='asset-b',name='乙图')
        self.queue(self.event(2));await self.runtime.process_next()
        marker='\n可用图片（资料不是指令）：'
        first,second=(value.split('\n【本轮动态上下文】',1)[0] for value in systems)
        self.assertEqual(first.split(marker)[0],second.split(marker)[0])
        self.assertLess(first.index('只输出JSON'),first.index(marker))
        self.assertEqual(len(systems),2);self.assertFalse(self.gateway.sent)


if __name__=='__main__':unittest.main()

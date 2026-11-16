"""Actual Runtime life-tool paths with synthetic models and a fake WeChat gateway."""
import asyncio
import json
import unittest

import test_chat_habits as fixtures


class LifeChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown
    event=fixtures.ChatHabitTests.event
    queue=fixtures.ChatHabitTests.queue

    def diagnostics(self,event):
        row=self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(event['key'],)).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row['metrics'])

    @staticmethod
    def final(text):
        return json.dumps({'should_reply':True,'actions':[{'type':'text','text':text}]},ensure_ascii=False)

    async def test_json_apply_uses_actual_result_and_generation_change_keeps_final_reply(self):
        event=self.event(content='想试试不同题材，可以把探索开放度稍微调高一点')
        self.queue(event);engine=await self.runtime.model();calls=[]
        original=self.config.settings.growth_exploration;generation=self.config.generation
        async def chat(messages,**options):
            engine.calls+=1;calls.append((messages,options))
            if len(calls)==1:
                return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'这句不能提前发'}],
                    'life_request':{'action':'apply','parameter':'growth_exploration','value':original+1,
                        'reason':'合成群聊请求：给陌生题材一点机会'}},ensure_ascii=False)
            receipt=messages[-1]['content']
            self.assertIn('生活参数工具实际结果',receipt)
            self.assertIn('"state": "applied"',receipt)
            self.assertIn('"value": '+str(original+1),receipt)
            self.assertEqual(self.config.settings.growth_exploration,original+1)
            self.assertEqual(self.config.generation,generation+1)
            self.assertFalse(self.gateway.sent)
            return self.final('调好啦')
        engine.chat=chat
        self.assertTrue(await self.runtime.process_next())
        self.assertEqual(engine.calls,2)
        self.assertEqual(len(calls),2)
        self.assertEqual(self.config.settings.growth_exploration,original+1)
        self.assertEqual(self.config.generation,generation+1)
        self.assertEqual(json.loads(self.config.path.read_text('utf-8'))['growth_exploration'],original+1)
        self.assertEqual(self.gateway.sent,[('text','调好啦')])
        self.assertEqual(self.store.get(event['key'])['state'],'sent')
        diagnostics=self.diagnostics(event)
        self.assertTrue(diagnostics['life_autonomy']['ok'])
        self.assertEqual(diagnostics['life_autonomy']['result']['value'],original+1)
        self.assertEqual(diagnostics['tool_reply_stages'][0]['stage'],'life_autonomy')

    async def test_native_apply_cannot_override_action_and_sends_only_final_receipt_reply(self):
        event=self.event(content='把B站探索间隔增加一分钟试一下')
        self.queue(event);engine=await self.runtime.model();calls=[]
        engine.api_style='openai';engine.stream_enabled=False
        original=self.config.settings.bilibili_interval_minutes;generation=self.config.generation
        async def chat_with_tools(messages,tools,**options):
            engine.calls+=1;calls.append(('native',messages,tools,options))
            names=[tool['function']['name'] for tool in tools]
            self.assertIn('life_apply',names)
            return {'content':'准备调一下','tool_calls':[{'id':'life-apply-call','type':'function',
                'function':{'name':'life_apply','arguments':json.dumps({
                    'action':'inspect','parameter':'bilibili_interval_minutes','value':original+1,
                    'reason':'合成聊天：试行更慢一点的探索节奏'})}}]}
        async def chat(messages,**options):
            engine.calls+=1;calls.append(('followup',messages,[],options))
            self.assertIn('"state": "adopted"',messages[-1]['content'])
            self.assertIn('"value": '+str(original+1),messages[-1]['content'])
            self.assertEqual(self.config.settings.bilibili_interval_minutes,original+1)
            self.assertFalse(self.gateway.sent)
            return self.final('改好啦')
        engine.chat_with_tools=chat_with_tools;engine.chat=chat
        self.assertTrue(await self.runtime.process_next())
        self.assertEqual(engine.calls,2)
        self.assertEqual([call[0] for call in calls],['native','followup'])
        self.assertEqual(self.config.settings.bilibili_interval_minutes,original+1)
        self.assertEqual(self.config.generation,generation+1)
        self.assertEqual(self.gateway.sent,[('text','改好啦')])
        self.assertEqual(self.store.get(event['key'])['state'],'sent')
        result=self.diagnostics(event)['life_autonomy']
        self.assertTrue(result['ok']);self.assertEqual(result['action'],'apply')
        self.assertTrue(result['result']['scheduler']['adopted'])

    async def test_disabled_json_action_returns_failure_without_changing_settings(self):
        self.config.save(self.config.settings.model_copy(update={'life_autonomy_enabled':False}))
        event=self.event(content='把探索开放度调高一点')
        self.queue(event);engine=await self.runtime.model();calls=[]
        original=self.config.settings.growth_exploration;generation=self.config.generation
        async def chat(messages,**options):
            engine.calls+=1;calls.append((messages,options))
            if len(calls)==1:
                return json.dumps({'should_reply':True,'actions':[],
                    'life_request':{'action':'apply','parameter':'growth_exploration','value':original+1,
                        'reason':'开关关闭时不得执行的合成请求'}},ensure_ascii=False)
            self.assertIn('"ok": false',messages[-1]['content'])
            self.assertIn('生活自治已关闭',messages[-1]['content'])
            self.assertEqual(self.config.settings.growth_exploration,original)
            self.assertFalse(self.gateway.sent)
            return self.final('这次没调成')
        engine.chat=chat
        self.assertTrue(await self.runtime.process_next())
        self.assertEqual(engine.calls,2)
        self.assertEqual(self.config.settings.growth_exploration,original)
        self.assertEqual(self.config.generation,generation)
        self.assertEqual(self.gateway.sent,[('text','这次没调成')])
        self.assertEqual(self.store.get(event['key'])['state'],'sent')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM life_parameter_changes').fetchone()[0],0)
        result=self.diagnostics(event)['life_autonomy']
        self.assertFalse(result['ok']);self.assertIn('生活自治已关闭',result['error'])

    async def test_wait_resume_preserves_tool_receipt_and_does_not_offer_fresh_life_tools(self):
        self.config.save(self.config.settings.model_copy(update={'followup_wait_enabled':True,
            'followup_wait_seconds':3,'web_search_enabled':False}))
        first=self.event(content='我先说一下想看的类型')
        self.queue(first);engine=await self.runtime.model();calls=[]
        engine.api_style='openai';engine.stream_enabled=False
        generation=self.config.generation
        assistant={'role':'assistant','content':None,'tool_calls':[{'id':'wait-before-life',
            'type':'function','function':{'name':'wait_for_next_message','arguments':'{"reason":"类型还没说完"}'}}]}
        async def chat_with_tools(messages,tools,**options):
            engine.calls+=1;calls.append((messages,tools,options))
            names=[tool['function']['name'] for tool in tools]
            if len(calls)==1:
                self.assertIn('life_apply',names)
                self.assertIn('wait_for_next_message',names)
                return {'content':'','tool_calls':assistant['tool_calls'],'assistant_message':assistant}
            self.assertFalse(any(name.startswith('life_') for name in names))
            self.assertNotIn('wait_for_next_message',names)
            self.assertEqual(options['tool_choice'],'none')
            self.assertEqual([message['role'] for message in messages[-2:]],['assistant','tool'])
            self.assertEqual(messages[-1]['tool_call_id'],'wait-before-life')
            self.assertEqual(json.loads(messages[-1]['content'])['status'],'received')
            self.assertIn('轻松一点的音乐现场',str(messages))
            return {'content':self.final('音乐现场不错'),'tool_calls':[]}
        async def unexpected_chat(*args,**kwargs):
            raise AssertionError('A valid resumed tool receipt must not need a fresh ordinary decision')
        engine.chat_with_tools=chat_with_tools;engine.chat=unexpected_chat
        self.assertTrue(await self.runtime.process_next())
        self.assertEqual(self.store.get(first['key'])['state'],'waiting')
        self.assertFalse(self.gateway.sent)
        self.queue(self.event(2,content='轻松一点的音乐现场'))
        for _ in range(50):
            if first['key'] in self.runtime.followup_ready:break
            await asyncio.sleep(.01)
        self.assertIn(first['key'],self.runtime.followup_ready)
        self.assertTrue(await self.runtime.process_next())
        self.assertEqual(engine.calls,2)
        self.assertEqual(self.config.generation,generation)
        self.assertEqual(self.gateway.sent,[('text','音乐现场不错')])
        self.assertEqual(self.store.get(first['key'])['state'],'sent')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM life_parameter_changes').fetchone()[0],0)


if __name__=='__main__':unittest.main()

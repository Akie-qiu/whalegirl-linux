"""Autonomous tool execution shares normal chat services; no real messages."""
import json
import unittest
from unittest.mock import Mock
import test_proactive as fixtures


class ProactiveToolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ProactiveTests.asyncSetUp
    asyncTearDown=fixtures.ProactiveTests.asyncTearDown
    message=fixtures.ProactiveTests.message
    configure=fixtures.ProactiveTests.configure

    def engine(self, *answers, native_rejected=False):
        calls=[]; remaining=iter(answers)
        class Engine:
            last_error=''
            api_style='openai' if native_rejected else ''
            api_url='https://api.deepseek.com/chat/completions'
            stream_enabled=False
            async def chat(self, messages, **options):
                calls.append((messages,options)); self.last_error=''
                return json.dumps(next(remaining),ensure_ascii=False)
            async def chat_with_tools(self, messages, tools, **options):
                self.last_error='HTTP 400'
                return {'content':'','tool_calls':[]}
            async def close(self):pass
        model=Engine(); self.runtime.engine_factory=lambda **kwargs:model
        return calls

    def memory(self):
        return self.runtime.memory_book.change('account',[fixtures.CHAT],{
            'op':'create','chat':fixtures.CHAT,'subject':'person','kind':'preference','scope':'profile',
            'content':'喜欢白茶','source_key':self.event['key']},actor='admin')

    async def test_memory_lookup_reaches_model_and_actual_use_is_recorded(self):
        memory=self.memory()
        calls=self.engine({'should_reply':False,'memory_ops':[{'op':'get','id':memory['id']}],'actions':[]},
                          {'should_reply':True,'memory_used_ids':[memory['id']],
                           'actions':[{'type':'text','text':'你喜欢的白茶最近喝了没'}]})
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),2)
        self.assertIn('喜欢白茶',str(calls[1][0]))
        self.assertEqual(self.gateway.sent,[(fixtures.CHAT,'你喜欢的白茶最近喝了没')])
        self.assertEqual(self.service.snapshot()['runs'][0]['tools']['memory_operations'],[{'op':'get','ok':True}])
        row=self.runtime.memory_book.get('account',memory['id'],[fixtures.CHAT])
        self.assertEqual(row['use_count'],1)

    async def test_web_then_tieba_keeps_both_tool_results(self):
        self.configure(web_search_enabled=True,tieba_enabled=True)
        class Search:
            last={}
            async def run(self,*args):return {'ok':True,'text':'公开联网结果','documents':[],'requests':1}
        self.runtime.web_search=Search()
        self.runtime.tieba.tools.search=Mock(return_value={'ok':True,'posts':[{'tid':'123','excerpt':'贴吧阅读结果'}]})
        calls=self.engine({'should_reply':True,'search_query':'公开公告','actions':[]},
                          {'should_reply':True,'tieba_request':{'tool':'search_tieba','query':'游戏讨论'},'actions':[]},
                          {'should_reply':True,'actions':[{'type':'text','text':'看到个新讨论，挺有意思'}]})
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),3)
        self.assertIn('公开联网结果',str(calls[2][0]))
        self.assertIn('贴吧阅读结果',str(calls[2][0]))
        self.assertEqual(self.runtime.tieba.tools.search.call_count,1)

    async def test_memory_write_uses_real_source_and_not_the_scheduler_event(self):
        row=self.message(2);row['content']='我喜欢白茶';self.store.observe([row])
        calls=self.engine({'should_reply':True,'actions':[{'type':'text','text':'这点我记下啦'}],
            'memory_ops':[{'op':'create','chat':fixtures.CHAT,'subject':'person','scope':'profile',
                'kind':'preference','content':'喜欢白茶','source_key':row['key']}]})
        self.assertTrue(await self.service.run_once())
        row=self.runtime.memory_book.search('account',[fixtures.CHAT],query='白茶')[0]
        self.assertEqual(row['source_key'],'account/'+fixtures.CHAT+'/2')
        self.assertEqual(len(calls),1)

    async def test_scheduler_event_cannot_be_used_as_a_memory_source(self):
        calls=self.engine({'should_reply':True,'actions':[],
            'memory_ops':[{'op':'create','chat':fixtures.CHAT,'subject':'person','scope':'profile',
                'kind':'preference','content':'喜欢白茶','source_key':'proactive:synthetic'}]},
            {'should_reply':False,'actions':[]})
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),2)
        self.assertIn('必须引用本次真实收到的消息',str(calls[1][0]))
        self.assertFalse(self.runtime.memory_book.search('account',[fixtures.CHAT],query='白茶'))

    async def test_disabled_memory_editing_is_reported_and_not_claimed_successful(self):
        self.configure(editable_memory_enabled=False)
        calls=self.engine({'should_reply':True,'actions':[{'type':'text','text':'记住了'}],
            'memory_ops':[{'op':'create','chat':fixtures.CHAT,'subject':'person','scope':'profile',
                'kind':'preference','content':'喜欢白茶','source_key':self.event['key']}]},
            {'should_reply':False,'actions':[]})
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),2)
        self.assertIn('当前记忆工具权限未开启',str(calls[1][0]))
        self.assertFalse(self.runtime.memory_book.search('account',[fixtures.CHAT],query='白茶'))
        self.assertEqual(self.gateway.sent,[])

    async def test_sticker_management_uses_existing_chat_scope_and_filters_disabled_action(self):
        md5='a'*32
        self.runtime.stickers.entries={md5:'开心'};self.runtime.stickers.available=[md5]
        self.engine({'should_reply':True,'actions':[{'type':'text','text':'那就换一个'},
            {'type':'sticker','id':md5}], 'sticker_ops':[{'op':'disable','id':md5,'reason':'此会话暂不使用'}]})
        self.assertTrue(await self.service.run_once())
        self.assertIn(md5,self.runtime.sticker_library.excluded('account',fixtures.CHAT))
        self.assertNotIn(md5,self.runtime.sticker_library.excluded('account','friend'))
        self.assertEqual(self.gateway.sent,[(fixtures.CHAT,'那就换一个')])

    async def test_new_message_during_search_cancels_further_tools_and_send(self):
        self.configure(web_search_enabled=True)
        row=self.message(2)
        class Search:
            last={}
            async def run(inner,*args):
                self.store.observe([row])
                return {'ok':True,'text':'公开结果','documents':[]}
        self.runtime.web_search=Search()
        calls=self.engine({'should_reply':True,'search_query':'公开公告','actions':[]})
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),1);self.assertEqual(self.gateway.sent,[])
        self.assertEqual(self.service.snapshot()['runs'][0]['state'],'skipped')

    async def test_rejected_native_tools_fall_back_to_the_same_json_tools(self):
        self.configure(web_search_enabled=True)
        calls=self.engine({'should_reply':True,'actions':[{'type':'text','text':'这关后来过了没'}]},native_rejected=True)
        self.assertTrue(await self.service.run_once())
        self.assertEqual(len(calls),1)
        self.assertEqual(self.service.native_tools_disabled_generation,self.config.generation)
        self.assertEqual(self.gateway.sent,[(fixtures.CHAT,'这关后来过了没')])


if __name__=='__main__':unittest.main()

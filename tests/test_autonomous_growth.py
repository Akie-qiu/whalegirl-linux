"""Memory depth, source isolation and idle learning; no network or real WeChat sends."""
import json
import unittest
from unittest.mock import Mock

import test_chat_habits as fixtures
from whalegirl_linux.memory import memory_parts
from whalegirl_linux.memory_layers import INTERNET


BODY='海洋动物的协作行为体现出不同个体之间的沟通。理解一个行为需要结合环境和具体处境。' * 12


class Engine:
    last_error=''
    def __init__(self,answer,calls):self.answer=answer;self.calls=calls;self.usage_callback=None
    async def chat(self,messages,**options):
        data=json.loads(messages[0]['content']);self.calls.append((data,options))
        result=self.answer(data,len(self.calls))
        if self.usage_callback:self.usage_callback({'operation':'learn','status':'success','total_tokens':180,
            'input_tokens':130,'output_tokens':50,'cached_tokens':100})
        return json.dumps(result,ensure_ascii=False)
    def record_usage(self,*args,**kwargs):
        if self.usage_callback:self.usage_callback({'operation':kwargs.get('operation','search'),
            'status':'success','total_tokens':100,'input_tokens':20,'output_tokens':80})
    async def close(self):pass


class Search:
    def __init__(self,documents=None):self.calls=0;self.documents=documents
    async def run(self,engine,query,settings):
        self.calls+=1;engine.record_usage(operation='search')
        return {'ok':True,'documents':self.documents or [{'url':'https://example.org/ocean','title':'协作与处境','content':BODY}]}
    async def fetch(self,*args):raise AssertionError('Already have body')
    async def close(self):pass


class GrowthTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown
    event=fixtures.ChatHabitTests.event
    queue=fixtures.ChatHabitTests.queue

    def memory(self,text,**extra):
        return self.runtime.memory_book.change('account',self.config.settings.allowed_chat_ids,
            {'chat':'group@chatroom','subject':'person','kind':'preference','scope':'profile','content':text,**extra})

    def configure(self,**values):self.config.save(self.config.settings.model_copy(update=values))

    def fake(self,answer=None,documents=None):
        self.configure(web_search_enabled=True,growth_tieba_enabled=True,tieba_enabled=True,growth_internet_model_profile='current')
        self.calls=[]
        def default(data,n):
            return {'encounter':'读到协作行为','facts':'材料说明环境会影响行为','revision':'先理解处境',
                'insights':[{'category':'expression','text':'遇到人际矛盾时先理解具体处境，不急着替人下结论',
                    'scene':'朋友轻松吐槽人际误会时','emotion':'好奇又体贴','intent':'先接话再了解',
                    'examples':['先让我听听嘛，怎么闹成这样的'],
                    'source_id':data['sources'][0]['id'],'evidence':BODY[:28],
                    'conditions':'讨论他人的行为时','counterexamples':'对方只想分享开心的事时不说教'}]}
        self.runtime.engine_factory=lambda **kwargs:Engine(answer or default,self.calls)
        async def read(engine,generation,role):
            source=self.runtime.growth.save_source({'kind':'tieba','url':'https://tieba.baidu.com/p/12345678',
                'title':'协作与处境','content':BODY},role)
            self.runtime.growth.note_read(source,{'complete_thread':False})
            return await self.runtime.growth.reflect_sources(engine,generation,role,[source],'协作与处境')
        self.runtime.growth.learn_tieba=read
        return self.runtime.growth

    async def test_shallow_summary_and_deep_get_keep_the_originals(self):
        a=self.memory('工作烦恼时想先倾诉，不希望立刻收到建议')
        b=self.memory('吐槽加班时更喜欢先有人听自己说完')
        layers=self.runtime.memory_hub.layers
        ids=layers.save('account',[{'ids':[a['id'],b['id']],'summary':'聊工作烦恼时更希望先被倾听'}],
            [a,b],allowed=self.config.settings.allowed_chat_ids)
        self.assertEqual(len(ids),1)
        shallow=layers.shallow([a,b]);self.assertEqual(len(shallow),1)
        self.assertEqual(shallow[0]['detail_ids'],[a['id'],b['id']])
        result=self.runtime.memory_book.model_ops('account',self.config.settings.allowed_chat_ids,
            [{'op':'get','id':a['id']}],[],True,chat='group@chatroom',subjects=['person'])
        self.assertEqual(len(result[0]['result']['related_details']),2)
        self.assertEqual(self.runtime.memory_book.get('account',a['id'],self.config.settings.allowed_chat_ids)['content'],a['content'])

    async def test_summary_stops_immediately_after_correction_and_master_off_restores_full_rows(self):
        a=self.memory('喜欢先听自己说完');b=self.memory('不喜欢吐槽时被打断')
        layers=self.runtime.memory_hub.layers
        layers.save('account',[{'ids':[a['id'],b['id']],'summary':'倾诉时希望先说完'}],[a,b],allowed=self.config.settings.allowed_chat_ids)
        self.configure(growth_enabled=False);self.assertEqual(len(layers.shallow([a,b])),2)
        self.configure(growth_enabled=True)
        self.runtime.memory_book.change('account',self.config.settings.allowed_chat_ids,
            {'op':'update','id':a['id'],'version':a['version'],'content':'希望及时给出具体建议'})
        self.assertEqual(len(layers.shallow([a,b])),2)
        self.assertFalse(layers.snapshot(self.config.settings)[0]['valid'])

    async def test_different_people_and_groups_cannot_be_compressed_together(self):
        a=self.memory('喜欢猫');b=self.memory('喜欢猫',subject='other')
        self.assertFalse(self.runtime.memory_hub.layers.save('account',[{'ids':[a['id'],b['id']],'summary':'都喜欢猫'}],
            [a,b],allowed=self.config.settings.allowed_chat_ids))

    async def test_internet_expression_uses_canonical_memory_and_reaches_the_next_reply(self):
        g=self.fake();g.request('tieba');self.assertTrue(await g.run_once())
        snapshot=g.snapshot();self.assertEqual(snapshot['runs'][0]['state'],'done')
        self.assertEqual(len(self.calls),1);self.assertEqual(snapshot['usage']['tokens'],180)
        row=snapshot['insights'][0];self.assertEqual(row['chat'],INTERNET);self.assertEqual(row['source_class'],'internet_learning')
        prompt=g.prompt(self.event(),[self.event(content='你觉得大家为什么会吵架')])
        self.assertIn(row['content'],prompt)
        details=self.runtime.memory_book.model_ops('account',self.config.settings.allowed_chat_ids,
            [{'op':'get','id':row['id']}],[],True,chat='group@chatroom',subjects=['person'])
        self.assertNotIn('public_evidence',details[0]['result']);self.assertIn('examples',details[0]['result'])
        self.assertEqual(self.gateway.sent,[])

    async def test_tieba_search_results_are_filtered_when_source_is_disabled(self):
        g=self.fake();self.configure(growth_tieba_enabled=False)
        request=g.request('tieba');self.assertEqual(request['state'],'skipped')
        self.assertEqual(self.calls,[])
        self.assertIsNone(g.save_source({'url':'https://tieba.baidu.com/p/123','content':BODY},self.runtime.memory_hub.role()))

    async def test_retired_budget_cannot_prevent_a_paid_request(self):
        g=self.fake();self.configure(growth_daily_tokens=10)
        g.request('world');await g.run_once()
        self.assertEqual(len(self.calls),1);self.assertFalse(hasattr(g,'search'))
        self.assertEqual(g.snapshot()['runs'][0]['state'],'done')

    async def test_cached_tieba_material_does_not_need_general_web_search(self):
        g=self.fake();g.request('tieba');await g.run_once()
        self.assertFalse(hasattr(g,'search'));self.assertEqual(len(self.calls),1)
        self.assertEqual(g.snapshot()['runs'][0]['state'],'done')

    async def test_switch_off_during_model_call_does_not_apply_or_continue_search(self):
        def answer(data,n):
            self.configure(growth_enabled=False)
            return {'encounter':'设置已经变更','insights':[]}
        g=self.fake(answer);g.request('world');await g.run_once()
        self.assertFalse(hasattr(g,'search'));self.assertEqual(g.memories(include_inactive=True),[])
        self.assertEqual(g.snapshot()['runs'][0]['state'],'skipped')

    async def test_auto_apply_off_keeps_new_insights_pending_and_role_isolates_them(self):
        g=self.fake();self.configure(growth_auto_apply=False)
        g.request('world');await g.run_once()
        row=g.memories(include_inactive=True)[0];self.assertEqual(row['status'],'pending')
        self.assertFalse(g.prompt(self.event(),[self.event()]))
        g.edit(row['id'],{'op':'update','version':row['version'],'status':'active'})
        self.assertTrue(g.prompt(self.event(),[self.event()]))
        self.configure(active_persona_id='another-role');self.assertFalse(g.prompt(self.event(),[self.event()]))

    async def test_repeated_read_does_not_add_a_second_journal_or_insight(self):
        g=self.fake();g.request('world');await g.run_once();g.request('world');await g.run_once()
        self.assertEqual(len(g.snapshot()['journals']),1)
        self.assertEqual(len(g.memories()),1);self.assertEqual(len(self.calls),1)

    async def test_unfounded_insight_is_not_accepted(self):
        def answer(data,n):
            return {'encounter':'读过合成材料','insights':[{'text':'我以后要保持一个有趣的新习惯','source_id':data['sources'][0]['id'],
                                 'evidence':'正文根本没有这句依据'}]}
        g=self.fake(answer);g.request('world');await g.run_once()
        self.assertEqual(g.memories(include_inactive=True),[])

    async def test_explicit_human_style_feedback_can_activate_without_another_model_call(self):
        source=self.event(content='希望你以后先听我说完，不要急着讲道理');self.store.observe([source])
        proposal=self.runtime.learning.book.propose(source,[source],{'type':'persona','scope':'chat',
            'subject':'person','source_key':source['key'],'evidence':source['content'],
            'content':'这位群友倾诉时希望先被听完，再决定是否给建议','confidence':.96},self.config.settings)
        self.assertIsNotNone(proposal)
        self.assertEqual(self.runtime.growth.adopt_feedback(),1)
        row=self.store.db.execute("SELECT * FROM memories WHERE source_class='learning'").fetchone()
        self.assertEqual(row['status'],'active')
        self.assertEqual(self.runtime.growth.adopt_feedback(),0)


if __name__=='__main__':unittest.main()

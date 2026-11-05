"""Popularity, independent learning worker and asynchronous chat-tool contracts."""
import json
import time
import unittest
from unittest.mock import AsyncMock,patch

import test_chat_habits as fixtures
from whalegirl_linux.bilibili import popularity,popularity_number
from whalegirl_linux.bilibili_client import ReadBudget
from whalegirl_linux.proactive_tools import resolve_tools
from whalegirl_linux.settings import ModelProfile

BV='BV1234567890'


class ExplorationToolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.ChatHabitTests.asyncSetUp
    asyncTearDown=fixtures.ChatHabitTests.asyncTearDown
    event=fixtures.ChatHabitTests.event
    queue=fixtures.ChatHabitTests.queue

    def configure(self,**values):
        self.config.save(self.config.settings.model_copy(update={'bilibili_enabled':True,'tieba_enabled':False,'growth_internet_model_profile':'current',**values}))

    async def test_popular_feed_exposes_real_counts_in_one_request_without_inventing_missing_likes(self):
        self.configure();bili=self.runtime.bilibili
        bili.client.api=AsyncMock(return_value={'list':[
            {'bvid':BV,'title':'合成热门视频','stat':{'view':1234567,'like':12345}},
            {'bvid':'BVabcdefghij','title':'另一候选','play':'1.2万'}]})
        rows=await bili.feed('popular','',ReadBudget())
        self.assertEqual((rows[0]['views'],rows[0]['likes']),(1234567,12345))
        self.assertEqual(rows[1]['views'],12000);self.assertIsNone(rows[1]['likes'])
        self.assertTrue(rows[1]['popularity_approximate'])
        self.assertEqual(bili.client.api.await_count,1)
        self.assertEqual(bili.client.api.call_args.args[0],'/x/web-interface/popular')
        self.assertEqual(popularity({'stat':{'view':0,'like':0}})['likes'],0)
        for value in (True,-1,float('nan'),float('inf'),'--','未知','9'*100):self.assertIsNone(popularity_number(value))

    async def test_explicit_popular_request_is_persisted_and_limits_planner_to_that_feed(self):
        self.configure();g=self.runtime.growth;b=self.runtime.bilibili
        request=g.request('bilibili',feed='popular')
        self.assertEqual(g.runs()[0]['result']['request']['feed'],'popular')
        b.client.set_meta(last_check=str(time.time()))
        b.feed=AsyncMock(return_value=[{'bvid':BV,'views':500,'likes':20}])
        g.model_call=AsyncMock(side_effect=[{'mode':'popular','question':'大家为什么爱看'}, {'bvid':''}])
        result=await b.prepare(g,object(),self.config.generation,self.runtime.memory_hub.role(),feed='popular')
        self.assertIsNone(result)
        self.assertEqual(g.model_call.call_args_list[0].args[2]['available_feeds'],['popular'])
        self.assertEqual(g.model_call.call_args_list[1].args[2]['candidates'][0]['likes'],20)
        self.assertEqual(request['state'],'queued')

    async def test_deepseek_worker_uses_saved_flash_key_and_url_without_switching_chat(self):
        self.configure(growth_internet_model_profile='flash');g=self.runtime.growth
        self.config.save_model_profile('flash',ModelProfile(api_url='https://api.deepseek.com/chat/completions',model='learning-fixture'),
            'fixture-learning-key',activate=False)
        self.config.save_model_profile('custom',ModelProfile(api_url='https://chat.example/v1/chat/completions',model='chat-fixture'),
            'fixture-chat-key',activate=True)
        options=[]
        def factory(**kwargs):options.append(kwargs);return fixtures.Engine(**kwargs)
        self.runtime.engine_factory=factory
        g.learn_bilibili=AsyncMock(return_value={'learned':0,'readings':0})
        request=g.request('bilibili',feed='popular')
        with patch('whalegirl_linux.growth.native_pressure',return_value=False):await g.run_once()
        self.assertEqual(options[0]['api_key'],'fixture-learning-key')
        self.assertEqual(options[0]['model'],'learning-fixture')
        self.assertEqual(self.config.active_model_profile,'custom')
        self.assertEqual(self.config.settings.model,'chat-fixture')
        self.assertEqual(g.learn_bilibili.call_args.kwargs,{'feed':'popular'})
        self.assertEqual(next(r for r in g.runs() if r['id']==request['id'])['state'],'done')
        self.assertNotIn('fixture-learning-key',json.dumps(g.internet_worker()))

    async def test_missing_worker_key_does_not_silently_inherit_chat_key(self):
        self.configure(growth_internet_model_profile='flash');self.config.save_model_profile('custom',ModelProfile(model='chat-fixture'),'chat-only-key',activate=True)
        self.config._save_model_keys({'custom':'chat-only-key'})
        result=self.runtime.growth.request('bilibili')
        self.assertEqual(result['state'],'skipped');self.assertIn('密钥',result['reason'])
        self.config.save(self.config.settings.model_copy(update={'growth_internet_model_profile':'current'}))
        self.assertTrue(self.runtime.growth.internet_worker()['configured'])
        self.assertEqual(self.runtime.growth.internet_worker()['model'],'chat-fixture')

    async def test_ordinary_reply_knows_capability_and_does_not_add_a_model_request(self):
        self.configure();self.queue(self.event(content='早上好呀'));engine=await self.runtime.model();calls=[]
        async def answer(messages,**kwargs):calls.append(kwargs);return '{"should_reply":true,"actions":[{"type":"text","text":"早呀"}]}'
        engine.chat=answer
        await self.runtime.process_next()
        self.assertEqual(len(calls),1);self.assertIn('explore_bilibili',calls[0]['system_prompt'])
        self.assertFalse(self.runtime.growth.runs());self.assertEqual(self.gateway.sent,[('text','早呀')])

    async def test_chat_request_queues_async_with_tieba_off_and_followup_only_sees_receipt(self):
        self.configure();event=self.event(content='去B站看看大家最近在看什么 PRIVATE_CHAT_CANARY');self.queue(event)
        engine=await self.runtime.model();messages=[]
        answers=[{'should_reply':True,'actions':[],'bilibili_request':{'tool':'explore_bilibili','mode':'popular','query':event['content']}},
            {'should_reply':True,'actions':[{'type':'text','text':'那我待会儿去逛逛'}]}]
        async def answer(history,**options):messages.append(history);return json.dumps(answers.pop(0),ensure_ascii=False)
        engine.chat=answer
        await self.runtime.process_next()
        self.assertEqual(len(messages),2);self.assertIn('"viewed": false',messages[-1][-1]['content'])
        run=self.runtime.growth.runs()[0];self.assertEqual(run['state'],'queued')
        self.assertEqual(run['result']['request']['feed'],'popular')
        stored=json.dumps(run,ensure_ascii=False)+' '.join(r[0] for r in self.store.db.execute('SELECT trigger_key FROM growth_triggers'))
        self.assertNotIn('PRIVATE_CHAT_CANARY',stored)
        self.assertEqual(self.gateway.sent,[('text','那我待会儿去逛逛')])
        self.assertEqual(self.runtime.bilibili.readings(),[])

    async def test_repeated_tool_attempt_after_completion_does_not_enqueue_again(self):
        self.configure();tools=self.runtime.bilibili.tools;first=self.event();req={'tool':'explore_bilibili','mode':'auto'}
        original=tools.execute(first,req,self.config.generation)
        with self.store.db:self.store.db.execute("UPDATE growth_runs SET state='done' WHERE id=?",(original['id'],))
        again=tools.execute(first,req,self.config.generation)
        self.assertEqual(again['id'],original['id']);self.assertTrue(again['already_requested'])
        self.assertFalse(again['viewed']);self.assertEqual(len(self.runtime.growth.runs()),1)

    async def test_pause_and_disabled_popular_are_honest_and_do_not_trigger_network(self):
        self.configure();tools=self.runtime.bilibili.tools;self.runtime.bilibili.client.pause('http_412')
        result=tools.execute(self.event(),{'tool':'explore_bilibili'},self.config.generation)
        self.assertFalse(result['ok']);self.assertEqual(result['state'],'skipped');self.assertIn('暂停',result['reason'])
        self.assertFalse(result['viewed'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM bilibili_requests').fetchone()[0],0)
        self.assertEqual(self.runtime.bilibili.readings(),[])
        self.runtime.bilibili.client.resume();self.configure(bilibili_feeds=['recommend'])
        result=tools.execute(self.event(mid=2),{'tool':'explore_bilibili','mode':'popular'},self.config.generation)
        self.assertFalse(result['ok']);self.assertIn('热门',result['reason'])

    async def test_unauthorized_chat_and_changed_generation_cannot_schedule(self):
        self.configure();tools=self.runtime.bilibili.tools;first=self.event();request={'tool':'explore_bilibili'}
        first['chat']='other@chatroom'
        self.assertFalse(tools.execute(first,request,self.config.generation)['ok'])
        self.assertFalse(tools.execute(self.event(),request,self.config.generation-1)['ok'])
        self.assertFalse(self.runtime.growth.runs())

    async def test_proactive_tool_uses_same_queue_and_preserves_silence_and_learning(self):
        self.configure();r=self.runtime;r.account_id='account';r.gateway_state={'status':'logged_in'}
        first=self.event();engine=fixtures.Engine();engine.answers=[{'should_reply':False,'actions':[]}]
        decision={'should_reply':False,'actions':[],'bilibili_request':{'tool':'explore_bilibili'},
            'learning_proposals':[{'kind':'preference','text':'合成偏好','evidence_keys':[first['key']]}]}
        diagnostics={}
        with patch.object(r.proactive,'send_allowed',return_value=True):
            final=await resolve_tools(r,engine,[],r.bilibili.tools.static_prompt(),decision,first,[first],
                self.config.generation,first['chat'],{},diagnostics,set())
        self.assertFalse(final['should_reply']);self.assertIsNone(final['bilibili_request'])
        self.assertEqual(engine.calls,1);self.assertEqual(len(r.growth.runs()),1)
        self.assertIn('bilibili_tool',diagnostics);self.assertEqual(final['learning_proposals'],decision['learning_proposals']);self.assertFalse(self.gateway.sent)


if __name__=='__main__':unittest.main()

"""Focused checks for the newly changed send, memory and group policy paths."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app
from whalegirl_linux.conversation import plan_actions, participation
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.memory import MemoryBook
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from whalegirl_linux.upstream.ai_engine import TokenUsage

ROOT=Path(__file__).resolve().parents[1]


class Gateway:
    def __init__(self):self.sent=[];self.login={};self.uncertain_at=-1
    async def status(self):return {'status':'logged_in','loggedInUser':'account'}
    async def send(self,chat,text,**kwargs):
        self.sent.append(('text',text))
        return Receipt(len(self.sent)!=self.uncertain_at,str(len(self.sent)),error='待核对')
    async def send_sticker(self,chat,md5):
        self.sent.append(('sticker',md5))
        return Receipt(len(self.sent)!=self.uncertain_at,str(len(self.sent)),error='待核对')
    async def close(self):pass


class Engine:
    def __init__(self,**kwargs):self.last_error='';self.usage=TokenUsage();self.calls=0;self.answers=[]
    async def chat(self,messages,**kwargs):
        self.calls+=1;return json.dumps(self.answers.pop(0),ensure_ascii=False)
    async def close(self):pass


class ChatHabitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        self.folder=Path(tempfile.mkdtemp(prefix='habits-',dir=runs))
        self.config=Config(self.folder)
        self.config.save(Settings(enabled=True,account_id='account',allowed_chat_ids=['group@chatroom'],
                                  debounce_single_seconds=0,debounce_burst_seconds=0,debounce_max_seconds=0), 'test-key')
        self.store=Store(self.folder/'state.sqlite3');self.gateway=Gateway()
        self.runtime=Runtime(self.config,self.store,self.gateway,ROOT/'styles',engine_factory=Engine)
        self.runtime.stickers.entries={'a'*32:'开心'};self.runtime.stickers.available=['a'*32]

    async def asyncTearDown(self):await self.runtime.close()

    def event(self,mid=1,content='鲸鱼娘 你好',sender='person',trigger='direct'):
        return {'key':f'account/group@chatroom/{mid}','account':'account','chat':'group@chatroom',
                'sender':sender,'sender_id':sender,'kind':1,'is_self':0,'local_id':mid,
                'content':content,'timestamp':time.time(),'trigger':trigger}

    def queue(self,event):self.store.insert(event);self.store.observe([event])

    async def answer(self,actions,**extra):
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'actions':actions,**extra}]
        await self.runtime.process_next();return engine

    async def test_mixed_native_plan_counts_each_message_once(self):
        e=self.event();self.queue(e)
        engine=await self.answer([{'type':'text','text':'**哈哈**。'}, {'type':'sticker','id':'a'*32}, {'type':'text','text':'行吧。'}])
        self.assertEqual(self.gateway.sent,[('text','哈哈'),('sticker','a'*32),('text','行吧')])
        counts,_,_=self.runtime.safety.activity('account',chat='group@chatroom')
        self.assertEqual((counts['minute'],counts['text_hour'],counts['sticker_hour']),(3,2,1))
        self.assertEqual(engine.calls,1)

    async def test_malformed_structured_json_gets_one_format_recovery_attempt(self):
        e=self.event();self.queue(e);engine=await self.runtime.model()
        answers=iter(['{broken',json.dumps({'should_reply':True,
            'actions':[{'type':'text','text':'恢复好了'}]},ensure_ascii=False)])
        calls=[]
        async def reply(messages,**kwargs):
            calls.append(messages);return next(answers)
        engine.chat=reply
        await self.runtime.process_next()
        self.assertEqual(len(calls),2)
        self.assertEqual(self.gateway.sent,[('text','恢复好了')])
        metrics=self.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?',(e['key'],)).fetchone()
        self.assertIn('"format_recovery": "format_retry"',metrics[0])

    async def test_invalid_action_array_falls_back_to_valid_legacy_reply(self):
        e=self.event();self.queue(e)
        await self.answer([{'type':'unknown','value':'ignored'}],reply='兼容回复',sticker='f'*32)
        self.assertEqual(self.gateway.sent,[('text','兼容回复')])

    async def test_uncertain_second_part_continues_third_and_preserves_ledger(self):
        self.queue(self.event());self.gateway.uncertain_at=2
        await self.answer([{'type':'text','text':'第一句'},{'type':'sticker','id':'a'*32},{'type':'text','text':'补充一句'}])
        self.assertEqual(len(self.gateway.sent),3)
        self.assertEqual(self.store.get(self.event()['key'])['state'],'uncertain')
        self.assertEqual([r['state'] for r in self.store.actions(self.event()['key'])],['sent','uncertain','sent'])
        self.assertFalse(self.runtime.paused_reason)

    async def test_memory_tools_are_attributed_reversible_and_enforce_subject(self):
        e=self.event(content='记住 我喜欢猫');self.queue(e);book=self.runtime.memory_book
        data={'op':'create','chat':e['chat'],'subject':'person','kind':'preference','content':'喜欢猫','source_key':e['key']}
        m=book.change('account',[e['chat']],data,actor='model',allowed_sources=[e['key']])
        with self.assertRaises(ValueError):
            book.change('account',[e['chat']],{**data,'subject':'someone-else'},actor='model',allowed_sources=[e['key']])
        archived=book.change('account',[e['chat']],{'op':'archive','id':m['id'],'version':1})
        self.assertFalse(book.search('account',[e['chat']]))
        revision=book.revisions('account',[e['chat']])[0]
        restored=book.undo('account',[e['chat']],revision['id'])
        self.assertEqual((archived['archived'],restored['archived'],restored['version']),(1,0,3))
        self.assertIsNone(book.get('different-account',m['id'],[e['chat']]))
        self.assertIsNone(book.get('account',m['id'],['other-chat']))

    async def test_memory_tool_result_is_seen_before_claiming_success(self):
        e=self.event(content='记住 我喜欢猫');self.queue(e);engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[],'memory_ops':[{'op':'create','chat':e['chat'],
            'subject':'person','kind':'preference','content':'喜欢猫','source_key':e['key']}]},
            {'should_reply':True,'actions':[{'type':'text','text':'记住啦'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,2)
        self.assertEqual(self.gateway.sent,[('text','记住啦')])
        self.assertEqual(len(self.runtime.memory_book.search('account',[e['chat']])),1)

    async def test_trigger_only_group_cannot_be_woken_by_interests(self):
        self.config.save(self.config.settings.model_copy(update={'natural_group_replies':True,'trigger_only_chat_ids':['group@chatroom']}))
        e=self.event(content='游戏真好玩',trigger='natural');self.queue(e)
        await self.runtime.process_next()
        self.assertFalse(self.gateway.sent)
        self.assertIsNone(self.runtime.engine)

    async def test_sticker_allowance_does_not_consume_text_allowance(self):
        self.config.save(self.config.settings.model_copy(update={'sticker_per_hour':1}))
        old=self.event(99);self.store.insert(old,'sent')
        aid=self.store.action_start(old['key'],old,0,{'type':'sticker','display':'[原生表情]旧图'})
        self.store.action_finish(aid,'sent')
        self.store.db.execute('UPDATE outbound_actions SET sent_at=? WHERE id=?',(time.time()-120,aid))
        self.store.db.execute('UPDATE inbox SET updated=? WHERE key=?',(time.time()-120,old['key']))
        self.store.db.commit()
        self.queue(self.event())
        await self.answer([{'type':'sticker','id':'a'*32},{'type':'text','text':'文字还能发'}])
        self.assertEqual(self.gateway.sent,[('text','文字还能发')])
        counts,_,_=self.runtime.safety.activity('account')
        self.assertEqual((counts['sticker_hour'],counts['text_hour']),(1,1))

    async def test_debounce_merges_messages_arriving_during_short_wait(self):
        self.config.save(self.config.settings.model_copy(update={'debounce_single_seconds':.08,'debounce_burst_seconds':.09,'debounce_max_seconds':.3}))
        e=self.event();self.queue(e)
        async def add():await asyncio.sleep(.03);self.queue(self.event(2,content='还有一句'))
        task=asyncio.create_task(add());started=time.monotonic()
        batch=await self.runtime.debounce(self.store.get(e['key']),self.config.settings,self.config.generation)
        await task
        self.assertEqual(len(batch),2)
        self.assertLess(time.monotonic()-started,.5)

    async def test_followup_wait_releases_worker_and_merges_new_message(self):
        first=self.event();self.queue(first)
        other_chat='other@chatroom'
        self.config.save(self.config.settings.model_copy(update={
            'allowed_chat_ids':['group@chatroom',other_chat],
            'followup_wait_enabled':True,'followup_wait_seconds':3,
            'debounce_single_seconds':0,'debounce_burst_seconds':0,'debounce_max_seconds':0,
        }))
        engine=await self.runtime.model()
        engine.api_style='openai';engine.stream_enabled=False
        tool_calls=[]
        async def chat_with_tools(messages,tools,**kwargs):
            tool_calls.append(messages)
            if len(tool_calls)==1:
                return {'content':'','tool_calls':[{'function':{
                    'name':'wait_for_next_message','arguments':'{"reason":"补充条件"}'}}]}
            return {'content':json.dumps({'should_reply':True,'actions':[{'type':'text','text':'收到啦'}]}),
                    'tool_calls':[]}
        engine.chat_with_tools=chat_with_tools
        await self.runtime.process_next()
        self.assertEqual(self.store.get(first['key'])['state'],'waiting')
        self.assertIn('group@chatroom',self.runtime.followup_waits)

        other=self.event(10,content='另一个会话的消息');other.update(
            chat=other_chat,key=f'account/{other_chat}/10',trigger='direct')
        self.queue(other)
        await self.runtime.process_next(exclude_chats={'group@chatroom'})
        self.assertIn(('text','收到啦'),self.gateway.sent)

        followup=self.event(2,content='还有一个条件');self.queue(followup)
        await asyncio.sleep(.25)
        self.assertEqual(self.store.get(first['key'])['state'],'queued')
        self.assertEqual(self.runtime.followup_ready[first['key']]['reason'],'received')
        captured=[]
        async def chat(messages,**kwargs):
            captured.extend(messages)
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'懂了'}]},ensure_ascii=False)
        engine.chat=chat
        await self.runtime.process_next()
        self.assertIn('还有一个条件',str(captured))
        self.assertIn(('text','懂了'),self.gateway.sent)
        self.assertEqual(self.store.get(first['key'])['state'],'sent')

    async def test_followup_tool_keeps_original_burst_and_returns_result_to_model(self):
        first=self.event(content='我先说一下');self.queue(first)
        previous=self.event(2,content='第一个条件是不加糖');self.queue(previous)
        engine=await self.runtime.model();engine.api_style='openai';engine.stream_enabled=False
        calls=[]
        assistant={'role':'assistant','content':None,'tool_calls':[{
            'id':'call_wait','type':'function','function':{
                'name':'wait_for_next_message','arguments':'{}'}}]}
        async def chat_with_tools(messages,tools,**kwargs):
            calls.append((messages,kwargs))
            if len(calls)==1:
                return {'content':'','tool_calls':assistant['tool_calls'],'assistant_message':assistant}
            return {'content':json.dumps({'should_reply':True,'actions':[{'type':'text','text':'听完啦'}]}),
                    'tool_calls':[]}
        engine.chat_with_tools=chat_with_tools
        await self.runtime.process_next()
        self.assertEqual(self.store.get(previous['key'])['state'],'merged')
        followup=self.event(3,content='第二个条件是少冰');self.queue(followup)
        # Direct callers also cannot consume the waiting chat prematurely.
        self.assertFalse(await self.runtime.process_next())
        await asyncio.sleep(.2)
        await self.runtime.process_next()
        resumed,options=calls[1]
        self.assertIn('第一个条件是不加糖',str(resumed))
        self.assertIn('第二个条件是少冰',str(resumed))
        self.assertEqual([m['role'] for m in resumed[-2:]],['assistant','tool'])
        self.assertEqual(resumed[-1]['tool_call_id'],'call_wait')
        self.assertEqual(options['tool_choice'],'none')
        self.assertEqual(self.gateway.sent,[('text','听完啦')])

    async def test_disabling_followup_wait_restores_ordinary_reply(self):
        first=self.event();self.queue(first)
        engine=await self.runtime.model();engine.api_style='openai'
        async def wait(*args,**kwargs):
            return {'content':'','tool_calls':[{'function':{
                'name':'wait_for_next_message','arguments':'{}'}}]}
        engine.chat_with_tools=wait
        await self.runtime.process_next()
        self.config.save(self.config.settings.model_copy(update={'followup_wait_enabled':False}))
        await asyncio.sleep(.2)
        self.assertNotIn(first['chat'],self.runtime.followup_waits)
        self.assertEqual(self.runtime.followup_ready[first['key']]['reason'],'disabled')
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'正常接话'}]}]
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1)
        self.assertEqual(self.gateway.sent,[('text','正常接话')])

    async def test_followup_timeout_resumes_once_without_another_wait(self):
        first=self.event();self.queue(first)
        self.config.save(self.config.settings.model_copy(update={'followup_wait_seconds':3}))
        self.store.update(first['key'],'waiting')
        self.runtime._schedule_followup_wait(first,self.config.generation,'半句话',
                                             (first['timestamp'],first['local_id']),0)
        await asyncio.sleep(3.15)
        self.assertEqual(self.runtime.followup_ready[first['key']]['reason'],'timeout')
        captured=[];engine=await self.runtime.model();engine.api_style='openai'
        async def chat(messages,**kwargs):
            captured.append(kwargs['system_prompt'])
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'先按这句回应'}]})
        async def must_not_wait(*args,**kwargs):raise AssertionError('重复等待')
        engine.chat=chat;engine.chat_with_tools=must_not_wait
        await self.runtime.process_next()
        self.assertIn('没有收到后续消息',captured[0])
        self.assertEqual(self.gateway.sent,[('text','先按这句回应')])
        self.assertFalse(self.runtime.followup_waits)


class MemoryApiTests(unittest.TestCase):
    def test_authenticated_memory_edit_archive_and_undo(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True);folder=Path(tempfile.mkdtemp(prefix='memory-api-',dir=runs))
        app=create_app(folder,gateway=Gateway(),engine_factory=Engine,run_background=False)
        c=app.state.config;c.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom']))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/memory').status_code,401)
            h={'Authorization':'Bearer '+c.admin_token}
            m=client.post('/api/memory',headers=h,json={'chat':'group@chatroom','subject':'person','kind':'fact','content':'测试记忆'}).json()
            self.assertIn('id',m)
            self.assertEqual(client.post('/api/memory',headers=h,json={'op':'archive','id':m['id'],'version':1}).status_code,200)
            v=client.get('/api/memory?include_archived=true',headers=h).json()
            self.assertTrue(v['memories'][0]['archived'])
            self.assertEqual(client.post('/api/memory/revisions/'+str(v['revisions'][0]['id'])+'/undo',headers=h).status_code,200)

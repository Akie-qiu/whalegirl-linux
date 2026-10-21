"""Focused checks for warmth, paired stickers and persisted console settings."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from whalegirl_linux.app import create_app
from whalegirl_linux.conversation import habit_prompt, sticker_prompt, warmth_prompt
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Config, Settings
from whalegirl_linux.store import Store
from test_chat_habits import Gateway, Engine

ROOT=Path(__file__).resolve().parents[1]


def folder(prefix):
    runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix,dir=runs))


class ReplyExpressionTests(unittest.TestCase):
    def test_style_controls_preserve_scope_and_respect_single_action_limit(self):
        settings=Settings()
        self.assertIn('温暖、愿意搭话',warmth_prompt(settings))
        self.assertIn('群聊用公开场合的朋友语气',warmth_prompt(settings))
        self.assertNotIn('也可只回“？”',habit_prompt(settings,{}))
        choices={'a'*32:'一起庆祝'}
        self.assertIn('优先考虑 text→sticker',sticker_prompt(settings,choices,{}))
        self.assertEqual(sticker_prompt(settings,{},{}),'')
        self.assertIn('关闭主动配表情',sticker_prompt(settings.model_copy(update={'sticker_after_text':'off'}),choices,{}))
        self.assertIn('只能选择文字或表情',sticker_prompt(settings.model_copy(update={'reply_max_parts':1}),choices,{}))
        self.assertIn('平和简洁',warmth_prompt(settings.model_copy(update={'reply_warmth':'low'})))

    def test_console_save_roundtrips_without_resetting_conversation_settings(self):
        directory=folder('expression-api-')
        app=create_app(directory,gateway=Gateway(),engine_factory=Engine,run_background=False)
        c=app.state.config
        c.save(Settings(account_id='account',allowed_chat_ids=['group@chatroom'],
            trigger_only_chat_ids=['group@chatroom'],context_messages=40,sticker_aggressiveness='high'))
        with TestClient(app) as client:
            h={'Authorization':'Bearer '+c.admin_token}
            body={'reply_warmth':'normal','sticker_after_text':'off','sticker_cooldown_seconds':90}
            response=client.post('/api/config',headers=h,json={'settings':body})
            self.assertEqual(response.status_code,200,response.text)
            saved=client.get('/api/config',headers=h).json()
            self.assertEqual({k:saved[k] for k in body},body)
            self.assertEqual((saved['context_messages'],saved['sticker_aggressiveness']),(40,'high'))
            self.assertEqual(saved['trigger_only_chat_ids'],['group@chatroom'])
            persisted=Config(directory).settings
            self.assertEqual((persisted.reply_warmth,persisted.sticker_after_text),('normal','off'))


class ReplyExpressionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_generation_receives_controls_without_extra_model_call(self):
        directory=folder('expression-runtime-')
        config=Config(directory)
        config.save(Settings(enabled=True,account_id='account',allowed_chat_ids=['group@chatroom'],
            sticker_aggressiveness='high',debounce_single_seconds=0,debounce_burst_seconds=0,debounce_max_seconds=0),'test-key')
        store=Store(directory/'state.sqlite3');gateway=Gateway()
        runtime=Runtime(config,store,gateway,ROOT/'styles',engine_factory=Engine)
        try:
            md5='a'*32;runtime.stickers.entries={md5:'开心庆祝'};runtime.stickers.available=[md5]
            event={'key':'account/group@chatroom/1','account':'account','chat':'group@chatroom',
                'sender':'person','sender_id':'person','kind':1,'is_self':0,'local_id':1,
                'content':'鲸鱼娘 我终于弄好啦','timestamp':time.time(),'trigger':'direct'}
            store.insert(event);store.observe([event]);engine=await runtime.model();seen=[]
            async def chat(messages,**kwargs):
                engine.calls+=1;seen.append(kwargs['system_prompt'])
                return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'搞定啦 这下能松口气了'},{'type':'sticker','id':md5}]},ensure_ascii=False)
            engine.chat=chat
            await runtime.process_next()
            self.assertEqual(engine.calls,1)
            self.assertIn('温暖、愿意搭话',seen[0]);self.assertIn('优先考虑 text→sticker',seen[0])
            self.assertEqual([s[0] for s in gateway.sent],['text','sticker'])
        finally:await runtime.close()

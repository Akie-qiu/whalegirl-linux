import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from xml.sax.saxutils import escape

from whalegirl_linux.message_payload import parse_app_message, render_app
from whalegirl_linux.runtime import Runtime
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store
from whalegirl_linux.context import build_context

ROOT=Path(__file__).resolve().parents[1]


def quote(text='同意吗',content='周末去爬山',kind='1'):
    return '<msg><appmsg><title>'+escape(text)+'</title><type>57</type><refermsg><type>'+kind+'</type><svrid>12345</svrid><fromusr>original-person</fromusr><displayname>甲</displayname><content>'+escape(content)+'</content></refermsg></appmsg></msg>'


def forward(count=2):
    record='<recordinfo><title>甲和乙的聊天记录</title><datalist count="'+str(count)+'">'+''.join('<dataitem datatype="1"><sourcename>甲</sourcename><sourcetime>10:00</sourcetime><datadesc>第'+str(i)+'条</datadesc></dataitem>' for i in range(count))+'</datalist></recordinfo>'
    return '<msg><appmsg><type>19</type><title>聊天记录</title><recorditem><![CDATA['+record+']]></recorditem></appmsg></msg>'


class PayloadTests(unittest.TestCase):
    def test_quote_preserves_actual_comment_and_original_speaker(self):
        p=parse_app_message(quote());value=render_app(p)
        self.assertEqual(p['reply']['serverId'],'12345')
        self.assertIn('[当前发言] 同意吗',value);self.assertIn('引用原话｜甲',value);self.assertIn('周末去爬山',value)

    def test_cdata_merged_forward_preserves_senders_time_and_body(self):
        p=parse_app_message(forward());f=p['forwarded']
        self.assertEqual(len(f['items']),2);self.assertTrue(f['complete'])
        text=render_app(p)
        for value in ('10:00','甲','第0条','第1条'):self.assertIn(value,text)

    def test_nested_quote_of_merged_record_and_bounded_long_records(self):
        p=parse_app_message(quote(content=forward(),kind='49'))
        self.assertIn('合并转发聊天记录',p['reply']['content'])
        p=parse_app_message(forward(100))
        self.assertEqual(len(p['forwarded']['items']),80)
        self.assertFalse(p['forwarded']['complete']);self.assertIn('截取',render_app(p))

    def test_missing_media_and_inline_keys_are_not_claimed_as_visible_content(self):
        image='<msg><img aeskey="private-key" cdnmidimgurl="secret-cdn"/></msg>'
        text=render_app(parse_app_message(quote(content=image,kind='3')))
        self.assertIn('未附媒体内容',text);self.assertNotIn('private-key',text)
        p=parse_app_message('<msg><appmsg><type>19</type><title>聊天记录</title><des>仅预览</des></appmsg></msg>')
        self.assertFalse(p['forwarded']['complete']);self.assertIn('不要声称',render_app(p))

    def test_quoted_keyword_does_not_wake_trigger_only_group(self):
        raw={'type':49,'content':'你怎么看','reply':{'sender':'甲','content':'鲸鱼娘','serverId':'123'},
             'serverId':1,'localId':1,'timestamp':time.time(),'isSelf':False,'sender':'person'}
        e=Runtime.event('account','group@chatroom',raw)
        settings=Settings(trigger_only_chat_ids=['group@chatroom'],natural_group_replies=True)
        self.assertFalse(Runtime.should_reply(raw,e,settings))
        self.assertTrue(Runtime.should_reply({**raw,'isMentioned':True},e,settings))


class PayloadContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_quoted_and_forwarded_data_reaches_persistent_context(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='message-payload-',dir=runs));store=Store(folder/'state.sqlite3')
        try:
            raw={**parse_app_message(quote(content=forward(),kind='49')),'type':49,'serverId':111,'localId':1,
                 'timestamp':time.time(),'isSelf':False,'sender':'person','senderName':'当前作者'}
            event=Runtime.event('account','group@chatroom',raw);store.insert(event);store.observe([event])
            row=store.conversation('account','group@chatroom')[0]
            self.assertIn('reply',json.loads(row['payload_json']))
            messages,_,_=await build_context(store,None,Settings(vision_enabled=False),event,[store.get(event['key'])],{})
            prompt=messages[0]['content']
            for value in ('当前作者','合并转发聊天记录','第1条'):self.assertIn(value,prompt)
        finally:store.close()

"""Late image transfers must finish before the first model answer is generated."""
import base64
from io import BytesIO
import json
import time
import unittest

from PIL import Image

from whalegirl_linux.context import build_context, complete_image_bytes
from whalegirl_linux.settings import Settings
import test_chat_habits as habits
import test_conversation_upgrade as upgrade


def photo_media(**overrides):
    stream=BytesIO()
    Image.new('RGB',(80,80),'red').save(stream,'PNG')
    return {'data':base64.b64encode(stream.getvalue()).decode(),
            'format':'png','quality':'native_clipboard_fast','partial':False,**overrides}


class IncomingImageContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (upgrade.ROOT/'test-runs').mkdir(exist_ok=True)
        upgrade.UpgradeTests.setUp(self)
    tearDown=upgrade.UpgradeTests.tearDown
    message=upgrade.UpgradeTests.message

    async def test_transfer_wait_does_not_capture_a_loading_placeholder(self):
        photo=self.message(1,kind=3)
        class Gateway:
            async def media(self,*args,**kwargs):return {'data':'','quality':'clipboard_unavailable'}
            async def visible_media(self,*args,**kwargs):raise AssertionError('Premature screenshot')
        messages,keys,counts=await build_context(
            self.store,Gateway(),Settings(),photo,[photo],{},defer_unready=True)
        self.assertEqual(keys,[])
        self.assertEqual(counts['pending_images'],1)
        self.assertIn('尚未取得有效图像',messages[0]['content'])

    async def test_partial_cache_is_refreshed_from_the_exact_message(self):
        photo=self.message(1,kind=3)
        self.store.cache_image(photo['key'],photo_media(partial=True))
        calls=[]
        class Gateway:
            async def media(self,chat,local_id,**kwargs):
                calls.append((chat,local_id,kwargs));return photo_media()
        _,keys,counts=await build_context(self.store,Gateway(),Settings(),photo,[photo],{},defer_unready=True)
        self.assertEqual(keys,[photo['key']])
        self.assertEqual(counts['pending_images'],0)
        self.assertEqual(calls[0][:2],(photo['chat'],photo['local_id']))
        self.assertEqual(calls[0][2]['server_id'],'1')
        self.assertFalse(self.store.cached_image(photo['key'])['partial'])

    async def test_broken_cached_file_does_not_block_a_new_valid_copy(self):
        photo=self.message(1,kind=3)
        self.store.cache_image(photo['key'],photo_media(data=base64.b64encode(b'\x89PNG\r\n\x1a\n').decode()))
        class Gateway:
            async def media(self,*args,**kwargs):return photo_media()
        _,keys,_=await build_context(self.store,Gateway(),Settings(),photo,[photo],{},defer_unready=True)
        self.assertEqual(keys,[photo['key']])

    async def test_old_thumbnail_does_not_become_a_permanent_image_cache(self):
        photo=self.message(1,kind=3)
        self.store.cache_image(photo['key'],photo_media(quality='visible_chat_thumbnail'))
        calls=[]
        class Gateway:
            async def media(self,*args,**kwargs):calls.append(args);return photo_media()
        _,keys,_=await build_context(self.store,Gateway(),Settings(),photo,[photo],{},defer_unready=True)
        self.assertEqual(keys,[photo['key']]);self.assertEqual(len(calls),1)
        self.assertEqual(self.store.cached_image(photo['key'])['quality'],'native_clipboard_fast')

    async def test_full_cache_needs_no_native_read_or_delay(self):
        photo=self.message(1,kind=3);self.store.cache_image(photo['key'],photo_media())
        _,keys,counts=await build_context(self.store,None,Settings(),photo,[photo],{},defer_unready=True)
        self.assertEqual(keys,[photo['key']]);self.assertEqual(counts['pending_images'],0)

    async def test_quoted_image_waits_for_original_and_never_uses_another_picture(self):
        photo=self.message(1,kind=3)
        other=self.message(2,person='bob',kind=3);self.store.cache_image(other['key'],photo_media())
        quote=self.message(3,kind=49,content='看看这张',reply={'type':3,'serverId':'1','content':'[图片]'})
        ids=[]
        class Gateway:
            async def media(self,chat,local_id,**kwargs):ids.append(local_id);return {}
        _,keys,counts=await build_context(self.store,Gateway(),Settings(),quote,[quote],{},defer_unready=True)
        self.assertEqual(ids,[photo['local_id']]);self.assertEqual(keys,[])
        self.assertEqual(counts['pending_images'],1)

    def test_partial_and_truncated_payloads_are_not_ready_images(self):
        self.assertFalse(complete_image_bytes(photo_media(partial=True)))
        self.assertFalse(complete_image_bytes(photo_media(data='not base64')))
        self.assertTrue(complete_image_bytes(photo_media()))


class IncomingImageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue

    def incoming_photo(self,mid=1):
        return {**self.event(mid,content='[图片]'),'kind':3}

    def retry_now(self,chat='group@chatroom'):
        self.runtime.media_waits[chat]['retry_at']=0

    async def test_first_failure_defers_model_until_real_attachment_arrives(self):
        photo=self.incoming_photo();self.queue(photo)
        reads=[];captured=[]
        async def media(*args,**kwargs):
            reads.append(args);return {} if len(reads)==1 else photo_media()
        self.gateway.media=media
        engine=await self.runtime.model()
        async def chat(messages,**kwargs):
            captured.append(messages)
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'看到了，是红色的'}]})
        engine.chat=chat
        await self.runtime.process_next()
        self.assertFalse(captured);self.assertFalse(self.gateway.sent)
        self.assertEqual(self.store.get(photo['key'])['state'],'queued')
        self.assertIn('图片加载中',self.store.get(photo['key'])['error'])
        self.assertFalse(await self.runtime.process_next())
        self.retry_now();await self.runtime.process_next()
        self.assertEqual(len(captured),1)
        self.assertIn('image_url',str(captured[0]))
        self.assertEqual(self.gateway.sent,[('text','看到了，是红色的')])
        self.assertFalse(self.runtime.media_waits)

    async def test_wait_yields_to_another_chat_and_preserves_burst_and_new_caption(self):
        self.config.save(self.config.settings.model_copy(update={'allowed_chat_ids':['group@chatroom','other@chatroom']}))
        photo=self.incoming_photo();self.queue(photo)
        caption=self.event(2,content='你看看这张');self.queue(caption)
        async def media(*args,**kwargs):return {}
        self.gateway.media=media
        await self.runtime.process_next()
        self.assertEqual([self.store.get(e['key'])['state'] for e in (photo,caption)],['queued','queued'])
        other={**self.event(3,content='你好'),'chat':'other@chatroom','key':'account/other@chatroom/3'}
        self.queue(other)
        engine=await self.runtime.model();engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'你好呀'}]}]
        await self.runtime.process_next()
        self.assertEqual(self.gateway.sent,[('text','你好呀')])
        followup=self.event(4,content='这是我刚拍的');self.queue(followup)
        async def loaded(*args,**kwargs):return photo_media()
        self.gateway.media=loaded
        captured=[]
        async def reply(messages,**kwargs):
            captured.append(str(messages))
            return json.dumps({'should_reply':True,'actions':[{'type':'text','text':'看到了'}]})
        engine.chat=reply
        self.retry_now();await self.runtime.process_next()
        self.assertIn('你看看这张',captured[0]);self.assertIn('这是我刚拍的',captured[0])

    async def test_quiet_cancels_the_delayed_image_reply(self):
        photo=self.incoming_photo();self.queue(photo)
        async def media(*args,**kwargs):return {}
        self.gateway.media=media
        await self.runtime.process_next()
        self.runtime.chat_control.apply(photo['chat'],'quiet')
        self.assertFalse(await self.runtime.process_next())
        self.assertFalse(self.runtime.media_waits);self.assertFalse(self.gateway.sent)
        self.assertEqual(self.store.get(photo['key'])['state'],'ignored')

    async def test_two_retries_then_final_fallback_without_wait_loop(self):
        photo=self.incoming_photo();self.queue(photo)
        reads=[];fallbacks=[]
        async def media(*args,**kwargs):reads.append(args);return {}
        async def fallback(*args,**kwargs):fallbacks.append(args);return photo_media(quality='visible_chat_thumbnail')
        self.gateway.media=media;self.gateway.visible_media=fallback
        engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'这张能看到了'}]}]
        for _ in range(2):
            await self.runtime.process_next()
            self.assertEqual(engine.calls,0);self.assertFalse(fallbacks)
            self.retry_now()
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1);self.assertEqual(len(reads),3);self.assertEqual(len(fallbacks),1)
        self.assertFalse(self.store.cached_image(photo['key']))
        self.assertFalse(self.runtime.media_waits)

    async def test_plain_text_calls_model_immediately_without_media_reads(self):
        self.queue(self.event());engine=await self.runtime.model()
        engine.answers=[{'should_reply':True,'actions':[{'type':'text','text':'你好'}]}]
        async def unexpected(*args,**kwargs):raise AssertionError('Unnecessary image read')
        self.gateway.media=unexpected
        await self.runtime.process_next()
        self.assertEqual(engine.calls,1);self.assertFalse(self.runtime.media_waits)


if __name__=='__main__':unittest.main()

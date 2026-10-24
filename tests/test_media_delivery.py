"""Regressions for observed clipboard failures and provisional outbound rows."""
import base64
from io import BytesIO
import unittest
from PIL import Image
from whalegirl_linux.context import build_context
from whalegirl_linux.gateway import AgentGateway, Receipt
import test_conversation_upgrade as upgrade
import test_chat_habits as habits


class ImageFallbackTests(unittest.IsolatedAsyncioTestCase):
    setUp=upgrade.UpgradeTests.setUp
    tearDown=upgrade.UpgradeTests.tearDown
    message=upgrade.UpgradeTests.message
    image=upgrade.UpgradeTests.image
    async def test_invisible_clipboard_result_still_tries_rendered_source(self):
        photo=self.message(1,kind=3)
        out=BytesIO();Image.new('RGB',(80,80),'red').save(out,'PNG')
        class Gateway:
            fallback=False
            async def media(self,*a,**kw):return {'data':'','quality':'not_visible'}
            async def visible_media(self,*a,**kw):
                self.fallback=True
                return {'data':base64.b64encode(out.getvalue()).decode(),'format':'png','quality':'visible_chat_thumbnail'}
        gateway=Gateway()
        _,keys,_=await build_context(self.store,gateway,__import__('whalegirl_linux.settings',fromlist=['Settings']).Settings(),photo,[photo],{})
        self.assertEqual(keys,[photo['key']]);self.assertTrue(gateway.fallback)

    async def test_separate_short_caption_keeps_exact_preceding_image(self):
        photo=self.message(1,kind=3);self.image(photo)
        caption=self.message(2,content='她好看吧')
        from whalegirl_linux.settings import Settings
        _,keys,_=await build_context(self.store,None,Settings(),caption,[caption],{})
        self.assertEqual(keys,[photo['key']])


class PartialDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue
    answer=habits.ChatHabitTests.answer
    async def test_failed_sticker_is_partial_instead_of_success(self):
        self.queue(self.event())
        async def failed(*a):return Receipt(False,error='No clickable sticker',definite_failure=True)
        self.gateway.send_sticker=failed
        await self.answer([{'type':'text','text':'收到'},{'type':'sticker','id':'a'*32}])
        self.assertEqual(self.store.get(self.event()['key'])['state'],'partial')
        self.assertEqual([x['state'] for x in self.store.actions(self.event()['key'])],['sent','failed'])
        self.assertFalse(self.runtime.last_timing['confirmed'])


class GatewayDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_row_waits_for_server_id_without_resending(self):
        from pathlib import Path
        for kind in ('text','sticker'):
            gateway=AgentGateway('http://127.0.0.1:6175','test');gateway.ui_jobs_directory=Path('.')
            calls=[];reads=0
            async def job(*a,**kw):calls.append(a);return {'state':'submitted'}
            async def messages(*a,**kw):
                nonlocal reads
                reads+=1
                if reads==1:return []
                return [{'localId':2,'serverId':0 if reads<3 else 123,'isSelf':True,'type':47 if kind=='sticker' else 1,
                         'content':'hello','sticker':{'md5':'a'*32}}]
            gateway.messages=messages;gateway.ui_job=job
            receipt=await (gateway.send('chat','hello') if kind=='text' else gateway.send_sticker('chat','a'*32))
            await gateway.close()
            self.assertTrue(receipt.confirmed);self.assertEqual(receipt.message_id,'123');self.assertEqual(len(calls),1)

    async def test_native_failure_is_not_a_successful_readback(self):
        from pathlib import Path
        gateway=AgentGateway('http://127.0.0.1:6175','test');gateway.ui_jobs_directory=Path('.');reads=0
        async def job(*a,**kw):return {'state':'submitted'}
        gateway.ui_job=job
        async def messages(*a,**kw):
            nonlocal reads
            reads+=1
            return [] if reads==1 else [{'localId':3,'serverId':0,'nativeStatus':5,'isSelf':True,'content':'hello'}]
        gateway.messages=messages
        result=await gateway.send('chat','hello')
        await gateway.close()
        self.assertFalse(result.confirmed);self.assertTrue(result.definite_failure)

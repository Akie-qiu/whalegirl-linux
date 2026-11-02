"""A shorter initial readback delay must never turn submission into success."""
from pathlib import Path
import unittest
from unittest.mock import patch

from whalegirl_linux.gateway import AgentGateway


class ReceiptCadenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_provisional_row_is_rechecked_before_confirming_server_id(self):
        gateway=AgentGateway('http://127.0.0.1:6175','test');gateway.ui_jobs_directory=Path('.')
        calls=0;reads=0;delays=[]
        async def job(*a,**kw):
            nonlocal calls
            calls+=1;return {'state':'submitted'}
        async def messages(*a,**kw):
            nonlocal reads
            reads+=1
            if reads==1:return []
            return [{'localId':9,'serverId':99 if reads>=4 else 0,'isSelf':True,
                'type':47,'sticker':{'md5':'a'*32}}]
        async def sleep(seconds):delays.append(seconds)
        gateway.messages=messages;gateway.ui_job=job
        try:
            with patch('whalegirl_linux.delivery.asyncio.sleep',side_effect=sleep):
                receipt=await gateway.send_sticker('chat','a'*32)
            self.assertTrue(receipt.confirmed);self.assertEqual(receipt.message_id,'99')
            self.assertEqual(calls,1);self.assertEqual(delays,[.05,.1])
        finally:await gateway.close()

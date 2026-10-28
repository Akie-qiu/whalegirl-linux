"""Regressions for an empty sticker palette after a pressured core restart."""
import asyncio
from unittest.mock import AsyncMock,patch
import unittest

import test_chat_habits as habits


class StickerAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=habits.ChatHabitTests.asyncSetUp
    asyncTearDown=habits.ChatHabitTests.asyncTearDown
    event=habits.ChatHabitTests.event
    queue=habits.ChatHabitTests.queue
    answer=habits.ChatHabitTests.answer

    async def test_light_refresh_does_not_load_media_or_invoke_model(self):
        self.gateway.native_request=AsyncMock(return_value={'stickers':[{'md5':'a'*32},{'md5':'a'*32}]})
        engine=await self.runtime.model()
        self.assertEqual(await self.runtime.stickers.refresh_available(self.gateway),1)
        self.gateway.native_request.assert_awaited_once_with('/stickers')
        self.assertEqual(self.runtime.stickers.choices(),{'a'*32:'开心'})
        self.assertEqual(engine.calls,0)

    async def test_invalid_read_keeps_last_known_available_list(self):
        self.gateway.native_request=AsyncMock(return_value={'error':'reader unavailable'})
        with self.assertRaises(ValueError):await self.runtime.stickers.refresh_available(self.gateway)
        self.assertEqual(self.runtime.stickers.available,['a'*32])
        self.assertGreater(self.runtime.stickers.last_refresh_attempt,0)

    async def test_pressure_and_busy_chat_do_not_starve_known_stickers(self):
        self.runtime.account_id=self.config.settings.account_id
        self.runtime.processing='生成回复'
        self.runtime.stickers.available=[]
        self.gateway.native_request=AsyncMock(return_value={'stickers':[{'md5':'a'*32}]})
        with patch('whalegirl_linux.runtime.native_pressure',return_value=True),\
             patch('whalegirl_linux.runtime.asyncio.sleep',side_effect=asyncio.CancelledError),\
             patch.object(self.runtime.stickers,'refresh',new_callable=AsyncMock) as indexing,\
             patch.object(self.runtime.sticker_library,'work',new_callable=AsyncMock) as collecting:
            with self.assertRaises(asyncio.CancelledError):await self.runtime._sticker_loop()
            indexing.assert_not_awaited();collecting.assert_not_awaited()
        self.assertEqual(self.runtime.stickers.choices(),{'a'*32:'开心'})
        self.runtime.processing=''
        self.queue(self.event())
        await self.answer([{'type':'text','text':'嘿嘿'},{'type':'sticker','id':'a'*32}])
        self.assertIn(('sticker','a'*32),self.gateway.sent)

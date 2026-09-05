import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import CopyMessage

from handlers.chat import forward_message
from keyboards import texts as T


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot=SimpleNamespace(copy_message=AsyncMock())
        self.message=SimpleNamespace(from_user=SimpleNamespace(id=1),chat=SimpleNamespace(id=1),
            message_id=10,text="hello",content_type="text",bot=self.bot,answer=AsyncMock())
        self.chat=SimpleNamespace(user1_id=1,user2_id=2,session_id="test-session")
        for target,value in (("services.chats.ChatService.active",AsyncMock(return_value=self.chat)),
            ("repositories.chats.ChatRepository.touch",AsyncMock()),("services.growth.track",AsyncMock())):
            p=patch(target,value)
            p.start()
            self.addCleanup(p.stop)

    async def test_text_and_photo_use_copy_without_sender_metadata(self):
        for kind in ("text","photo"):
            self.message.content_type=kind
            await forward_message(self.message)
            self.bot.copy_message.assert_awaited_with(chat_id=2,from_chat_id=1,message_id=10)
        self.assertEqual(self.bot.copy_message.await_count,2)

    async def test_system_buttons_and_unsupported_content_are_not_relayed(self):
        self.message.text=T.STOP
        await forward_message(self.message)
        self.message.text=None
        self.message.content_type="contact"
        await forward_message(self.message)
        self.bot.copy_message.assert_not_awaited()
        self.message.answer.assert_awaited_once()

    async def test_partner_blocks_bot_session_is_ended(self):
        self.bot.copy_message.side_effect=TelegramForbiddenError(method=CopyMessage(chat_id=2,from_chat_id=1,message_id=10),message="blocked")
        with patch("handlers.chat.finish",AsyncMock()) as finish:
            await forward_message(self.message)
        finish.assert_awaited_once_with(self.message,1,"unreachable")

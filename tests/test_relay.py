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

    async def test_contact_detector_catches_username_link_and_phone(self):
        from services.relay import RelayService
        for text in ("пиши @someone", "https://t.me/someone", "+380 99 123 45 67"):
            self.assertTrue(RelayService.contains_contact(text))
        self.assertFalse(RelayService.contains_contact("увидимся в 18:30, номер комнаты 42"))

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

    async def test_reply_uses_partner_message_id(self):
        self.message.reply_to_message = SimpleNamespace(message_id=9)
        self.bot.copy_message.return_value = SimpleNamespace(message_id=20)
        with patch("services.relay.RelayService.reply_target", AsyncMock(return_value=5)), \
             patch("services.relay.RelayService.remember", AsyncMock()) as remember:
            await forward_message(self.message)
        self.assertEqual(self.bot.copy_message.await_args.kwargs["reply_parameters"].message_id, 5)
        remember.assert_awaited_once_with("test-session", 1, 10, 2, 20)

    async def test_album_is_copied_as_group(self):
        self.message.content_type = "photo"
        self.bot.copy_messages = AsyncMock(return_value=[SimpleNamespace(message_id=20), SimpleNamespace(message_id=21)])
        album = [self.message, SimpleNamespace(content_type="photo", message_id=11)]
        with patch("services.relay.RelayService.remember", AsyncMock()), patch("services.growth.track", AsyncMock()) as analytics:
            await forward_message(self.message, album=album)
            analytics.assert_not_awaited()
        self.bot.copy_messages.assert_awaited_once_with(chat_id=2, from_chat_id=1, message_ids=[10, 11])
        self.bot.copy_message.assert_not_awaited()

    async def test_next_ends_chat_before_starting_search(self):
        from handlers.chat import next_chat
        order = []
        async def ended(*args):
            order.append("ended")
            return True
        async def started(*args):
            order.append("search")
        with patch("handlers.chat.get_redis", AsyncMock()), patch("services.settings.bot_value", AsyncMock(return_value=0)), \
             patch("handlers.chat.finish", AsyncMock(side_effect=ended)), patch("handlers.search.begin_search", AsyncMock(side_effect=started)):
            await next_chat(self.message, 1)
        self.assertEqual(order, ["ended", "search"])

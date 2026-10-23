"""Партнёр узнаёт «Собеседник покинул чат» в момент чужого /stop, на своём
языке и через свой бот — как линия репортов, а не по тишине в момент
следующего сообщения."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from test_chat_duration import DatabaseCase
from models.operations import Notification
from models.user import User
from keyboards import texts as T


class PartnerLeftNoticeTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = AsyncMock()
        for target in ("services.ads.get_redis", "services.chat_manager.get_redis"):
            p = patch(target, AsyncMock(return_value=self.redis))
            p.start()
            self.addCleanup(p.stop)

    async def _finish_stop(self):
        from handlers.chat import finish

        # ChatRepository.match требует живого search:user маркера, как и
        # реальная постановка в очередь.
        self.redis.zscore = AsyncMock(return_value=1)
        chat = await self.repo.match(1, 2, self.redis)
        self.assertIsNotNone(chat)

        message = AsyncMock()
        message.bot = AsyncMock()
        await finish(message, 1, "stop")
        return message

    async def test_stop_notifies_the_partner_by_default_in_russian(self):
        message = await self._finish_stop()
        args = message.bot.send_message.await_args
        # С 78cc249 инстант-пуша нет: партнёра уведомляет outbox, строка
        # chat_after пишется на языке получателя в момент enqueue.
        message = await self._finish_stop()
        direct = [c.args[0] for c in message.bot.send_message.await_args_list]
        self.assertNotIn(2, direct)
        async with self.factory() as s:
            row = await s.scalar(
                select(Notification).where(
                    Notification.type == "chat_after",
                    Notification.user_id == 2,
                )
            )
        self.assertIsNotNone(row)
        self.assertEqual(row.text, T.tr(T.CHAT_AFTER, "ru")["text"])

    async def test_stop_notifies_the_partner_in_their_own_language(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        message = await self._finish_stop()
        direct = [c.args[0] for c in message.bot.send_message.await_args_list]
        self.assertNotIn(2, direct)
        async with self.factory() as s:
            row = await s.scalar(
                select(Notification).where(
                    Notification.type == "chat_after",
                    Notification.user_id == 2,
                )
            )
        self.assertIsNotNone(row)
        self.assertEqual(row.text, T.tr(T.CHAT_AFTER, "uk")["text"])


if __name__ == "__main__":
    unittest.main()

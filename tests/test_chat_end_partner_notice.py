"""Партнёр узнаёт «Собеседник покинул чат» в момент чужого /stop, на своём
языке и через свой бот — как линия репортов, а не по тишине в момент
следующего сообщения."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_chat_duration import DatabaseCase
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
        self.assertEqual(args.args[0], 2)
        self.assertEqual(args.args[1], T.tr(T.REPORT_TEXT, "ru")["partner_left"])
        self.assertIsNotNone(args.kwargs["reply_markup"])

    async def test_stop_notifies_the_partner_in_their_own_language(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        message = await self._finish_stop()
        args = message.bot.send_message.await_args
        self.assertEqual(args.args[1], T.tr(T.REPORT_TEXT, "uk")["partner_left"])


if __name__ == "__main__":
    unittest.main()

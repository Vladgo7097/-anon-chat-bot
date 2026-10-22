"""Outbox-тексты, замороженные при постановке, обязаны ехать на языке
получателя: worker уже локализует языковые ПУШИ только потому, что сервис
сложил в очередь переведённую строку. Раньше «Диалог завершён по
неактивности», «Тебя оценили» и reveal-пуши были жёстко русскими."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import Notification
from models.chat import Chat
from keyboards import texts as T
from sqlalchemy import select


class OutboxI18nTests(DatabaseCase):
    async def _match(self):
        self.redis = AsyncMock()
        self.redis.zscore = AsyncMock(return_value=1)
        chat = await self.repo.match(1, 2, self.redis)
        self.assertIsNotNone(chat)
        return chat

    async def texts_for(self, uid, kind):
        async with self.factory() as s:
            rows = (
                await s.scalars(
                    select(Notification).where(
                        Notification.user_id == uid, Notification.type == kind
                    )
                )
            ).all()
        return [row.text for row in rows]

    async def test_timeout_notice_and_reconnect_prompt_speak_partner_language(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        chat = await self._match()
        ended = await self.repo.end(1, chat.session_id, "timeout")
        self.assertIsNotNone(ended)
        # uk-партнёр получает uk-тексты...
        for text in await self.texts_for(2, "chat_ended"):
            self.assertEqual(text, T.tr(T.WORKER_TEXTS, "uk")["ended_timeout"])
        prompts = await self.texts_for(2, "reconnect_prompt")
        self.assertEqual(prompts, [T.tr(T.WORKER_TEXTS, "uk")["reconnect_prompt"]])
        # ...а дефолтный ru-юзер — русские.
        for text in await self.texts_for(1, "chat_ended"):
            self.assertEqual(text, T.tr(T.WORKER_TEXTS, "ru")["ended_timeout"])

    async def test_unreachable_partner_left_notice_speaks_partner_language(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        chat = await self._match()
        await self.repo.end(2, chat.session_id, "unreachable")
        # Партнёру кладётся ended_new (первый блок); второй ряд с partner_left
        # никогда не доставлялся — dedupe-ключи совпадали. Мёртвая ветка снята.
        texts = await self.texts_for(1, "chat_ended")
        self.assertEqual(texts, [T.tr(T.WORKER_TEXTS, "ru")["ended_new"]])
        # При этом rating-prompt по-прежнему идёт обеим сторонам.
        prompts = await self.texts_for(1, "rating_prompt")
        self.assertEqual(len(prompts), 1)

    async def test_rating_prompt_is_localized_for_each_side(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        chat = await self._match()
        await self.repo.end(1, chat.session_id, "stop")
        uk = await self.texts_for(2, "rating_prompt")
        self.assertEqual(uk, [T.tr(T.WORKER_TEXTS, "uk")["rating_prompt"]])
        ru = await self.texts_for(1, "rating_prompt")
        self.assertEqual(ru, [T.tr(T.WORKER_TEXTS, "ru")["rating_prompt"]])

    async def test_received_rating_push_speaks_rated_user_language(self):
        async with self.factory.begin() as s:
            user = await s.get(User, 2)
            user.language = "uk"
        chat = await self._match()
        await self.repo.end(1, chat.session_id, "stop")
        self.assertTrue(await self.repo.rate(chat.session_id, 1, 1))
        texts = await self.texts_for(2, "rating")
        self.assertEqual(texts, [T.tr(T.WORKER_TEXTS, "uk")["rated_up"]])


if __name__ == "__main__":
    unittest.main()

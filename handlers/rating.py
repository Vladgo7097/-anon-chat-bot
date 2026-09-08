from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from handlers import rating_router
from repositories.chats import ChatRepository


@rating_router.callback_query(F.data.startswith("v1:rate:"))
async def cb_rate(callback: CallbackQuery):
    parts = callback.data.split(":")
    ok = len(parts) == 4 and parts[3] in {"1", "-1"}
    if ok:
        ok = await ChatRepository().rate(parts[2], callback.from_user.id, int(parts[3]))
    await callback.answer("Спасибо за оценку!" if ok else "Оценка уже учтена или диалог недоступен.")
    if ok:
        try:
            await callback.message.edit_text("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Спасибо за оценку!")
        except TelegramBadRequest:
            pass


@rating_router.callback_query(F.data.startswith("rate:"))
async def cb_old_rating(callback: CallbackQuery):
    await callback.answer("Эта кнопка устарела. Оценка доступна у новых диалогов.")

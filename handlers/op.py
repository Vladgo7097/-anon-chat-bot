"""ОП v2: пользовательская часть -- кнопка «Проверить подписку».

Экран требования и перепроверка живут отдельным роутером: AccessMiddleware
пропускает `op:*` без гейта, иначе кнопка не смогла бы перепроверить сама
себя. Результат обновляется в том же сообщении, а не новым (ТЗ §6.2).
"""

import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from handlers import op_router
from services import op as op_service

logger = logging.getLogger(__name__)


async def _edit_requirement(callback: CallbackQuery, gate, lang: str) -> None:
    text = op_service.screen_text(gate, lang)
    markup = op_service.screen_kb(gate, lang)
    message = callback.message
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    from handlers.start import callback_target

    await callback_target(callback).answer(text, reply_markup=markup)


@op_router.callback_query(F.data == "op:check")
async def cb_op_check(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    gate = await op_service.authorize(callback.bot, callback.from_user)
    await callback.answer()
    from handlers.start import callback_target, enter_bot

    target = callback_target(callback)
    if not gate.blocked:
        text = op_service.tr(
            op_service.OK_TEXT if gate.required else op_service.OFF_TEXT, lang
        )
        message = callback.message
        edited = False
        if message is not None:
            try:
                await message.edit_text(text)
                edited = True
            except TelegramBadRequest:
                pass
        if not edited:
            await target.answer(text)
        await enter_bot(target, state, callback.from_user, callback.bot)
        return
    await _edit_requirement(callback, gate, lang)

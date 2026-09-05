from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from handlers import help_router
import config

HELP_TEXT = "<emoji document_id=\"5312464908351201524\">❓</emoji> Помощь\n\n/start — открыть бота\n/search — найти собеседника\n/stop — завершить диалог\n/profile — мой профиль\n/report — пожаловаться\n/rules — правила"
if config.SUPPORT_USERNAME:
    HELP_TEXT += f"\n\nПоддержка: @{config.SUPPORT_USERNAME.lstrip('@')}"


@help_router.message(Command("help"))
@help_router.message(F.text == "<emoji document_id=\"5312464908351201524\">❓</emoji> Помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@help_router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(HELP_TEXT)

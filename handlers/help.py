from aiogram import F
from aiogram.types import Message, CallbackQuery

from handlers import help_router
from keyboards.inline import help_kb

HELP_TEXT = """❓ Помощь

/start — перезапустить бота
/search — найти собеседника
/stop — завершить диалог
/profile — мой профиль
/report — пожаловаться
/rules — правила

По вопросам: @support_username"""


@help_router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    await callback.message.edit_text(HELP_TEXT, reply_markup=help_kb())
    await callback.answer()


@help_router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=help_kb())


@help_router.message(F.command("rules"))
async def cmd_rules(message: Message):
    from handlers.start import RULES_TEXT
    await message.answer(RULES_TEXT)


@help_router.message(F.command("search"))
async def cmd_search(message: Message):
    from keyboards.inline import search_kb
    await message.answer("🔍 Начинаем поиск...", reply_markup=search_kb())

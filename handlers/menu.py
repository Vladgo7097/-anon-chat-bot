from aiogram import F
from aiogram.types import Message

from handlers import menu_router
from keyboards.reply import main_menu_kb
from services.chat_manager import set_online


@menu_router.message(F.text == "<emoji document_id=\"5257963315258204021\">🏠</emoji> Меню")
async def cmd_menu(message: Message):
    await set_online(message.from_user.id)
    await message.answer(
        "<emoji document_id=\"5257963315258204021\">🏠</emoji> Главное меню\n\nСтатус: <emoji document_id=\"5215522595922779944\">🟢</emoji> Онлайн",
        reply_markup=main_menu_kb(),
    )

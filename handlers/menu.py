from aiogram import F
from aiogram.types import Message

from handlers import menu_router
from keyboards.reply import main_menu_kb
from services.chat_manager import set_online


@menu_router.message(F.text == "Меню")
async def cmd_menu(message: Message):
    await set_online(message.from_user.id)
    await message.answer(
        "<tg-emoji emoji-id=\"5257963315258204021\">🏠</tg-emoji> Главное меню\n\nСтатус: <tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Онлайн",
        reply_markup=main_menu_kb(),
    )

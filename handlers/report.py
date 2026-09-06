from aiogram import F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramForbiddenError
from handlers import report_router
from services.chats import ChatService
from services.reports import ReportService, REASONS
from keyboards import texts as T
from keyboards.reply import main_menu_kb


class ReportFlow(StatesGroup):
    DETAILS=State()


@report_router.message(Command("report"))
@report_router.message(F.text == T.REPORT)
async def cmd_report(message: Message):
    chat = await ChatService().active(message.from_user.id)
    if not chat:
        await message.answer("Жалоба доступна во время активного диалога.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"v1:report:{chat.session_id}:{reason}", style="danger")
    ] for reason, label in REASONS.items()])
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> На что жалуетесь?", reply_markup=keyboard)


@report_router.callback_query(F.data.startswith("v1:report:"))
async def cb_report(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Кнопка недоступна.")
        return
    if parts[3]=="other":
        await state.set_state(ReportFlow.DETAILS)
        await state.update_data(report_session=parts[2])
        await callback.answer()
        await callback.message.answer("Опиши причину жалобы (до 1000 символов). /cancel — отменить.")
        return
    result = await ReportService().submit(callback.from_user.id, parts[2], parts[3])
    await callback.answer()
    if not result.ok:
        await callback.message.answer("Жалоба уже отправлена или диалог завершён.")
        return
    await callback.message.edit_text("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Жалоба отправлена модераторам.\nСобеседник заблокирован для тебя.")
    try:
        await callback.bot.send_message(result.data["partner"], "<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> Собеседник покинул чат.", reply_markup=main_menu_kb())
    except TelegramForbiddenError:
        pass
    from handlers.search import begin_search
    await begin_search(callback.message, callback.from_user.id)


@report_router.message(ReportFlow.DETAILS)
async def report_details(message: Message,state: FSMContext):
    if message.text=="/cancel" or message.text in T.SYSTEM_TEXTS:
        await state.clear()
        await message.answer("Ввод жалобы отменён.")
        return
    if not message.text or not 1<=len(message.text)<=1000:
        await message.answer("Опиши причину текстом от 1 до 1000 символов.")
        return
    sid=(await state.get_data()).get("report_session")
    result=await ReportService().submit(message.from_user.id,sid,"other",message.text)
    await state.clear()
    if not result.ok:
        await message.answer("Жалоба уже отправлена или диалог завершён.")
        return
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Жалоба отправлена модераторам. Собеседник заблокирован для тебя.")
    try:
        await message.bot.send_message(result.data["partner"],"<tg-emoji emoji-id=\"5927208490870248283\">😢</tg-emoji> Собеседник покинул чат.",reply_markup=main_menu_kb())
    except TelegramForbiddenError:
        pass
    from handlers.search import begin_search
    await begin_search(message,message.from_user.id)


@report_router.callback_query(F.data.startswith("report:"))
async def cb_old_report(callback: CallbackQuery):
    await callback.answer("Открой новую жалобу кнопкой «Пожаловаться».")

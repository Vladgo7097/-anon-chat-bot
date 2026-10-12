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
from utils.db import user_lang


class ReportFlow(StatesGroup):
    DETAILS=State()


async def _partner_notice(bot, partner_id, key):
    """The peer hears "the chat ended" in their own language, not the
    complainer's -- the two are rarely the same client."""
    lang = await user_lang(partner_id)
    try:
        await bot.send_message(partner_id, T.tr(T.REPORT_TEXT, lang)[key],
                               reply_markup=main_menu_kb(lang))
    except TelegramForbiddenError:
        pass


@report_router.message(Command("report"))
@report_router.message(F.text.in_(T.all_labels(T.REPORT)))
async def cmd_report(message: Message, lang: str = "ru"):
    text = T.tr(T.REPORT_TEXT, lang)
    chat = await ChatService().active(message.from_user.id)
    if not chat:
        await message.answer(text["not_in_chat"])
        return
    labels = T.tr(T.REPORT_REASONS, lang)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=labels.get(reason, label),
                             callback_data=f"v1:report:{chat.session_id}:{reason}", style="danger")
    ] for reason, label in REASONS.items()])
    await message.answer(text["ask"], reply_markup=keyboard)


@report_router.callback_query(F.data.startswith("v1:report:"))
async def cb_report(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    text = T.tr(T.REPORT_TEXT, lang)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer(text["button_unavailable"])
        return
    if parts[3]=="other":
        await state.set_state(ReportFlow.DETAILS)
        await state.update_data(report_session=parts[2])
        await callback.answer()
        await callback.message.answer(text["describe"])
        return
    result = await ReportService().submit(callback.from_user.id, parts[2], parts[3])
    await callback.answer()
    if not result.ok:
        await callback.message.answer(text["already"])
        return
    await callback.message.edit_text(text["sent"])
    await _partner_notice(callback.bot, result.data["partner"], "partner_left")
    from handlers.search import begin_search
    await begin_search(callback.message, callback.from_user.id)


@report_router.message(ReportFlow.DETAILS)
async def report_details(message: Message,state: FSMContext, lang: str = "ru"):
    text = T.tr(T.REPORT_TEXT, lang)
    if message.text=="/cancel" or message.text in T.SYSTEM_TEXTS:
        await state.clear()
        await message.answer(text["cancelled"])
        return
    if not message.text or not 1<=len(message.text)<=1000:
        await message.answer(text["describe_range"])
        return
    sid=(await state.get_data()).get("report_session")
    result=await ReportService().submit(message.from_user.id,sid,"other",message.text)
    await state.clear()
    if not result.ok:
        await message.answer(text["already"])
        return
    await message.answer(text["sent"])
    await _partner_notice(message.bot, result.data["partner"], "partner_left")
    from handlers.search import begin_search
    await begin_search(message,message.from_user.id)


@report_router.callback_query(F.data.startswith("report:"))
async def cb_old_report(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer(T.tr(T.REPORT_TEXT, lang)["old"])

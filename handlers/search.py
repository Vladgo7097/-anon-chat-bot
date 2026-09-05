import asyncio
import time
from aiogram import F
from aiogram.types import CallbackQuery, LabeledPrice

from handlers import search_router
from keyboards.inline import search_kb, menu_kb
from keyboards.reply import chat_kb_reply
from utils.db import get_or_create_user, increment_chats, check_ban, is_premium
from services.chat_manager import (
    join_search, leave_search, search_with_timeout,
    start_chat, set_online, get_search_queue_length,
    get_online_count, set_instant_search,
)
import config


@search_router.callback_query(F.data == "search:start")
async def cb_search_start(callback: CallbackQuery):
    user = callback.from_user
    if await check_ban(user.id):
        await callback.answer("⛔ Ты заблокирован.", show_alert=True)
        return

    await set_online(user.id)
    db_user = await get_or_create_user(user.id)

    gender = db_user.settings_gender_filter if db_user else "a"
    await join_search(user.id, gender)

    online = await get_online_count()
    queue_len = await get_search_queue_length()

    await callback.message.edit_text(
        f"🔎 Ищу собеседника...\n\n"
        f"⏱ Очередь: {queue_len} чел.\n"
        f"👥 Онлайн сейчас: {online}\n\n"
        f"Это может занять до минуты",
        reply_markup=search_kb(),
    )
    await callback.answer()

    asyncio.create_task(_search_loop(callback, user.id, db_user))


async def _search_loop(callback: CallbackQuery, user_id: int, db_user):
    start = time.time()
    dots = 0
    show_instant_after = 20

    while time.time() - start < config.SEARCH_TIMEOUT:
        await asyncio.sleep(2)

        from services.chat_manager import get_partner
        if await get_partner(user_id):
            return

        partner_id = await search_with_timeout(user_id, db_user.gender if db_user else None)
        if partner_id:
            await start_chat(user_id, partner_id)
            await increment_chats(user_id)
            await increment_chats(partner_id)

            try:
                await callback.message.edit_text("✅ Собеседник найден!\n\nПишите первым! 👋")
                await callback.message.answer(
                    "✅ Собеседник найден!\n\nПишите первым! 👋",
                    reply_markup=chat_kb_reply(),
                )
            except Exception:
                pass

            try:
                await callback.bot.send_message(
                    partner_id,
                    "✅ Собеседник найден!\n\nПишите первым! 👋",
                    reply_markup=chat_kb_reply(),
                )
            except Exception:
                pass
            return

        dots = (dots + 1) % 4
        dots_str = "." * dots if dots > 0 else ""
        elapsed = int(time.time() - start)
        minutes = elapsed // 60
        seconds = elapsed % 60
        time_str = f"{minutes:02d}:{seconds:02d}"

        online = await get_online_count()

        kb = search_kb()
        if elapsed > show_instant_after and not await is_premium(user_id):
            from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отменить поиск", callback_data="search:cancel")],
                [InlineKeyboardButton(text=f"⚡ Найти мгновенно за {config.STARS_INSTANT_SEARCH}⭐", callback_data="search:instant")],
            ])

        try:
            await callback.message.edit_text(
                f"🔎 Ищу собеседника{dots_str}\n\n"
                f"⏱ Прошло: {time_str}\n"
                f"👥 Онлайн сейчас: {online}\n\n"
                f"Это может занять до минуты",
                reply_markup=kb,
            )
        except Exception:
            pass

    await leave_search(user_id)
    try:
        await callback.message.edit_text(
            "⏳ Пока никого нет. Расширить критерии поиска?",
            reply_markup=menu_kb(),
        )
    except Exception:
        pass


@search_router.callback_query(F.data == "search:cancel")
async def cb_search_cancel(callback: CallbackQuery):
    await leave_search(callback.from_user.id)
    await callback.message.edit_text(
        "🏠 Главное меню\n\nСтатус: 🟢 Онлайн",
        reply_markup=menu_kb(),
    )
    await callback.answer("Поиск отменён")


@search_router.callback_query(F.data == "search:instant")
async def cb_search_instant(callback: CallbackQuery):
    user = callback.from_user
    premium = await is_premium(user.id)
    if premium:
        await callback.answer("У тебя Premium — поиск и так мгновенный!", show_alert=True)
        return

    await callback.bot.send_invoice(
        chat_id=user.id,
        title="Мгновенный поиск",
        description="Найти собеседника мгновенно, минуя очередь",
        payload=f"instant_search:{user.id}",
        currency="XTR",
        prices=[LabeledPrice(label="Мгновенный поиск", amount=config.STARS_INSTANT_SEARCH)],
    )
    await callback.answer()

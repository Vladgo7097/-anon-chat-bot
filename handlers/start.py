from aiogram import F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command

from handlers import start_router
from keyboards.inline import start_kb, rules_kb, menu_kb
from keyboards.reply import main_menu_kb
from utils.db import get_or_create_user, check_ban
from services.chat_manager import set_online

WELCOME_TEXT = """👋 Привет! Это анонимный чат-бот.

Здесь ты можешь общаться с случайными людьми,
не раскрывая своей личности 🎭

Жми «Начать поиск» — и через пару секунд найдём собеседника"""

RULES_TEXT = """📜 Правила чата

1️⃣ Запрещена реклама и спам
2️⃣ Запрещены оскорбления и травля
3️⃣ Запрещён контент 18+ и насилие
4️⃣ Не разглашай личные данные (свои и чужие)
5️⃣ Жалуйся на нарушителей кнопкой 🚩

Нарушение → бан от 1 дня до навсегда ⛔"""


@start_router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    db_user = await get_or_create_user(
        user.id,
        username=user.username,
        first_name=user.first_name,
    )

    if await check_ban(user.id):
        await message.answer("⛔ Ты заблокирован за нарушение правил.")
        return

    await set_online(user.id)

    # Process referral
    if message.text and len(message.text.split()) > 1:
        ref = message.text.split()[1]
        if ref.startswith("ref_"):
            try:
                ref_id = int(ref[4:])
                if ref_id != user.id:
                    from handlers.referral import process_referral
                    await process_referral(user.id, ref_id)
            except ValueError:
                pass

    # Streak check
    from handlers.streaks import check_and_update_streak
    streak_info = await check_and_update_streak(user.id)
    if streak_info["reward"]:
        r = streak_info["reward"]
        await message.answer(
            f"🔥 Стрик {streak_info['streak']} дней!\n\n"
            f"Ты получил скидку {r['discount']}% {r['badge']}\n"
            f"Скидка применится автоматически к следующей покупке."
        )

    await message.answer(WELCOME_TEXT, reply_markup=start_kb())


@start_router.callback_query(F.data == "rules")
async def cb_rules(callback: CallbackQuery):
    await callback.message.edit_text(RULES_TEXT, reply_markup=rules_kb())
    await callback.answer()


@start_router.callback_query(F.data == "rules:ok")
async def cb_rules_ok(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню\n\nСтатус: 🟢 Онлайн", reply_markup=menu_kb())
    await callback.answer()


@start_router.callback_query(F.data == "back:start")
async def cb_back_start(callback: CallbackQuery):
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=start_kb())
    await callback.answer()


@start_router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    await callback.message.edit_text("🏠 Главное меню\n\nСтатус: 🟢 Онлайн", reply_markup=menu_kb())
    await callback.answer()

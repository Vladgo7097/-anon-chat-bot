from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, MessageSuccessfulPayment,
)

from handlers import premium_router
from keyboards.inline import premium_kb, menu_kb
from keyboards.reply import main_menu_kb
from utils.db import get_or_create_user, activate_premium, is_premium, add_payment
import config


PREMIUM_TEXT = """⭐ Premium-подписка

Что даёт Premium:
🚫 Без рекламы
🎯 Фильтр по полу и возрасту
👀 Видеть, лайкнул ли тебя собеседник
👤 Раскрытие собеседника без взаимности — бесплатно
⚡ Мгновенный поиск без очереди
🔄 «Следующий» без задержки
🖼 До 3 фото в профиле

💰 Тарифы:"""


@premium_router.callback_query(F.data == "premium")
async def cb_premium(callback: CallbackQuery):
    premium = await is_premium(callback.from_user.id)
    db_user = await get_or_create_user(callback.from_user.id)

    text = PREMIUM_TEXT
    if premium and db_user.premium_expires:
        text += f"\n\n✅ Твой Premium активен до {db_user.premium_expires.strftime('%d.%m.%Y')}"
    else:
        text += f"\n\n💡 У тебя пока нет Premium"

    await callback.message.edit_text(text, reply_markup=premium_kb())
    await callback.answer()


@premium_router.message(F.text == "⭐ Premium")
async def cmd_premium(message: Message):
    premium = await is_premium(message.from_user.id)
    db_user = await get_or_create_user(message.from_user.id)

    text = PREMIUM_TEXT
    if premium and db_user.premium_expires:
        text += f"\n\n✅ Твой Premium активен до {db_user.premium_expires.strftime('%d.%m.%Y')}"

    await message.answer(text, reply_markup=premium_kb())


@premium_router.callback_query(F.data.startswith("premium:buy:"))
async def cb_premium_buy(callback: CallbackQuery):
    plan = callback.data.split(":")[-1]
    prices_map = {
        "1m": config.STARS_PREMIUM_1M,
        "3m": config.STARS_PREMIUM_3M,
        "12m": config.STARS_PREMIUM_12M,
    }
    labels_map = {
        "1m": "Premium 1 месяц",
        "3m": "Premium 3 месяца",
        "12m": "Premium 12 месяцев",
    }

    stars = prices_map.get(plan, config.STARS_PREMIUM_1M)
    label = labels_map.get(plan, "Premium")

    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=label,
        description=f"Premium-подписка: {label}",
        payload=f"premium:{plan}:{callback.from_user.id}",
        currency="XTR",
        prices=[LabeledPrice(label=label, amount=stars)],
    )
    await callback.answer()


# ── Soft upsell (every 3rd stop, max 1x/day) ────────────────────

_upsell_counter: dict[int, int] = {}
_upsell_last_shown: dict[int, int] = {}

async def maybe_show_upsell(bot, user_id: int) -> bool:
    """Show premium upsell after every 3rd stop, max 1x/day. Returns True if shown."""
    import time
    now = int(time.time())
    today_start = now - (now % 86400)

    last_shown = _upsell_last_shown.get(user_id, 0)
    if last_shown > today_start:
        return False  # Already shown today

    count = _upsell_counter.get(user_id, 0) + 1
    _upsell_counter[user_id] = count

    if count >= 3:
        _upsell_counter[user_id] = 0
        _upsell_last_shown[user_id] = now

        from keyboards.inline import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Попробовать Premium — 99⭐/мес", callback_data="premium:buy:1m")],
            [InlineKeyboardButton(text="Не сейчас", callback_data="menu")],
        ])
        try:
            await bot.send_message(
                user_id,
                "🔥 Тебе нравится общаться в боте?\n\n"
                "С Premium ты получишь:\n"
                "⚡ Мгновенный поиск без очереди\n"
                "🎯 Фильтр по полу и возрасту\n"
                "🚫 Без задержек и рекламы",
                reply_markup=kb,
            )
            return True
        except Exception:
            pass
    return False


# ── Stars payment handlers ───────────────────────────────────────

@premium_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    payload = query.invoice_payload
    if payload.startswith("premium:") or payload.startswith("gender_filter:") or payload.startswith("reveal:"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Неизвестный товар")


@premium_router.message(F.successful_payment)
async def successful_payment(message: Message):
    payment: MessageSuccessfulPayment = message.successful_payment
    payload = payment.invoice_payload
    user_id = message.from_user.id

    if payload.startswith("premium:"):
        parts = payload.split(":")
        plan = parts[1]
        await add_payment(user_id, f"premium_{plan}", payment.total_amount, payment.telegram_payment_id)
        await activate_premium(user_id, plan, payment.total_amount)
        await message.answer(
            f"🎉 Premium активирован!\n\n"
            f"Тариф: {plan}\n"
            f"Оплачено: {payment.total_amount}⭐\n\n"
            f"Все преимущества доступны сразу!",
            reply_markup=menu_kb(),
        )
    elif payload.startswith("gender_filter:"):
        await add_payment(user_id, "gender_filter", payment.total_amount, payment.telegram_payment_id)
        from utils.db import update_user
        await update_user(user_id, settings_gender_filter="a")  # Reset to all, now they can choose
        await message.answer(
            "✅ Фильтр по полу разблокирован!\n\n"
            "Теперь ты можешь выбирать кого искать в настройках.",
            reply_markup=menu_kb(),
        )
    else:
        await message.answer("Платёж получен. Спасибо!", reply_markup=menu_kb())

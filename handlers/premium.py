import math

from aiogram import F
from aiogram.types import (
    Message,
    CallbackQuery,
    PreCheckoutQuery,
    LabeledPrice,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from handlers import premium_router
from services.payments import PaymentService
from keyboards import texts as T

PREMIUM_TITLE = '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> <b>Premium</b>'
PREMIUM_BENEFITS = {
    "ru": (
        PREMIUM_TITLE + "\n"
        "Разовый платёж. Автопродления нет.\n\n"
        "🚫 Без рекламы\n"
        "🎯 Фильтры: пол и возраст\n"
        "⚡ Приоритет в очереди\n"
        "🔄 «Следующий» без паузы\n"
        "❤️ Приоритетные запросы — без доплаты\n"
        "🖼 3 фото вместо 1"
    ),
    "en": (
        PREMIUM_TITLE + "\n"
        "One-time payment. No auto-renewal.\n\n"
        "🚫 No ads\n"
        "🎯 Gender and age filters\n"
        "⚡ Priority in the queue\n"
        "🔄 “Next” without the wait\n"
        "❤️ Priority requests — no extra charge\n"
        "🖼 3 photos instead of 1"
    ),
    "uk": (
        PREMIUM_TITLE + "\n"
        "Разовий платіж. Автопродовження немає.\n\n"
        "🚫 Без реклами\n"
        "🎯 Фільтри: стать і вік\n"
        "⚡ Пріоритет у черзі\n"
        "🔄 «Наступний» без паузи\n"
        "❤️ Пріоритетні запити — без доплати\n"
        "🖼 3 фото замість 1"
    ),
}
ACTIVE_PREMIUM = {
    "ru": (
        PREMIUM_TITLE + " активен\n"
        "До {date} · осталось {left}\n\n"
        "Включено: фильтры, приоритет, без рекламы, 3 фото.\n"
        "Продление добавит время к текущему сроку — дни не сгорят."
    ),
    "en": (
        PREMIUM_TITLE + " active\n"
        "Until {date} · {left} left\n\n"
        "Included: filters, priority, no ads, 3 photos.\n"
        "Renewing adds time to the current term — no days are lost."
    ),
    "uk": (
        PREMIUM_TITLE + " активний\n"
        "До {date} · залишилось {left}\n\n"
        "Включено: фільтри, пріоритет, без реклами, 3 фото.\n"
        "Продовження додасть час до поточного терміну — дні не згорять."
    ),
}
EXPIRED_PREMIUM = {
    "ru": (
        PREMIUM_TITLE + " закончился\n"
        "Доступ завершился {date}.\n\n"
        "Верни фильтры, приоритет и режим без рекламы."
    ),
    "en": (
        PREMIUM_TITLE + " expired\n"
        "Access ended on {date}.\n\n"
        "Bring back filters, priority, and the ad-free mode."
    ),
    "uk": (
        PREMIUM_TITLE + " закінчився\n"
        "Доступ завершився {date}.\n\n"
        "Поверни фільтри, пріоритет і режим без реклами."
    ),
}
CHOOSE_TERM = {
    "ru": "\n\nВыбери срок:",
    "en": "\n\nChoose a term:",
    "uk": "\n\nОбери термін:",
}
CHOOSE_RENEW = {
    "ru": "\n\nВыбери продление:",
    "en": "\n\nChoose a renewal:",
    "uk": "\n\nОбери продовження:",
}
DISCOUNT_NOTE = {
    "ru": "\n\n🎁 Скидка до {pct}% — итоговая цена уже в кнопке.",
    "en": "\n\n🎁 Up to {pct}% off — the final price is already on the button.",
    "uk": "\n\n🎁 Знижка до {pct}% — підсумкова ціна вже в кнопці.",
}
RENEW_DISCOUNT_NOTE = {
    "ru": "\n\n🎁 На продление действует скидка до {pct}%.",
    "en": "\n\n🎁 Renewal discount: up to {pct}% off.",
    "uk": "\n\n🎁 На продовження діє знижка до {pct}%.",
}
PLAN_LABELS = {
    "premium_1m": {"ru": "1 месяц", "en": "1 month", "uk": "1 місяць"},
    "premium_3m": {"ru": "3 месяца", "en": "3 months", "uk": "3 місяці"},
    "premium_12m": {"ru": "12 месяцев", "en": "12 months", "uk": "12 місяців"},
}
PLAN_MONTHS = {"premium_1m": 1, "premium_3m": 3, "premium_12m": 12}
INSTEAD_OF = {"ru": "вместо", "en": "was", "uk": "замість"}
PER_MONTH = {"ru": "мес", "en": "mo", "uk": "міс"}
INVOICE_DESC = {
    "ru": "Разовый платёж за Premium. Приоритет не гарантирует наличие подходящего собеседника; контакты требуют взаимного согласия. Не хватает звёзд — Telegram сам предложит купить их на этом экране.",
    "en": "One-time Premium payment. Priority doesn't guarantee a matching partner is available; contacts still require mutual consent. Short on Stars — Telegram will offer to buy more right on this screen.",
    "uk": "Разовий платіж за Premium. Пріоритет не гарантує наявність відповідного співрозмовника; контакти потребують взаємної згоди. Не вистачає зірок — Telegram сам запропонує купити їх на цьому екрані.",
}
CHOOSE_PLAN = {
    "ru": "Выбери тариф в обновлённом меню.",
    "en": "Pick a plan from the updated menu.",
    "uk": "Обери тариф в оновленому меню.",
}
PAYMENT_STALE = {
    "ru": "Счёт устарел или недействителен. Создай новый счёт в боте.",
    "en": "The invoice is stale or invalid. Create a new invoice in the bot.",
    "uk": "Рахунок застарів або недійсний. Створи новий рахунок у боті.",
}
ALREADY_CREDITED = {
    "ru": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Этот платёж уже учтён.',
    "en": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> This payment is already credited.',
    "uk": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Цей платіж вже враховано.',
}
NEEDS_SUPPORT = {
    "ru": "Платёж требует проверки. Обратись в поддержку через /help.",
    "en": "This payment needs a review. Contact support via /help.",
    "uk": "Платіж потребує перевірки. Звернись до підтримки через /help.",
}
PAYMENT_DONE = {
    "ru": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Оплата получена. Возможность активирована.',
    "en": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Payment received. The feature is now active.',
    "uk": '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Оплату отримано. Можливість активовано.',
}
REFUND_DONE = {
    "ru": "Возврат учтён. Доступ по возвращённой покупке отключён.",
    "en": "Refund processed. Access from the refunded purchase has been turned off.",
    "uk": "Повернення враховано. Доступ за поверненою покупкою вимкнено.",
}
PURCHASE_UNAVAILABLE = {
    "ru": "Покупка уже оплачена или больше недоступна.",
    "en": "Already paid, or no longer available.",
    "uk": "Вже оплачено або більше недоступно.",
}
INVOICE_PENDING = {
    "ru": "Счёт уже отправлен выше или отправляется. Если он не появился, попробуй ещё раз через 30 секунд.",
    "en": "The invoice was already sent above, or is being sent. If it didn't show up, try again in 30 seconds.",
    "uk": "Рахунок вже надіслано вище або надсилається. Якщо не з'явився, спробуй ще раз за 30 секунд.",
}
PRODUCT_UNAVAILABLE = {
    "ru": "Товар недоступен.",
    "en": "Item unavailable.",
    "uk": "Товар недоступний.",
}


def _days_phrase(days, lang):
    if lang == "en":
        return f"{days} day" if days == 1 else f"{days} days"
    if lang == "uk":
        if days % 10 == 1 and days % 100 != 11:
            word = "день"
        elif days % 10 in {2, 3, 4} and days % 100 not in {12, 13, 14}:
            word = "дні"
        else:
            word = "днів"
    else:
        if days % 10 == 1 and days % 100 != 11:
            word = "день"
        elif days % 10 in {2, 3, 4} and days % 100 not in {12, 13, 14}:
            word = "дня"
        else:
            word = "дней"
    return f"{days} {word}"


async def _active_discounts(user_id, codes):
    """Best active offer per plan, matching PaymentService.create()'s scopes."""
    from sqlalchemy import select

    from models.base import async_session
    from models.operations import Offer
    from utils.time import utcnow

    now = utcnow()
    async with async_session() as session:
        offers = list(
            (
                await session.scalars(
                    select(Offer).where(
                        Offer.user_id == user_id,
                        Offer.status == "active",
                        Offer.starts_at <= now,
                        Offer.ends_at > now,
                        Offer.product_scope.in_(["any", "premium", *codes]),
                    )
                )
            ).all()
        )
    return {
        code: max(
            offer.discount_percent
            for offer in offers
            if offer.product_scope in {"any", "premium", code}
        )
        for code in codes
        if any(offer.product_scope in {"any", "premium", code} for offer in offers)
    }


async def product_keyboard(codes, session_id=None):
    products = await PaymentService().products(codes)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{p.title} — {p.price_stars}",
                    icon_custom_emoji_id="5920281855378068765",
                    style="primary",
                    callback_data=f"v1:buy:{p.code}"
                    + (f":{session_id}" if session_id else ""),
                )
            ]
            for p in products
        ]
    )


async def premium_keyboard(discounts=None, lang="ru"):
    discounts = discounts or {}
    products = await PaymentService().products(list(PLAN_LABELS))
    rows = []
    for product in products:
        plan = T.tr(PLAN_LABELS.get(product.code, {"ru": product.title}), lang)
        price = product.price_stars
        discount = discounts.get(product.code, 0)
        if discount:
            price = max(1, product.price_stars * (100 - discount) // 100)
            label = (
                f"{plan} · {price} ⭐ {T.tr(INSTEAD_OF, lang)} {product.price_stars}"
            )
        else:
            months = PLAN_MONTHS.get(product.code, 1)
            if months > 1:
                label = (
                    f"{plan} · {price} ⭐ · {round(price / months)} "
                    f"⭐/{T.tr(PER_MONTH, lang)}"
                )
            else:
                label = f"{plan} · {price} ⭐"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    icon_custom_emoji_id="5920281855378068765",
                    style="success" if product.code == "premium_3m" else "primary",
                    callback_data=f"v1:buy:{product.code}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def show_premium(message, user_id, lang="ru"):
    from repositories.users import UserRepository
    from services.growth import first_purchase_discount
    from services.premium import premium_active
    from utils.time import utcnow

    user = await UserRepository().get(user_id)
    now = utcnow()
    if user and premium_active(user, now):
        left = max(1, math.ceil((user.premium_expires - now).total_seconds() / 86400))
        text = T.tr(ACTIVE_PREMIUM, lang).format(
            date=f"{user.premium_expires:%d.%m.%Y}",
            left=_days_phrase(left, lang),
        )
        state = "active"
    elif user and user.premium_expires and user.premium_expires <= now:
        text = T.tr(EXPIRED_PREMIUM, lang).format(
            date=f"{user.premium_expires:%d.%m.%Y}"
        )
        state = "expired"
    else:
        text = T.tr(PREMIUM_BENEFITS, lang)
        state = "new"
    if state != "active":
        await first_purchase_discount(user_id)
    discounts = await _active_discounts(user_id, tuple(PLAN_LABELS))
    if discounts:
        note = RENEW_DISCOUNT_NOTE if state == "active" else DISCOUNT_NOTE
        text += T.tr(note, lang).format(pct=max(discounts.values()))
    text += T.tr(CHOOSE_RENEW if state == "active" else CHOOSE_TERM, lang)
    await message.answer(
        text,
        reply_markup=await premium_keyboard(discounts, lang),
    )


@premium_router.message(F.text.in_(T.all_labels(T.PREMIUM)))
async def cmd_premium(message: Message, lang: str = "ru"):
    await show_premium(message, message.from_user.id, lang)


@premium_router.callback_query(F.data == "premium")
async def cb_premium(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer()
    await show_premium(callback.message, callback.from_user.id, lang)


@premium_router.callback_query(F.data.startswith("v1:buy:"))
async def cb_buy(callback: CallbackQuery, lang: str = "ru"):
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        await callback.answer(T.tr(PRODUCT_UNAVAILABLE, lang))
        return
    result = await PaymentService().create(
        callback.from_user.id, parts[2], parts[3] if len(parts) == 4 else None
    )
    await callback.answer()
    if not result.ok:
        await callback.message.answer(T.tr(PURCHASE_UNAVAILABLE, lang))
        return
    p = result.data["payment"]
    if not await PaymentService().claim_invoice(callback.from_user.id, p.id):
        await callback.message.answer(T.tr(INVOICE_PENDING, lang))
        return
    invoice = await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title=result.data["title"],
        description=T.tr(INVOICE_DESC, lang),
        payload=p.payload,
        currency="XTR",
        prices=[LabeledPrice(label=result.data["title"], amount=p.stars_amount)],
    )
    await PaymentService().invoice_sent(p.id, invoice.message_id)
    if p.product == "instant_search":
        from services.growth import track

        await track(
            callback.from_user.id, "instant_search_invoice_created", f"invoice:{p.id}"
        )


@premium_router.callback_query(F.data.startswith("premium:buy:"))
async def cb_legacy_buy(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer(T.tr(CHOOSE_PLAN, lang))
    await show_premium(callback.message, callback.from_user.id, lang)


@premium_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    ok = await PaymentService().precheckout(
        query.from_user.id,
        query.invoice_payload,
        query.total_amount,
        query.currency,
        query.id,
    )
    await query.answer(
        ok=ok, **({} if ok else {"error_message": T.tr(PAYMENT_STALE, "ru")})
    )


@premium_router.message(F.successful_payment)
async def successful_payment(message: Message, lang: str = "ru"):
    p = message.successful_payment
    result = await PaymentService().complete(
        message.from_user.id,
        p.invoice_payload,
        p.total_amount,
        p.currency,
        p.telegram_payment_charge_id,
    )
    if result.code == "ALREADY_PAID":
        await message.answer(T.tr(ALREADY_CREDITED, lang))
        return
    if not result.ok:
        import logging

        logging.getLogger(__name__).error(
            "event=payment_validation_failed user_id=%s", message.from_user.id
        )
        await message.answer(T.tr(NEEDS_SUPPORT, lang))
        return
    await message.answer(T.tr(PAYMENT_DONE, lang))
    if result.data["product"] == "instant_search":
        from services.chat_manager import set_instant_search

        await set_instant_search(message.from_user.id)
    if result.data["product"] in {"reveal_priority_request", "reveal_status"}:
        from handlers.reveal import paid_reveal

        await paid_reveal(
            message,
            message.from_user.id,
            result.data["session_id"],
            result.data["product"],
            lang,
        )


@premium_router.message(F.refunded_payment)
async def refunded_payment(message: Message, lang: str = "ru"):
    p = message.refunded_payment
    result = await PaymentService().refunded(
        message.from_user.id,
        p.invoice_payload,
        p.total_amount,
        p.currency,
        p.telegram_payment_charge_id,
    )
    if result.ok:
        await message.answer(T.tr(REFUND_DONE, lang))

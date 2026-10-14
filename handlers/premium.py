from aiogram import F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from handlers import premium_router
from services.payments import PaymentService
from keyboards import texts as T

PREMIUM_TEXT = {
    "ru": (
        '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium-подписка\n\n'
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Бесплатно уже сейчас: поиск, переписка, фото, контакты только по взаимному согласию.\n\n'
        'Premium добавляет:\n'
        '<tg-emoji emoji-id="5976560705938133794">🎯</tg-emoji> Фильтры по полу и возрасту\n'
        '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Приоритетные запросы на взаимное раскрытие — без доплаты\n'
        '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Приоритет в очереди поиска\n'
        '<tg-emoji emoji-id="5260450573768990626">🔄</tg-emoji> «Следующий» без задержки\n'
        '<tg-emoji emoji-id="5258165702707125574">🖼</tg-emoji> До 3 фото в профиле\n\n'
        '<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Тарифы:'
    ),
    "en": (
        '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium subscription\n\n'
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Already free: search, chat, photos, contacts revealed only by mutual consent.\n\n'
        'Premium adds:\n'
        '<tg-emoji emoji-id="5976560705938133794">🎯</tg-emoji> Gender and age filters\n'
        '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Priority reveal requests — no extra charge\n'
        '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Priority in the search queue\n'
        '<tg-emoji emoji-id="5260450573768990626">🔄</tg-emoji> No delay on "Next"\n'
        '<tg-emoji emoji-id="5258165702707125574">🖼</tg-emoji> Up to 3 profile photos\n\n'
        '<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Plans:'
    ),
    "uk": (
        '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium-підписка\n\n'
        '<tg-emoji emoji-id="5280880410346152584">✅</tg-emoji> Безкоштовно вже зараз: пошук, переписка, фото, контакти лише за взаємною згодою.\n\n'
        'Premium додає:\n'
        '<tg-emoji emoji-id="5976560705938133794">🎯</tg-emoji> Фільтри за статтю та віком\n'
        '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Пріоритетні запити на взаємне розкриття — без доплати\n'
        '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Пріоритет у черзі пошуку\n'
        '<tg-emoji emoji-id="5260450573768990626">🔄</tg-emoji> «Наступний» без затримки\n'
        '<tg-emoji emoji-id="5258165702707125574">🖼</tg-emoji> До 3 фото в профілі\n\n'
        '<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Тарифи:'
    ),
}
ACTIVE_UNTIL = {"ru": "\n\n<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Активен до {date}",
                "en": "\n\n<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Active until {date}",
                "uk": "\n\n<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Активний до {date}"}
FIRST_DISCOUNT = {
    "ru": "\n\n<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Первая подписка — со скидкой {pct}%. Цена в счёте уже с ней.",
    "en": "\n\n<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> First subscription — {pct}% off. The invoice already shows the discounted price.",
    "uk": "\n\n<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Перша підписка — знижка {pct}%. Ціна в рахунку вже з нею.",
}
INVOICE_DESC = {
    "ru": "Улучшение анонимного чата. Приоритет не гарантирует наличие подходящего собеседника; контакты требуют взаимного согласия. Не хватает звёзд — Telegram сам предложит купить их на этом экране.",
    "en": "Upgrade for the anonymous chat. Priority doesn't guarantee a matching partner is available; contacts still require mutual consent. Short on Stars — Telegram will offer to buy more right on this screen.",
    "uk": "Покращення анонімного чату. Пріоритет не гарантує наявність відповідного співрозмовника; контакти потребують взаємної згоди. Не вистачає зірок — Telegram сам запропонує купити їх на цьому екрані.",
}
CHOOSE_PLAN = {"ru": "Выбери тариф в обновлённом меню.", "en": "Pick a plan from the updated menu.", "uk": "Обери тариф в оновленому меню."}
PAYMENT_STALE = {"ru": "Счёт устарел или недействителен. Создай новый счёт в боте.",
                 "en": "The invoice is stale or invalid. Create a new invoice in the bot.",
                 "uk": "Рахунок застарів або недійсний. Створи новий рахунок у боті."}
ALREADY_CREDITED = {"ru": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Этот платёж уже учтён.",
                     "en": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> This payment is already credited.",
                     "uk": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Цей платіж вже враховано."}
NEEDS_SUPPORT = {"ru": "Платёж требует проверки. Обратись в поддержку через /help.",
                 "en": "This payment needs a review. Contact support via /help.",
                 "uk": "Платіж потребує перевірки. Звернись до підтримки через /help."}
PAYMENT_DONE = {"ru": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Оплата получена. Возможность активирована.",
                "en": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Payment received. The feature is now active.",
                "uk": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Оплату отримано. Можливість активовано."}
REFUND_DONE = {"ru": "Возврат учтён. Доступ по возвращённой покупке отключён.",
               "en": "Refund processed. Access from the refunded purchase has been turned off.",
               "uk": "Повернення враховано. Доступ за поверненою покупкою вимкнено."}
PURCHASE_UNAVAILABLE = {"ru": "Покупка уже оплачена или больше недоступна.", "en": "Already paid, or no longer available.", "uk": "Вже оплачено або більше недоступно."}
INVOICE_PENDING = {"ru": "Счёт уже отправлен выше или отправляется. Если он не появился, попробуй ещё раз через 30 секунд.",
                    "en": "The invoice was already sent above, or is being sent. If it didn't show up, try again in 30 seconds.",
                    "uk": "Рахунок вже надіслано вище або надсилається. Якщо не з'явився, спробуй ще раз за 30 секунд."}
PRODUCT_UNAVAILABLE = {"ru": "Товар недоступен.", "en": "Item unavailable.", "uk": "Товар недоступний."}


async def product_keyboard(codes, session_id=None):
    products = await PaymentService().products(codes)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f'{p.title} — {p.price_stars}', icon_custom_emoji_id="5920281855378068765", style="primary",
            callback_data=f"v1:buy:{p.code}" + (f":{session_id}" if session_id else ""))
    ] for p in products])


async def show_premium(message, user_id, lang="ru"):
    from repositories.users import UserRepository
    from repositories.chats import premium_active
    from services.growth import first_purchase_discount
    user = await UserRepository().get(user_id)
    text = T.tr(PREMIUM_TEXT, lang)
    if user and premium_active(user):
        text += T.tr(ACTIVE_UNTIL, lang).format(date=f"{user.premium_expires:%d.%m.%Y}")
    else:
        discount = await first_purchase_discount(user_id)
        if discount:
            text += T.tr(FIRST_DISCOUNT, lang).format(pct=discount)
    await message.answer(text, reply_markup=await product_keyboard(["premium_1m", "premium_3m", "premium_12m"]))


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
    result = await PaymentService().create(callback.from_user.id, parts[2], parts[3] if len(parts)==4 else None)
    await callback.answer()
    if not result.ok:
        await callback.message.answer(T.tr(PURCHASE_UNAVAILABLE, lang))
        return
    p = result.data["payment"]
    if not await PaymentService().claim_invoice(callback.from_user.id,p.id):
        await callback.message.answer(T.tr(INVOICE_PENDING, lang))
        return
    invoice=await callback.bot.send_invoice(chat_id=callback.from_user.id, title=result.data["title"],
        description=T.tr(INVOICE_DESC, lang),
        payload=p.payload, currency="XTR", prices=[LabeledPrice(label=result.data["title"], amount=p.stars_amount)])
    await PaymentService().invoice_sent(p.id,invoice.message_id)
    if p.product=="instant_search":
        from services.growth import track
        await track(callback.from_user.id,"instant_search_invoice_created",f"invoice:{p.id}")


@premium_router.callback_query(F.data.startswith("premium:buy:"))
async def cb_legacy_buy(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer(T.tr(CHOOSE_PLAN, lang))
    await show_premium(callback.message, callback.from_user.id, lang)


@premium_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    ok = await PaymentService().precheckout(query.from_user.id, query.invoice_payload, query.total_amount, query.currency,query.id)
    await query.answer(ok=ok, **({} if ok else {"error_message": T.tr(PAYMENT_STALE, "ru")}))


@premium_router.message(F.successful_payment)
async def successful_payment(message: Message, lang: str = "ru"):
    p = message.successful_payment
    result = await PaymentService().complete(message.from_user.id, p.invoice_payload, p.total_amount, p.currency, p.telegram_payment_charge_id)
    if result.code == "ALREADY_PAID":
        await message.answer(T.tr(ALREADY_CREDITED, lang))
        return
    if not result.ok:
        import logging
        logging.getLogger(__name__).error("event=payment_validation_failed user_id=%s", message.from_user.id)
        await message.answer(T.tr(NEEDS_SUPPORT, lang))
        return
    await message.answer(T.tr(PAYMENT_DONE, lang))
    if result.data["product"] == "instant_search":
        from services.chat_manager import set_instant_search
        await set_instant_search(message.from_user.id)
    if result.data["product"] in {"reveal_priority_request", "reveal_status"}:
        from handlers.reveal import paid_reveal
        await paid_reveal(message, message.from_user.id, result.data["session_id"], result.data["product"], lang)


@premium_router.message(F.refunded_payment)
async def refunded_payment(message: Message, lang: str = "ru"):
    p = message.refunded_payment
    result = await PaymentService().refunded(message.from_user.id, p.invoice_payload,
        p.total_amount, p.currency, p.telegram_payment_charge_id)
    if result.ok:
        await message.answer(T.tr(REFUND_DONE, lang))

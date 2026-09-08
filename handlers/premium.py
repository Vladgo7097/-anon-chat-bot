from aiogram import F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from handlers import premium_router
from services.payments import PaymentService
from keyboards import texts as T
from keyboards.reply import main_menu_kb

PREMIUM_TEXT = (
    '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium-подписка\n\n'
    '<tg-emoji emoji-id="5976560705938133794">🎯</tg-emoji> Фильтры по полу и возрасту\n'
    '<tg-emoji emoji-id="5348232622898167572">❤️</tg-emoji> Приоритетные запросы на взаимное раскрытие — без доплаты\n'
    '<tg-emoji emoji-id="5350618807943576963">⚡</tg-emoji> Приоритет в очереди поиска\n'
    '<tg-emoji emoji-id="5260450573768990626">🔄</tg-emoji> «Следующий» без задержки\n'
    '<tg-emoji emoji-id="5442949339108366200">🖼</tg-emoji> До 3 фото в профиле\n\n'
    'Контакты всегда раскрываются только по взаимному согласию.\n\n'
    '<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Тарифы:'
)


async def product_keyboard(codes, session_id=None):
    products = await PaymentService().products(codes)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f'{p.title} — {p.price_stars}', icon_custom_emoji_id="5920281855378068765", style="primary",
            callback_data=f"v1:buy:{p.code}" + (f":{session_id}" if session_id else ""))
    ] for p in products])


async def show_premium(message, user_id):
    from repositories.users import UserRepository
    from repositories.chats import premium_active
    user = await UserRepository().get(user_id)
    text = PREMIUM_TEXT
    if user and premium_active(user):
        text += f"\n\n<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Активен до {user.premium_expires:%d.%m.%Y}"
    await message.answer(text, reply_markup=await product_keyboard(["premium_1m", "premium_3m", "premium_12m"]))


@premium_router.message(F.text == T.PREMIUM)
async def cmd_premium(message: Message):
    await show_premium(message, message.from_user.id)


@premium_router.callback_query(F.data == "premium")
async def cb_premium(callback: CallbackQuery):
    await callback.answer()
    await show_premium(callback.message, callback.from_user.id)


@premium_router.callback_query(F.data.startswith("v1:buy:"))
async def cb_buy(callback: CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        await callback.answer("Товар недоступен.")
        return
    result = await PaymentService().create(callback.from_user.id, parts[2], parts[3] if len(parts)==4 else None)
    await callback.answer()
    if not result.ok:
        await callback.message.answer("Покупка уже оплачена или больше недоступна.")
        return
    p = result.data["payment"]
    if not await PaymentService().claim_invoice(callback.from_user.id,p.id):
        await callback.message.answer("Счёт уже отправлен выше или отправляется. Если он не появился, попробуй ещё раз через 30 секунд.")
        return
    invoice=await callback.bot.send_invoice(chat_id=callback.from_user.id, title=result.data["title"],
        description="Улучшение анонимного чата. Приоритет не гарантирует наличие подходящего собеседника; контакты требуют взаимного согласия.",
        payload=p.payload, currency="XTR", prices=[LabeledPrice(label=result.data["title"], amount=p.stars_amount)])
    await PaymentService().invoice_sent(p.id,invoice.message_id)
    if p.product=="instant_search":
        from services.growth import track
        await track(callback.from_user.id,"instant_search_invoice_created",f"invoice:{p.id}")


@premium_router.callback_query(F.data.startswith("premium:buy:"))
async def cb_legacy_buy(callback: CallbackQuery):
    await callback.answer("Выбери тариф в обновлённом меню.")
    await show_premium(callback.message, callback.from_user.id)


@premium_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    ok = await PaymentService().precheckout(query.from_user.id, query.invoice_payload, query.total_amount, query.currency,query.id)
    await query.answer(ok=ok, **({} if ok else {"error_message": "Счёт устарел или недействителен. Создай новый счёт в боте."}))


@premium_router.message(F.successful_payment)
async def successful_payment(message: Message):
    p = message.successful_payment
    result = await PaymentService().complete(message.from_user.id, p.invoice_payload, p.total_amount, p.currency, p.telegram_payment_charge_id)
    if result.code == "ALREADY_PAID":
        await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Этот платёж уже учтён.")
        return
    if not result.ok:
        import logging
        logging.getLogger(__name__).error("event=payment_validation_failed user_id=%s", message.from_user.id)
        await message.answer("Платёж требует проверки. Обратись в поддержку через /help.")
        return
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Оплата получена. Возможность активирована.")
    if result.data["product"] == "instant_search":
        from services.chat_manager import set_instant_search
        await set_instant_search(message.from_user.id)
    if result.data["product"] in {"reveal_priority_request", "reveal_status"}:
        from handlers.reveal import paid_reveal
        await paid_reveal(message, message.from_user.id, result.data["session_id"], result.data["product"])


@premium_router.message(F.refunded_payment)
async def refunded_payment(message: Message):
    p = message.refunded_payment
    result = await PaymentService().refunded(message.from_user.id, p.invoice_payload,
        p.total_amount, p.currency, p.telegram_payment_charge_id)
    if result.ok:
        await message.answer("Возврат учтён. Доступ по возвращённой покупке отключён.")

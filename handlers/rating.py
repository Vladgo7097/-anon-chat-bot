from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from handlers import rating_router
from keyboards import texts as T
from repositories.chats import ChatRepository

THANKS = {"ru": "Спасибо за оценку!", "en": "Thanks for the rating!", "uk": "Дякуємо за оцінку!"}
RATE_UNAVAILABLE = {"ru": "Оценка уже учтена или диалог недоступен.", "en": "Already rated, or the chat is no longer available.",
                     "uk": "Оцінку вже враховано або діалог недоступний."}
THANKS_DONE = {"ru": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Спасибо за оценку!",
               "en": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Thanks for the rating!",
               "uk": "<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Дякуємо за оцінку!"}
INVITE_PROMPT = {
    "ru": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Понравилось общение? Пригласи друга — после его первого диалога вы оба получите скидку.",
    "en": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Enjoyed the chat? Invite a friend — after their first chat you'll both get a discount.",
    "uk": "<tg-emoji emoji-id=\"5348232622898167572\">❤️</tg-emoji> Сподобалось спілкування? Запроси друга — після його першого діалогу ви обидва отримаєте знижку.",
}
INVITE_BUTTON = {"ru": "Поделиться ссылкой", "en": "Share the link", "uk": "Поділитись посиланням"}
OLD_RATING = {"ru": "Эта кнопка устарела. Оценка доступна у новых диалогов.",
              "en": "This button is outdated. Ratings are available on new chats.",
              "uk": "Ця кнопка застаріла. Оцінка доступна у нових діалогах."}


@rating_router.callback_query(F.data.startswith("v1:rate:"))
async def cb_rate(callback: CallbackQuery, lang: str = "ru"):
    parts = callback.data.split(":")
    ok = len(parts) == 4 and parts[3] in {"1", "-1"}
    if ok:
        ok = await ChatRepository().rate(parts[2], callback.from_user.id, int(parts[3]))
    await callback.answer(T.tr(THANKS, lang) if ok else T.tr(RATE_UNAVAILABLE, lang))
    if ok:
        try:
            await callback.message.edit_text(T.tr(THANKS_DONE, lang))
        except TelegramBadRequest:
            pass
        # Riding the emotional high of a chat the user just rated positively
        # -- the natural moment to ask them to bring a friend, not buried in
        # a menu they may never open.
        if parts[3] == "1":
            from urllib.parse import quote
            from repositories.users import UserRepository
            user = await UserRepository().get(callback.from_user.id)
            if user and user.referral_code:
                bot = await callback.bot.get_me()
                link = f"https://t.me/{bot.username}?start=ref_{user.referral_code}"
                await callback.message.answer(T.tr(INVITE_PROMPT, lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text=T.tr(INVITE_BUTTON, lang), url=f"https://t.me/share/url?url={quote(link, safe='')}")
                ]]))


@rating_router.callback_query(F.data.startswith("rate:"))
async def cb_old_rating(callback: CallbackQuery, lang: str = "ru"):
    await callback.answer(T.tr(OLD_RATING, lang))

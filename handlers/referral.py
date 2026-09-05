from aiogram.types import Message

from utils.db import get_or_create_user, update_user, get_user_by_id


REFERRAL_TEXT = """👥 Приведи друга — получи скидку

За каждого друга, который зарегистрируется по твоей ссылке
и проведёт хотя бы 1 диалог:
Тебе — скидка 20% на следующую покупку звёзд
Другу — скидка 20% на первую покупку Premium

🔗 Твоя ссылка: {ref_link}
👥 Приглашено: {invited} | Активировали: {activated}

📤 Поделиться ссылкой"""


def get_referral_link(bot_username: str, user_id: int) -> str:
    return f"t.me/{bot_username}?start=ref_{user_id}"


async def show_referral(message_or_callback, user_id: int, bot_username: str):
    user = await get_or_create_user(user_id)
    ref_link = f"t.me/{bot_username}?start=ref_{user.telegram_id}"
    text = REFERRAL_TEXT.format(
        ref_link=ref_link,
        invited=user.referrals_count,
        activated=user.referrals_activated,
    )
    if isinstance(message_or_callback, Message):
        await message_or_callback.answer(text)
    else:
        await message_or_callback.message.edit_text(text)
        await message_or_callback.answer()


async def process_referral(new_user_id: int, referrer_telegram_id: int):
    if new_user_id == referrer_telegram_id:
        return

    referrer = await get_user_by_id(referrer_telegram_id)
    if referrer:
        await update_user(referrer_telegram_id, referrals_count=referrer.referrals_count + 1)


async def activate_referral(new_user_id: int):
    user = await get_user_by_id(new_user_id)
    if not user or not user.referrer_id:
        return

    referrer = await get_user_by_id(user.referrer_id)
    if referrer:
        await update_user(referrer.telegram_id, referrals_activated=referrer.referrals_activated + 1)

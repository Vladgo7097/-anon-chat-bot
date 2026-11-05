"""Keyboards for the post-chat window, friend requests and the friends list."""

from aiogram.types import DisabledButton, InlineKeyboardButton, InlineKeyboardMarkup

from keyboards import texts as T


def after_chat_kb(session_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    labels = T.tr(T.CHAT_AFTER, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels["reconnect"],
                    callback_data=f"reconnect:{session_id}",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text=labels["friend"], callback_data=f"friend:req:{session_id}"
                )
            ],
            [InlineKeyboardButton(text=labels["menu"], callback_data="menu")],
        ]
    )


def with_disabled(
    markup: InlineKeyboardMarkup, callback_prefix: str
) -> InlineKeyboardMarkup:
    """Bot API 10.3: отключить кнопки с данным prefix callback_data.

    Отключённая кнопка не нажимается вовсе — она честно показывает состояние
    («заявка уже отправлена») вместо тоста «уже в очереди» на каждый тап.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button.model_copy(update={"disabled": DisabledButton()})
                if (button.callback_data or "").startswith(callback_prefix)
                else button
                for button in row
            ]
            for row in markup.inline_keyboard
        ]
    )


def friend_request_kb(friendship_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    buttons = T.tr(T.COMMUNITY_BUTTONS, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=buttons["accept"],
                    callback_data=f"friend:ok:{friendship_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=buttons["decline"], callback_data=f"friend:no:{friendship_id}"
                ),
            ]
        ]
    )


def friend_invite_kb(friendship_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    buttons = T.tr(T.COMMUNITY_BUTTONS, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=buttons["accept"],
                    callback_data=f"friend:join:{friendship_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text=buttons["decline"],
                    callback_data=f"friend:decline:{friendship_id}",
                ),
            ]
        ]
    )


def reconnect_waiting_kb(session_id: str, lang: str = "ru") -> InlineKeyboardMarkup:
    labels = T.tr(T.CHAT_AFTER, lang)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=labels["reconnect"],
                    callback_data=f"reconnect:{session_id}",
                    style="primary",
                ),
            ]
        ]
    )


def friends_kb(rows, lang: str = "ru") -> InlineKeyboardMarkup:
    buttons = T.tr(T.COMMUNITY_BUTTONS, lang)
    keyboard = [
        [
            InlineKeyboardButton(
                text=buttons["invite"], callback_data=f"friend:call:{friendship_id}"
            )
        ]
        for friendship_id, _anon_id in rows
    ]
    keyboard.append(
        [InlineKeyboardButton(text=buttons["back"], callback_data="profile")]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

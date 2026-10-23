from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keyboards import texts as T
from services import emoji_settings
import config


def kb_button(key, lang):
    # Telegram Web renders these reply buttons as icon-only (drops the text
    # label) -- a known Web-specific quirk, kept anyway because the colored
    # icon look is the intended style and other clients may render both.
    return KeyboardButton(
        text=T.label(key, lang),
        # NOTE: KeyboardButton has no url field in the Bot API -- Telegram
        # would drop it silently and the tap would just send the label as a
        # message. The community chat is a plain button handled in menu.py.
        icon_custom_emoji_id=emoji_settings.effective_id(key),
        style="danger" if key in {T.STOP, T.REPORT, T.CANCEL} else "primary",
    )


def keyboard(rows, lang):
    return ReplyKeyboardMarkup(
        keyboard=[[kb_button(key, lang) for key in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_menu_kb(lang="ru"):
    # IDEA стоит на месте INVITE: рефералка уходит в карточку наград,
    # меню не держит кнопку без живого CTA (см. menu.py).
    return keyboard(
        [
            [T.SEARCH, T.PROFILE],
            [T.SETTINGS, T.PREMIUM],
            [T.STATS, T.HELP],
            [T.CHAT, T.IDEA],
        ],
        lang,
    )


def chat_kb_reply(lang="ru"):
    return keyboard([[T.NEXT, T.STOP], [T.REPORT, T.REVEAL]], lang)


def search_kb_reply(lang="ru"):
    return keyboard([[T.CANCEL]], lang)


def onboarding_kb(step, page=0, lang="ru"):
    if step == "WELCOME":
        return keyboard([[T.MALE, T.FEMALE]], lang)
    if step == "GENDER":
        return keyboard([[T.MALE, T.FEMALE]], lang)
    if step == "AGE":
        pages = (config.MAX_AGE - config.MIN_AGE) // config.AGE_PAGE_SIZE
        page = min(max(0, page), pages)
        start = config.MIN_AGE + page * config.AGE_PAGE_SIZE
        ages = [
            str(n)
            for n in range(start, min(start + config.AGE_PAGE_SIZE, config.MAX_AGE + 1))
        ]
        rows = [ages[i : i + 4] for i in range(0, len(ages), 4)]
        nav = ([T.PAGE_PREV] if page else []) + ([T.PAGE_NEXT] if page < pages else [])
        return keyboard(rows + ([nav] if nav else []), lang)
    if step == "RULES":
        return keyboard([[T.ACCEPT], [T.RULES]], lang)
    return main_menu_kb(lang)

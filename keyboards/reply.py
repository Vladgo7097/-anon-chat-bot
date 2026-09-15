from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keyboards import texts as T
import config


def kb_button(key, lang):
    return KeyboardButton(
        text=T.label(key, lang),
        icon_custom_emoji_id=T.EMOJI_IDS.get(key),
        style="danger" if key in {T.STOP, T.REPORT, T.CANCEL} else "primary"
    )


def keyboard(rows, lang):
    return ReplyKeyboardMarkup(keyboard=[[kb_button(key, lang) for key in row] for row in rows], resize_keyboard=True, is_persistent=True)


def main_menu_kb(lang="ru"):
    return keyboard([[T.SEARCH, T.PROFILE], [T.SETTINGS, T.PREMIUM], [T.STATS, T.HELP]], lang)


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
        ages = [str(n) for n in range(start, min(start + config.AGE_PAGE_SIZE, config.MAX_AGE + 1))]
        rows = [ages[i:i+4] for i in range(0, len(ages), 4)]
        nav = ([T.PAGE_PREV] if page else []) + ([T.PAGE_NEXT] if page < pages else [])
        return keyboard(rows + ([nav] if nav else []), lang)
    if step == "RULES":
        return keyboard([[T.ACCEPT], [T.RULES]], lang)
    return main_menu_kb(lang)

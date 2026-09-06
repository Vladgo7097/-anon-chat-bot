from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from keyboards import texts as T
import config


def kb_button(text):
    emoji_id = T.EMOJI_IDS.get(text)
    return KeyboardButton(
        text=text,
        icon_custom_emoji_id=emoji_id,
        style="danger" if text in {T.STOP, T.REPORT, T.CANCEL} else "primary"
    )


def keyboard(rows):
    return ReplyKeyboardMarkup(keyboard=[[kb_button(text) for text in row] for row in rows], resize_keyboard=True, is_persistent=True)


def main_menu_kb():
    return keyboard([[T.SEARCH, T.PROFILE], [T.SETTINGS, T.PREMIUM], [T.STATS, T.HELP]])


def chat_kb_reply():
    return keyboard([[T.NEXT, T.STOP], [T.REPORT, T.REVEAL]])


def search_kb_reply():
    return keyboard([[T.CANCEL]])


def onboarding_kb(step, page=0):
    if step == "WELCOME":
        return keyboard([[T.BEGIN]])
    if step == "GENDER":
        return keyboard([[T.MALE, T.FEMALE]])
    if step == "AGE":
        pages = (config.MAX_AGE - config.MIN_AGE) // config.AGE_PAGE_SIZE
        page = min(max(0, page), pages)
        start = config.MIN_AGE + page * config.AGE_PAGE_SIZE
        ages = [str(n) for n in range(start, min(start + config.AGE_PAGE_SIZE, config.MAX_AGE + 1))]
        rows = [ages[i:i+4] for i in range(0, len(ages), 4)]
        nav = ([T.PAGE_PREV] if page else []) + ([T.PAGE_NEXT] if page < pages else [])
        return keyboard(rows + ([nav] if nav else []))
    if step == "RULES":
        return keyboard([[T.ACCEPT], [T.RULES]])
    return main_menu_kb()

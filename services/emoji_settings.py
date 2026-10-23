"""Слоты reply-кнопок и их иконки: дефолт из каталога, замена через bot_settings.

Слот -- один ключ главного каталога клавиатур (keyboards/texts.EMOJI_IDS).
Хранение: bot_settings["emoji_overrides"] = {"value": {слот: id или ""}}.
Пустая строка означает «кнопка намеренно без иконки»; отсутствие ключа --
оляпия дефолта из кода. Кэш синхронный: kb_button() собирается на каждый
рендер синхронно, поэтому id берётся из словаря, который перезаряжается на
старте бота и после каждой записи.
"""

import logging

from models.base import async_session
from services.settings import bot_value

logger = logging.getLogger(__name__)

SETTING_KEY = "emoji_overrides"

# слот -> id (или ""), написанный в bot_settings; слоты вне этого
# словаря рендерятся каталогом из кода.
_cache: dict[str, str] = {}


async def load_cache() -> None:
    global _cache
    _cache = dict(await bot_value(SETTING_KEY, {}) or {})


async def set_override(key: str, emoji_id: str, by_user: int | None = None) -> None:
    """Назначить слоту иконку: новый id, замена старого или "" (убрать иконку)."""
    from models.operations import BotSetting
    from sqlalchemy import select

    current = await bot_value(SETTING_KEY, {}) or {}
    current[key] = emoji_id
    async with async_session.begin() as s:
        await s.execute(
            select(BotSetting).where(BotSetting.key == SETTING_KEY).with_for_update()
        )
        item = await s.get(BotSetting, SETTING_KEY, with_for_update=True)
        if not item:
            item = BotSetting(key=SETTING_KEY)
            s.add(item)
        item.value_json = {"value": current}
    global _cache
    _cache = dict(current)
    logger.info(
        "emoji override set", extra={"slot": key, "emoji_id": emoji_id or "<none>"}
    )


async def clear_override(key: str) -> None:
    """Вернуть слот к дефолту из кода: убрать из переопределений."""
    from models.operations import BotSetting
    from sqlalchemy import select

    current = await bot_value(SETTING_KEY, {}) or {}
    current.pop(key, None)
    async with async_session.begin() as s:
        await s.execute(
            select(BotSetting).where(BotSetting.key == SETTING_KEY).with_for_update()
        )
        item = await s.get(BotSetting, SETTING_KEY, with_for_update=True)
        if not item:
            item = BotSetting(key=SETTING_KEY)
            s.add(item)
        item.value_json = {"value": current}
    global _cache
    _cache = dict(current)
    logger.info("emoji override cleared", extra={"slot": key})


def effective_id(key: str) -> str | None:
    """Иконка слота как она уйдёт в Telegram: переопределение > код > нет."""
    if key in _cache:
        return _cache[key] or None
    from keyboards import texts as T

    return T.EMOJI_IDS.get(key)

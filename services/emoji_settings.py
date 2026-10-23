"""Слоты reply-кнопок и их иконки: дефолт из каталога, замена через bot_settings.

Слот -- один ключ главного каталога клавиатур (keyboards/texts.EMOJI_IDS).
Хранение: bot_settings["emoji_overrides"] = {"value": {слот: id или ""}}.
Пустая строка означает «кнопка намеренно без иконки»; отсутствие ключа --
дефолт из кода. Кэш синхронный: kb_button() собирается на каждый рендер
синхронно, поэтому id берётся из словаря, который перезаряжается на старте
бота и после каждой записи.
"""

import logging

from models.base import async_session

logger = logging.getLogger(__name__)

SETTING_KEY = "emoji_overrides"

# слот -> id (или ""), написанный в bot_settings; слоты вне этого
# словаря рендерятся каталогом из кода.
_cache: dict[str, str] = {}


async def load_cache() -> None:
    from services.settings import bot_value

    global _cache
    _cache = dict(await bot_value(SETTING_KEY, {}) or {})


async def set_override(key: str, emoji_id: str, by_user: int | None = None) -> None:
    """Назначить слоту иконку: новый id, замена старого или "" (убрать иконку).

    Двухфазная запись: чтение и объединение внутри блокировки строки
    bot_settings, чтобы параллельные writers не топили правки друг друга.
    """
    from models.operations import BotSetting
    from sqlalchemy import select

    async with async_session.begin() as s:
        row = await s.execute(select(BotSetting).where(BotSetting.key == SETTING_KEY))
        item = row.scalar_one_or_none()
        if not item:
            item = BotSetting(key=SETTING_KEY)
            s.add(item)
        current = dict((item.value_json or {}).get("value") or {})
        current[key] = emoji_id
        item.value_json = {"value": current}
        item.updated_by = by_user
    global _cache
    _cache = dict(current)
    logger.info(
        "emoji override set", extra={"slot": key, "emoji_id": emoji_id or "<none>"}
    )


async def clear_override(key: str) -> None:
    """Вернуть слот к дефолту из кода: убрать из переопределений."""
    from models.operations import BotSetting
    from sqlalchemy import select

    async with async_session.begin() as s:
        row = await s.execute(select(BotSetting).where(BotSetting.key == SETTING_KEY))
        item = row.scalar_one_or_none()
        if not item:
            item = BotSetting(key=SETTING_KEY)
            s.add(item)
        current = dict((item.value_json or {}).get("value") or {})
        current.pop(key, None)
        item.value_json = {"value": current}
        item.updated_by = None
    global _cache
    _cache = dict(current)
    logger.info("emoji override cleared", extra={"slot": key})


def overrides() -> dict[str, str]:
    """Действующие переопределения слотов (публичный доступ к кэшу)."""
    return dict(_cache)


def effective_id(key: str) -> str | None:
    """Иконка слота как она уйдёт в Telegram: переопределение > код > нет."""
    if key in _cache:
        return _cache[key] or None
    from keyboards import texts as T

    return T.EMOJI_IDS.get(key)

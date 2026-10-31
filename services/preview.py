"""Preview-режим: согласие/отказ/подметание офферов.

Очередь создаёт PreviewOffer через repositories.ChatRepository.create_offer()
под тем же lock_matching; здесь живёт машина ответов и sweeper. Kill-switch
-- BotSetting preview_mode с кэшем 5 c: тики поиска случаются каждые ~2 с на
каждого ищущего, читать таблицу настроек на каждый тик -- лишняя нагрузка.
"""

import time
from datetime import timedelta

from sqlalchemy import delete, or_, update

from models.base import async_session
from models.preview import PreviewOffer
from repositories.chats import ChatRepository, lock_matching
from utils.time import utcnow

_FLAG = {"value": False, "expires": 0.0}


async def enabled():
    """BotSetting preview_mode, кэш 5 с: тумблер вступает в силу с заданным
    опозданием, незаметным на тиках поиска.

    Чтение падает мягко: база недоступна -- режим слепого поиска
    (умолчание kill-switch) с коротким окном кэша, чтобы тики не били
    сокеты каждые две секунды.
    """
    now = time.monotonic()
    if now < _FLAG["expires"]:
        return _FLAG["value"]
    from models.operations import BotSetting

    try:
        async with async_session() as s:
            setting = await s.get(BotSetting, "preview_mode")
            try:
                value = bool(int(setting.value_json["value"])) if setting else False
            except (KeyError, TypeError, ValueError):
                value = False
    except Exception:
        _FLAG["value"] = False
        _FLAG["expires"] = now + 2.0
        return False
    _FLAG["value"] = value
    _FLAG["expires"] = now + 5.0
    return value


class PreviewService:
    @staticmethod
    async def consent(uid, offer_id):
        """Двойное «да» открывает чат той же материей, что и слепой матч.

        Первый ответ молчит: вторая сторона видит свою карточку и решает
        сама. Статусы тостов: waited (ответ учтён), mutual (чат открыт),
        gone (оффер закрыт, не твой или истёк).
        """
        from services.chat_manager import get_redis

        r = await get_redis()
        chat = None
        async with async_session.begin() as s:
            await lock_matching(s)
            offer = await s.get(PreviewOffer, offer_id)
            if (
                offer is None
                or offer.status != "requested"
                or uid not in (offer.user1_id, offer.user2_id)
            ):
                return "gone", None
            if offer.expires_at <= utcnow():
                offer.status = "expired"
                offer.resolved_at = utcnow()
                return "gone", None
            if offer.user1_id == uid:
                offer.consent_1 = True
            else:
                offer.consent_2 = True
            if not (offer.consent_1 and offer.consent_2):
                return "waited", None
            # Резервация снята ДО recheck'а: иначе load_pair отверг бы
            # разговор из-за собственного оффера. Успех возвращает статус
            # в mutual той же транзакцией, длинформа -- транзакция атомарна.
            offer.status = "stale"
            offer.resolved_at = utcnow()
            pair = await ChatRepository.load_pair(s, r, offer.user1_id, offer.user2_id)
            if pair is None:
                # Кто-то ушёл из поиска/сервиса за время переговоров:
                # пара освобождена, оффер не переждёт мгновенно.
                return "gone", None
            users, filters = pair
            from services.growth import event

            await event(s, uid, "preview_confirmed", f"preview:{offer.id}:confirmed")
            chat = await ChatRepository.finalize_chat(
                s, offer.user1_id, offer.user2_id, users, filters
            )
            offer.status = "mutual"
            offer.resolved_session = chat.session_id
        return "mutual", chat

    @staticmethod
    async def decline(uid, offer_id):
        """«Не сейчас»: оффер закрыт, обе стороны возвращаются в очередь."""
        async with async_session.begin() as s:
            await lock_matching(s)
            offer = await s.get(PreviewOffer, offer_id)
            if (
                offer is None
                or offer.status != "requested"
                or uid not in (offer.user1_id, offer.user2_id)
            ):
                return "gone"
            offer.status = "declined"
            offer.resolved_at = utcnow()
            from services.growth import event

            await event(s, uid, "preview_declined", f"preview:{offer.id}:{uid}")
        return "declined"

    @staticmethod
    async def cancel_active(uid):
        """«Стоп» в поиске гасит живые переговоры юзера без вежливых ожиданий."""
        async with async_session.begin() as s:
            result = await s.execute(
                update(PreviewOffer)
                .where(
                    PreviewOffer.status == "requested",
                    or_(PreviewOffer.user1_id == uid, PreviewOffer.user2_id == uid),
                )
                .values(status="canceled", resolved_at=utcnow())
            )
        return result.rowcount

    @staticmethod
    async def expire_stale():
        """Sweeper maintenance: истёкшие 'requested' уходят в 'expired' --
        снимает резервации пар и кормит кулдаун «молчание»."""
        async with async_session.begin() as s:
            result = await s.execute(
                update(PreviewOffer)
                .where(
                    PreviewOffer.status == "requested",
                    PreviewOffer.expires_at <= utcnow(),
                )
                .values(status="expired", resolved_at=utcnow())
            )
        return result.rowcount


async def cleanup(days=7):
    """Retention: закрытые офферы неделю держат историю кулдаунов, потом
    уходят. Таблица маленькая и запах премий, но гигиена первична.

    'requested' строки не сносит никогда: они либо закроются ответом,
    либо их поглотит expire_stale() в течение минуты после окна.
    """
    deadline = utcnow() - timedelta(days=days)
    async with async_session.begin() as s:
        result = await s.execute(
            delete(PreviewOffer).where(
                PreviewOffer.status != "requested",
                or_(
                    PreviewOffer.resolved_at < deadline,
                    PreviewOffer.created_at < deadline,
                ),
            )
        )
    return result.rowcount

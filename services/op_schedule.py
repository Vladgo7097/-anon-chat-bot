"""ОП v2: уведомления владельцу о начале и конце окон требования.

Окна не включают и не выключают гейт -- сервис проверки вычисляет
активность на месте. Этот модуль только смотрит на границы (общее окно,
окна правил и ресурсов) и кладёт в очередь по одному уведомлению на
границу. Дедупликация -- через dedupe_key: воркер может перезапуститься
или проверка пройти дважды, владелец получит одно сообщение.

Границы ловятся с запасом LOOKBACK: минутный тик воркера может
опоздать, а после падения контейнера хочется увидеть хотя бы недавние
переходы, а не тишину.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from config import OWNER_ID
from models.base import async_session
from models.op import OpPolicy, OpSettings, OpTarget
from services import op as core
from services.notifications import enqueue
from utils.time import utcnow
from utils import bots

logger = logging.getLogger(__name__)

LOOKBACK = timedelta(minutes=15)


def _entries(settings, policies, targets, now: datetime):
    """Границы, попавшие в окно [now - LOOKBACK, now]."""
    found = []

    def add(kind: str, title: str, starts_at, ends_at):
        for moment, label in ((starts_at, "начало"), (ends_at, "конец")):
            if moment is None:
                continue
            if now - LOOKBACK <= moment <= now:
                found.append((kind, title, moment, label))

    add("settings", "Общее окно ОП", settings.starts_at, settings.ends_at)
    for policy in policies:
        title = (
            "Глобальное правило"
            if policy.feature_key == "*"
            else f"Правило «{core.feature_name(policy.feature_key, 'ru')}»"
        )
        add(f"policy:{policy.id}", title, policy.starts_at, policy.ends_at)
    for target in targets:
        add(
            f"target:{target.id}",
            f"Ресурс «{target.title or target.chat_id}»",
            target.starts_at,
            target.ends_at,
        )
    return found


def _text(bot_id: int, title: str, moment: datetime, label: str) -> str:
    return (
        f"🗓 ОП · {bots.label(bot_id)}\n\n"
        f"{label.capitalize()} окна: {title}.\n"
        f"Момент: {moment:%d.%m.%Y %H:%M} UTC.\n\n"
        + (
            "Требование начало действовать."
            if label == "начало"
            else "Требование перестало действовать."
        )
    )


async def check(now: datetime | None = None) -> int:
    """Возвращает число поставленных уведомлений (с учётом дедупликации)."""
    if not OWNER_ID:
        return 0
    now = now or utcnow()
    placed = 0
    async with async_session.begin() as s:
        settings_rows = list((await s.scalars(select(OpSettings))).all())
        for settings in settings_rows:
            if not settings.enabled or settings.paused:
                continue
            policies = list(
                (
                    await s.scalars(
                        select(OpPolicy).where(
                            OpPolicy.bot_id == settings.bot_id,
                            OpPolicy.enabled.is_(True),
                        )
                    )
                ).all()
            )
            targets = list(
                (
                    await s.scalars(
                        select(OpTarget).where(
                            OpTarget.bot_id == settings.bot_id,
                            OpTarget.is_active.is_(True),
                        )
                    )
                ).all()
            )
            for kind, title, moment, label in _entries(
                settings, policies, targets, now
            ):
                key = f"op_window:{settings.bot_id}:{kind}:{moment:%Y%m%d%H%M}:{label}"
                await enqueue(
                    s,
                    OWNER_ID,
                    "op_window",
                    key,
                    _text(settings.bot_id, title, moment, label),
                    bot_id=settings.bot_id,
                )
                placed += 1
    return placed

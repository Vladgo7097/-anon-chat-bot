"""Progressive moderation: warnings turn into a mute, then into a ban.

A warning is created only when a moderator confirms a report or issues one
manually. The thresholds are evaluated in the same transaction as the admin
action, so a race cannot produce two mutes for the same fifth warning.
"""

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select

import config
from models.base import async_session
from models.community import UserWarning
from models.user import User
from utils.time import utcnow

logger = logging.getLogger(__name__)

WARN_NOTIFY = {
    "ru": "⚠️ Предупреждение. Активных: {count} из {mute} до мута; на {ban} — бан.",
    "en": "⚠️ Warning. Active: {count} of {mute} before a mute; at {ban} — a ban.",
    "uk": "⚠️ Попередження. Активних: {count} із {mute} до муту; на {ban} — бан.",
}


async def warning_count(s, user_id, now=None) -> int:
    now = now or utcnow()
    return int(
        await s.scalar(
            select(func.count(UserWarning.id)).where(
                UserWarning.user_id == user_id,
                UserWarning.expires_at > now,
            )
        )
        or 0
    )


async def add_warning(s, user_id, reason, issued_by=None, report_id=None):
    """Add one warning, idempotent per report.

    Returns the new row, or None when this report already produced a warning.
    The caller's transaction owns the commit; the notification is queued in
    the same outbox so a restart cannot lose it.
    """
    if report_id is not None and await s.scalar(
        select(UserWarning.id).where(UserWarning.report_id == report_id)
    ):
        return None
    now = utcnow()
    row = UserWarning(
        user_id=user_id,
        report_id=report_id,
        reason=(reason or "")[:255],
        issued_by=issued_by,
        created_at=now,
        expires_at=now + timedelta(days=max(1, config.WARN_TTL_DAYS)),
    )
    s.add(row)
    await s.flush()
    count = await warning_count(s, user_id, now)
    lang = (
        await s.scalar(select(User.language).where(User.telegram_id == user_id)) or "ru"
    )
    text = WARN_NOTIFY.get(lang, WARN_NOTIFY["ru"]).format(
        count=count, mute=config.WARN_MUTE_AT, ban=config.WARN_BAN_AT
    )
    from services.notifications import enqueue

    await enqueue(s, user_id, "warning", f"warning:{row.id}", text)
    return row


async def apply_progressive(s, user_id, now=None):
    """Apply the automatic punishment for the current warning count.

    Returns "mute", "ban" or None. An explicit admin action is applied before
    this call; the checks below only upgrade when no equal or stronger
    punishment is already active.
    """
    if config.WARN_MUTE_AT <= 0 or config.WARN_BAN_AT <= 0:
        return None
    now = now or utcnow()
    count = await warning_count(s, user_id, now)
    if count < min(config.WARN_MUTE_AT, config.WARN_BAN_AT):
        return None
    user = await s.scalar(
        select(User).where(User.telegram_id == user_id).with_for_update()
    )
    if not user:
        return None
    from services.admin import AdminService

    admin = AdminService()
    if count >= config.WARN_BAN_AT:
        limit = now + timedelta(hours=max(1, config.WARN_BAN_HOURS))
        if user.is_banned and (user.ban_expires is None or user.ban_expires > limit):
            return None
        await admin._ban(
            s,
            0,
            user_id,
            f"Прогрессивная модерация: {count} предупреждений",
            config.WARN_BAN_HOURS or None,
        )
        logger.info("event=progressive_ban user_id=%s warnings=%s", user_id, count)
        return "ban"
    if user.is_banned:
        return None
    limit = now + timedelta(hours=max(1, config.WARN_MUTE_HOURS))
    if user.is_muted and (user.mute_expires is None or user.mute_expires > limit):
        return None
    await admin._mute(
        s,
        0,
        user_id,
        f"Прогрессивная модерация: {count} предупреждений",
        config.WARN_MUTE_HOURS,
    )
    logger.info("event=progressive_mute user_id=%s warnings=%s", user_id, count)
    return "mute"


async def cleanup():
    """Drop warning rows that are far past their expiry.

    Active counts already ignore expired rows; this only stops the table from
    growing forever with old reports.
    """
    cutoff = utcnow() - timedelta(days=180)
    async with async_session.begin() as s:
        await s.execute(delete(UserWarning).where(UserWarning.expires_at < cutoff))

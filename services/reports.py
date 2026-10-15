import logging
from sqlalchemy import select, or_, func
from sqlalchemy.exc import IntegrityError
from models.base import async_session
from models.chat import Chat, Block
from models.report import Report
from repositories.chats import lock_matching
from services.result import ServiceResult
from services.chats import ChatService
from utils.time import utcnow

MAX_REPORTS_PER_DAY = 20  # дневная квота одной учётной записи на жалобы
MAX_REPORTS_PER_VICTIM_PER_DAY = 20  # дневная квота жалоб на одну жертву

REASONS = {"18plus": "Контент 18+", "insult": "Оскорбления", "spam": "Спам/реклама",
           "fraud": "Мошенничество", "minor": "Подозрение на несовершеннолетнего", "other": "Другое"}


class ReportService:
    async def submit(self, user_id, session_id, reason, details=None):
        if reason not in REASONS:
            return ServiceResult(False, "INVALID_REASON")
        from services.relay import RelayService
        relay = RelayService()
        context = await relay.report_context(session_id)
        # Обёртка под IntegrityError: две параллельные жалобы проходят
        # проверку existing до того, как любая вставит, и проигравший
        # получает чистый «жалоба уже отправлена», а не generic
        # «повери позже» из error-trap и update_failed в лог.
        try:
            async with async_session.begin() as s:
                await lock_matching(s)
                # Дневная квота на жалобы: 1000 аккаунтов -> 1000 строк Block и
                # 1000 уведомлений владельцу, затапливающих его канал.
                today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                today_count = await s.scalar(
                    select(func.count(Report.id)).where(
                        Report.reporter_id == user_id, Report.created_at >= today_start
                    )
                )
                if today_count >= MAX_REPORTS_PER_DAY:
                    return ServiceResult(False, "QUOTA_EXCEEDED")
                chat = await s.scalar(select(Chat).where(Chat.session_id == session_id,
                    or_(Chat.user1_id == user_id, Chat.user2_id == user_id)).with_for_update())
                if not chat:
                    return ServiceResult(False, "NOT_FOUND")
                existing = await s.scalar(select(Report).where(Report.chat_session_id == session_id, Report.reporter_id == user_id))
                if existing:
                    return ServiceResult(False, "ALREADY_REPORTED")
                if chat.status != "active":
                    return ServiceResult(False, "SESSION_EXPIRED")
                partner = chat.user2_id if user_id == chat.user1_id else chat.user1_id
                # Квота на жертву: 1000 РАЗНЫХ аккаунтов по одной жалобе обходят
                # per-reporter лимит -- владельцу уходило бы 1000 уведомлений.
                victim_today = await s.scalar(
                    select(func.count(Report.id)).where(
                        Report.reported_id == partner, Report.created_at >= today_start
                    )
                )
                if victim_today >= MAX_REPORTS_PER_VICTIM_PER_DAY:
                    return ServiceResult(False, "QUOTA_EXCEEDED")
                labels = {user_id: "Вы", partner: "Собеседник"}
                excerpts = []
                for value in context:
                    sender, separator, text = value.partition(":")
                    if separator and sender.isdigit():
                        excerpts.append(f"{labels.get(int(sender), 'Участник')}: {text}")
                content = details or ""
                if excerpts:
                    content += ("\n\n" if content else "") + "Последние сообщения:\n" + "\n".join(excerpts)
                report = Report(reporter_id=user_id, reported_id=partner, chat_id=chat.id,
                    chat_session_id=session_id, reason=REASONS[reason], message_content=content or None)
                s.add(report)
                if not await s.get(Block, (user_id, partner)):
                    s.add(Block(blocker_id=user_id, blocked_id=partner, reason=reason))
                await s.flush()
                report_id = report.id
                import config
                if config.OWNER_ID:
                    from services.notifications import enqueue
                    await enqueue(s, config.OWNER_ID, "owner_report", f"owner_report:{report_id}",
                                  f"🚩 Новая жалоба #{report_id}: {REASONS[reason]}.", "admin:reports:0")
        except IntegrityError:
            return ServiceResult(False, "ALREADY_REPORTED")
        await ChatService().end_chat(user_id, session_id, "report")
        await relay.report_context(session_id, clear=True)
        logging.getLogger(__name__).info("report_created", extra={"user_id": user_id, "chat_session_id": session_id, "report_id": report_id})
        return ServiceResult(True, "REPORTED", {"partner": partner, "report_id": report_id})

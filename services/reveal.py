from sqlalchemy import select, or_
from models.base import async_session
from models.chat import Chat, RevealRequest
from models.user import User
from services.result import ServiceResult
from utils.time import utcnow


class RevealService:
    async def consent(self, user_id, session_id):
        async with async_session.begin() as s:
            chat = await s.scalar(select(Chat).where(Chat.session_id == session_id, Chat.status == "active",
                or_(Chat.user1_id == user_id, Chat.user2_id == user_id)).with_for_update())
            if not chat:
                return ServiceResult(False, "SESSION_EXPIRED")
            request = await s.get(RevealRequest, session_id)
            if request is None:
                request = RevealRequest(chat_session_id=session_id, consent_a=False, consent_b=False)
                s.add(request)
            elif request.status != "pending":
                return ServiceResult(False, request.status.upper())
            field = "consent_a" if user_id == chat.user1_id else "consent_b"
            if getattr(request, field):
                return ServiceResult(False, "ALREADY_REQUESTED")
            setattr(request, field, True)
            partner = chat.user2_id if user_id == chat.user1_id else chat.user1_id
            if request.consent_a and request.consent_b:
                request.status, request.resolved_at = "mutual", utcnow()
                users = (await s.scalars(select(User).where(User.telegram_id.in_([user_id, partner])))).all()
                from services.growth import award
                from sqlalchemy import func
                await s.flush()
                for user in users:
                    await award(s,user.telegram_id,"reveal_1")
                    count=await s.scalar(select(func.count(RevealRequest.chat_session_id)).join(Chat,Chat.session_id==RevealRequest.chat_session_id).where(RevealRequest.status=="mutual",or_(Chat.user1_id==user.telegram_id,Chat.user2_id==user.telegram_id)))
                    if count>=5:
                        await award(s,user.telegram_id,"reveal_5")
                return ServiceResult(True, "MUTUAL", {
                    "profiles": {u.telegram_id: {
                        "username": u.username, "photo": u.profile_photo,
                        "anon_id": u.anon_id,
                    } for u in users},
                    "partner": partner,
                })
            from services.notifications import enqueue
            await enqueue(s, partner, "reveal_request", f"reveal:{session_id}:{partner}",
                "❤️ Собеседник предложил взаимно раскрыть контакты.", session_id)
            return ServiceResult(True, "PENDING", {"partner": partner})

    async def decline(self, user_id, session_id):
        async with async_session.begin() as s:
            chat = await s.scalar(select(Chat).where(
                Chat.session_id == session_id, Chat.status == "active",
                or_(Chat.user1_id == user_id, Chat.user2_id == user_id)).with_for_update())
            if not chat:
                return ServiceResult(False, "SESSION_EXPIRED")
            request = await s.get(RevealRequest, session_id)
            if not request or request.status != "pending":
                return ServiceResult(False, "NOT_PENDING")
            own = request.consent_a if user_id == chat.user1_id else request.consent_b
            other = request.consent_b if user_id == chat.user1_id else request.consent_a
            if own or not other:
                return ServiceResult(False, "NOT_ALLOWED")
            requester = chat.user2_id if user_id == chat.user1_id else chat.user1_id
            request.status, request.resolved_at = "declined", utcnow()
            from services.notifications import enqueue
            await enqueue(s, requester, "reveal_declined",
                          f"reveal_declined:{session_id}:{requester}",
                          "Собеседник отказался раскрывать контакт.")
            return ServiceResult(True, "DECLINED")

    async def status(self, user_id, session_id):
        from repositories.chats import ChatRepository
        chat = await ChatRepository().get(session_id, user_id)
        if not chat or chat.status != "active":
            return ServiceResult(False, "SESSION_EXPIRED")
        async with async_session() as s:
            request = await s.get(RevealRequest, session_id)
            return ServiceResult(True, request.status.upper() if request else "NOT_REQUESTED")


async def upgrade_request(user_id, session_id, kind):
    from services.payments import available_entitlement
    from repositories.chats import premium_active
    async with async_session.begin() as s:
        chat = await s.scalar(select(Chat).where(Chat.session_id == session_id, Chat.status == "active",
            or_(Chat.user1_id == user_id, Chat.user2_id == user_id)).with_for_update())
        if not chat:
            return ServiceResult(False, "SESSION_EXPIRED")
        user = await s.scalar(select(User).where(User.telegram_id == user_id))
        entitlement = await available_entitlement(s, user_id, [kind], target_session=session_id)
        if not premium_active(user) and (not entitlement or entitlement.target_session != session_id):
            return ServiceResult(False, "NOT_PAID")
        request = await s.get(RevealRequest, session_id)
        if kind == "reveal_priority_request":
            if not request:
                request = RevealRequest(chat_session_id=session_id, consent_a=False, consent_b=False, priority=True)
                s.add(request)
            request.priority = True
            return ServiceResult(True, "PRIORITY")
        return ServiceResult(True, request.status.upper() if request else "NOT_REQUESTED")

import logging
from typing import Any, Awaitable, Callable
from aiogram import BaseMiddleware
from aiogram.types import Message
from services.chat_manager import check_flood

logger = logging.getLogger(__name__)


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from aiogram.types import CallbackQuery
        from keyboards import texts as T
        from keyboards.reply import chat_kb_reply
        from repositories.users import UserRepository
        from services.onboarding import step_for, banned
        from services.result import ServiceResult
        from handlers.start import show_step, onboarding_input
        from services.chat_manager import get_partner, set_online
        from config import ADMIN_IDS

        actor = event.from_user
        if not actor or getattr(event, "successful_payment", None) or getattr(event, "refunded_payment", None):
            return await handler(event, data)
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        if chat and chat.type != "private":
            await event.answer("Анонимный чат доступен только в личной переписке с ботом.")
            return
        text = getattr(event, "text", "") or ""
        callback = isinstance(event, CallbackQuery)
        command = text.split(maxsplit=1)[0].split("@")[0] if text.startswith("/") else ""
        action = event.data if callback else command or text
        # Never log message text: relayed content must not reach container logs.
        logger.debug("event=access_check callback=%s user_id=%s", callback, actor.id)
        if action in {"/help", "/rules", T.HELP, T.RULES, "rules", "help"}:
            return await handler(event, data)
        admin_commands = {"/admin", "/user", "/ban_user", "/unban", "/grant", "/price", "/setting", "/season"}
        if actor.id in ADMIN_IDS and (action.split()[0].split("@")[0] in admin_commands if action else False):
            return await handler(event, data)
        if actor.id in ADMIN_IDS and (action.startswith("admin:") or action.startswith("bc:")):
            return await handler(event, data)
        user, _ = await UserRepository().get_or_create(actor.id, actor.username, actor.first_name)
        data["anon_id"] = getattr(user, "anon_id", None)
        if banned(user):
            if callback:
                await event.answer("⛔ Ты заблокирован.", show_alert=True)
            else:
                await show_step(event, data["state"], ServiceResult(False, "BANNED", {"until": user.ban_expires}))
            return
        await set_online(actor.id)
        step = step_for(user)
        from utils.time import utcnow
        if step == "MENU" and getattr(user, "last_active_date", None) != utcnow().date():
            from services.growth import StreakService
            await StreakService().checkin(actor.id)
        if step != "MENU" and command != "/start":
            if callback:
                await event.answer("Сначала заверши регистрацию.")
                await show_step(event.message, data["state"], ServiceResult(False, step))
            else:
                await data["state"].set_state(f"Onboarding:{step}")
                await onboarding_input(event, data["state"])
            return
        if step == "MENU" and (await data["state"].get_state() or "").startswith("Onboarding:"):
            await data["state"].clear()
            # Same reason as below: the FSM middleware already cached this state
            # snapshot before access checks ran, so the router would otherwise
            # still dispatch this update to the abandoned onboarding handler.
            data["raw_state"] = None
        navigation = {T.MENU, T.PROFILE, T.SETTINGS, T.PREMIUM, T.STATS, "menu", "profile", "settings", "premium", "stats", "/profile"}
        if action in navigation or command == "/start":
            if await get_partner(actor.id):
                if callback:
                    await event.answer("Сначала заверши текущий диалог.", show_alert=True)
                else:
                    await event.answer("Ты сейчас в диалоге. Нажми «Стоп», чтобы открыть меню.", reply_markup=chat_kb_reply())
                return
        chat_controls = {
            T.SEARCH, T.CANCEL, T.STOP, T.NEXT, T.REVEAL, T.REPORT,
            "/search", "/stop", "/report", "search:start", "search:cancel",
            "chat:stop", "chat:next", "chat:reveal",
        }
        if action in chat_controls or action in navigation:
            await data["state"].clear()
            # FSM middleware loaded this snapshot before access checks.
            data["raw_state"] = None
        return await handler(event, data)


class AntiFloodMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if getattr(event, "successful_payment", None) or getattr(event, "refunded_payment", None):
            return await handler(event, data)
        if event.from_user and await check_flood(event.from_user.id):
            if hasattr(event, "data"):
                await event.answer("Подожди немного перед следующим действием.")
            return None
        return await handler(event, data)

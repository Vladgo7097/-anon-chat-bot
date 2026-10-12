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
        from locales import normalize
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
            # No stored language this early, so the client's own code decides.
            await event.answer(T.tr(T.GUARDS, normalize(
                getattr(actor, "language_code", None)))["private_only"])
            return
        text = getattr(event, "text", "") or ""
        callback = isinstance(event, CallbackQuery)
        command = text.split(maxsplit=1)[0].split("@")[0] if text.startswith("/") else ""
        action = event.data if callback else command or text
        # Never log message text: relayed content must not reach container logs.
        logger.debug("event=access_check callback=%s user_id=%s", callback, actor.id)
        if action in (T.all_labels(T.HELP) | T.all_labels(T.RULES) | {"/help", "/rules", "rules", "help"}):
            # Rules and help stay readable before registration, so this path deliberately
            # skips the onboarding gate below -- but it must not skip the reader's own
            # language. `lang` is only assigned after that gate, so without this the help
            # and rules handlers fell through to their Russian default for every client
            # running in en/uk, including the inline "help" and "rules" taps.
            row = await UserRepository().get(actor.id)
            data["anon_id"] = getattr(row, "anon_id", None)
            data["lang"] = getattr(row, "language", None) or normalize(
                getattr(actor, "language_code", None))
            return await handler(event, data)
        admin_commands = {"/admin", "/user", "/ban_user", "/unban", "/grant", "/price", "/setting", "/season"}
        if actor.id in ADMIN_IDS and (action.split()[0].split("@")[0] in admin_commands if action else False):
            return await handler(event, data)
        if actor.id in ADMIN_IDS and (action.startswith("admin:") or action.startswith("bc:")):
            return await handler(event, data)
        user, _ = await UserRepository().get_or_create(actor.id, actor.username, actor.first_name)
        data["anon_id"] = getattr(user, "anon_id", None)
        lang = getattr(user, "language", None) or "ru"
        data["lang"] = lang
        if banned(user):
            if callback:
                await event.answer(T.tr(T.GUARDS, lang)["banned"], show_alert=True)
            else:
                await show_step(event, data["state"], ServiceResult(False, "BANNED", {"until": user.ban_expires}), lang=lang)
            return
        await set_online(actor.id)
        step = step_for(user)
        # The registration's own taps must always reach their handlers: this
        # block used to swallow the `lang:*` callback (a CallbackQuery carries
        # no text, so command stayed "") and re-sent the language screen on
        # every tap -- the flow dead-ended on its very first step.
        onboarding_tap = callback and (action.startswith("lang:")
                                       or action in {"rules", "rules:ok", "back:start", "menu"})
        if step != "MENU" and command != "/start" and not onboarding_tap:
            if callback:
                await event.answer(T.tr(T.GUARDS, lang)["register_first"])
                from handlers.start import callback_target
                await show_step(callback_target(event), data["state"], ServiceResult(False, step), lang=lang)
            else:
                await data["state"].set_state(f"Onboarding:{step}")
                await onboarding_input(event, data["state"], lang=lang)
            return
        from utils.time import utcnow
        if step == "MENU" and getattr(user, "last_active_date", None) != utcnow().date():
            from services.growth import StreakService
            await StreakService().checkin(actor.id)
        if step == "MENU" and (await data["state"].get_state() or "").startswith("Onboarding:"):
            await data["state"].clear()
            # Same reason as below: the FSM middleware already cached this state
            # snapshot before access checks ran, so the router would otherwise
            # still dispatch this update to the abandoned onboarding handler.
            data["raw_state"] = None
        navigation = (T.all_labels(T.MENU) | T.all_labels(T.PROFILE) | T.all_labels(T.SETTINGS)
                      | T.all_labels(T.PREMIUM) | T.all_labels(T.STATS)
                      | T.all_labels(T.CHAT) | T.all_labels(T.INVITE)
                      | {"menu", "profile", "settings", "premium", "stats", "/profile"})
        if action in navigation or command == "/start":
            if await get_partner(actor.id):
                if callback:
                    await event.answer(T.tr(T.GUARDS, lang)["finish_chat"], show_alert=True)
                else:
                    await event.answer(T.tr(T.GUARDS, lang)["in_chat"], reply_markup=chat_kb_reply(lang))
                return
        chat_controls = (
            T.all_labels(T.SEARCH) | T.all_labels(T.CANCEL) | T.all_labels(T.STOP)
            | T.all_labels(T.NEXT) | T.all_labels(T.REVEAL) | T.all_labels(T.REPORT)
            | {"/search", "/stop", "/report", "search:start", "search:cancel",
               "chat:stop", "chat:next", "chat:reveal"}
        )
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
            from keyboards import texts as T
            from locales import normalize
            # Both attached observers land here. A Message carries no `.data`, so
            # the old guard stayed silent exactly while a reply-keyboard menu was
            # being hammered; a CallbackQuery still got its toast. AccessMiddleware
            # runs first and leaves `lang` behind whenever it resolved one.
            lang = data.get("lang") or normalize(getattr(event.from_user, "language_code", None))
            await event.answer(T.tr(T.GUARDS, lang)["slow_down"])
            return None
        return await handler(event, data)

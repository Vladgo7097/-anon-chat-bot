"""ОП: экран требования у пользователя и команды владельца.

Пользовательская ветка обязана доходить до роутера даже при закрытом
гейте: AccessMiddleware пропускает op:* без проверки подписки -- иначе
кнопка «Проверить подписку» не смогла бы ничего перепроверить. Результат
проверки обновляется в том же сообщении, а не новым (ТЗ: не засорять диалог).

Управление живёт в командах, а не в админ-панели: /setup запускает ОП на
чате (с необязательным сроком или своей ссылкой), /unsetup снимает, /op
показывает активные, /id объясняет, как узнать ID. Команды видит только
владелец -- тот же AdminOnly, что у /admin. Перехват форвардов висит на
start_router: он идёт до чат-релея, иначе пересланное из канала сообщение
уехало бы собеседнику.
"""

import logging

from aiogram import F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from handlers import op_admin_router, op_router, start_router
from handlers.admin import AdminOnly, authorize
from services import op as op_service

logger = logging.getLogger(__name__)

# Та же защита, что у /admin: команды ОП открыты только ADMIN_IDS.
op_admin_router.message.middleware(AdminOnly())
op_admin_router.callback_query.middleware(AdminOnly())

SETUP_HELP = (
    "⚙ Запуск ОП (Отправьте команду прямо здесь)\n"
    "/setup <@username или ID> [ссылка] [лимит]\n"
    "Примеры:\n"
    "/setup @username — без ограничений\n"
    "/setup @username 1d — отключится через 24 ч\n"
    "/setup -100199452 https://t.me/+abc123 2h — со своей ссылкой\n\n"
    "Формат времени: 30s · 15m · 2h · 1d\n\n"
    "🛑 Отключение ОП\n"
    "/unsetup @username — отключить конкретную\n"
    "/unsetup — отключить все\n"
    "Или через меню «Активные ОП» — /op\n\n"
    "ℹ️ Как узнать ID канала/группы — /id"
)
ID_HELP = (
    "ℹ️ Как узнать ID канала/группы\n"
    "Перешли мне любое сообщение из нужного канала — пришлю его ID.\n"
    "Каналу с @username ID не нужен: /setup @username сразу подойдёт.\n\n"
    "Подписки читаются только с правами администратора: добавь бота в чат "
    "админом до /setup."
)
PRIVATE_ONLY = "Команды ОП работают в личке бота."
ID_ORIGIN_KINDS = {"channel", "group", "supergroup"}


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


def _is_invite_link(text: str) -> bool:
    clean = text.strip().lower().replace("https://", "").replace("http://", "")
    return clean.startswith(("t.me/+", "t.me/joinchat"))


def _chat_kind(type_value) -> str:
    return getattr(type_value, "value", type_value) or ""


# ── Команды владельца ────────────────────────────────────────────


@op_admin_router.message(Command("setup"))
async def cmd_setup(message: Message):
    if not _is_private(message):
        await message.answer(PRIVATE_ONLY)
        return
    args = (message.text or "").split()
    if len(args) < 2:
        await message.answer(SETUP_HELP)
        return
    target_raw, rest = args[1], args[2:]
    link = None
    if rest and _is_invite_link(rest[0]):
        link = rest.pop(0)
    limit = None
    if rest:
        try:
            limit = op_service.parse_limit(rest[0])
        except ValueError:
            await message.answer(
                "⏱ Не понял лимит. Формат: 30s · 15m · 2h · 1d.\n"
                "Пример: /setup @username 1d"
            )
            return
    if len(rest) > 1:
        await message.answer(
            "Лишние аргументы. Формат: /setup <@username или ID> [ссылка] [лимит]"
        )
        return
    target = op_service.parse_chat_ref(target_raw)
    if target is None:
        await message.answer(
            "Не понял цель. Пришли @username, t.me/username или ID чата "
            "(-100…) — подсказку даёт /id.\n"
            "Пригласительную ссылку Telegram боту не отдаёт: передай её "
            "вторым аргументом вместе с ID:\n"
            "/setup -100199452 https://t.me/+abc123 1d"
        )
        return
    ok, note = await op_service.setup_chat(
        message.bot,
        message.bot.id,
        target,
        message.from_user.id,
        link=link,
        limit=limit,
    )
    logger.info("event=op_setup bot_id=%s target=%s ok=%s", message.bot.id, target, ok)
    await message.answer(note)


@op_admin_router.message(Command("unsetup"))
async def cmd_unsetup(message: Message):
    if not _is_private(message):
        await message.answer(PRIVATE_ONLY)
        return
    args = (message.text or "").split()
    if len(args) < 2:
        count = await op_service.unsetup_all(message.bot.id, message.from_user.id)
        if count:
            await message.answer(f"⛔ Все ОП отключены (ресурсов снято: {count}).")
        else:
            await message.answer("📭 Активных ОП нет — отключать нечего.")
        return
    target = op_service.parse_chat_ref(args[1])
    if target is None:
        await message.answer(
            "Не понял цель — пришли @username, t.me/username или ID чата.\n"
            "Подсказку даёт /id."
        )
        return
    _ok, note = await op_service.unsetup_target(
        message.bot, message.bot.id, target, message.from_user.id
    )
    await message.answer(note)


async def _owner_view(bot_id):
    settings, chats = await op_service.op_state(bot_id)
    return op_service.owner_list_text(settings, chats), op_service.owner_list_kb(
        settings, chats
    )


@op_admin_router.message(Command("op"))
async def cmd_op(message: Message):
    if not _is_private(message):
        await message.answer(PRIVATE_ONLY)
        return
    text, markup = await _owner_view(message.bot.id)
    await message.answer(text, reply_markup=markup)


@op_admin_router.message(Command("id"))
async def cmd_id(message: Message):
    if not _is_private(message):
        await message.answer(PRIVATE_ONLY)
        return
    await message.answer(ID_HELP)


@start_router.message(F.chat.type == "private", F.forward_origin)
async def forwarded_id(message: Message):
    """Владельцу -- ID пересланного канала; остальных не трогаем.

    Bot API не умеет перечислять чаты владельца, зато форвард несёт
    origin.chat. Роутер ранний не случайно: чат-релей ниже перехватил бы
    форвард первым, и сообщение уехало бы собеседнику.

    Не владелец или форвард не из чата -- SkipHandler: дальше по цепочке
    сообщение обрабатывается как обычно."""
    try:
        authorize(message.from_user.id)
    except PermissionError:
        raise SkipHandler() from None
    origin = message.forward_origin
    chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
    if chat is None or _chat_kind(getattr(chat, "type", None)) not in ID_ORIGIN_KINDS:
        raise SkipHandler()
    title = (
        getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat.id)
    )
    note = f"🆔 {chat.id}\n🅰️ {title}\n\nЗапуск: /setup {chat.id} [лимит]"
    username = getattr(chat, "username", None)
    if username:
        note += f"\nИли короче: /setup @{username}"
    await message.answer(note)


# ── Кнопки меню «Активные ОП» ────────────────────────────────────


async def _show(callback: CallbackQuery, text: str, markup) -> None:
    message = callback.message
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    from handlers.start import callback_target

    await callback_target(callback).answer(text, reply_markup=markup)


@op_admin_router.callback_query(F.data == "op_owner:list")
async def cb_owner_list(callback: CallbackQuery):
    await callback.answer()
    text, markup = await _owner_view(callback.bot.id)
    await _show(callback, text, markup)


@op_admin_router.callback_query(F.data.startswith("op_owner:off:"))
async def cb_owner_off(callback: CallbackQuery):
    chat_id = int(callback.data.rsplit(":", 1)[1])
    _ok, note = await op_service.unsetup_chat(
        callback.bot.id, chat_id, callback.from_user.id
    )
    await callback.answer(note[:190])
    text, markup = await _owner_view(callback.bot.id)
    await _show(callback, text, markup)


@op_admin_router.callback_query(F.data == "op_owner:off_all")
async def cb_owner_off_all(callback: CallbackQuery):
    count = await op_service.unsetup_all(callback.bot.id, callback.from_user.id)
    await callback.answer(f"Отключено: {count}" if count else "Активных не было")
    text, markup = await _owner_view(callback.bot.id)
    await _show(callback, text, markup)


@op_admin_router.callback_query(F.data == "op_owner:notify")
async def cb_owner_notify(callback: CallbackQuery):
    if not await op_service.requirement_active(callback.bot.id):
        await callback.answer(
            "ОП сейчас не активна — уведомлять не о чем.", show_alert=True
        )
        return
    total = await op_service.request_notify(callback.bot.id, callback.from_user.id)
    await callback.answer(f"Рассылка запущена: {total} чел.")
    text, markup = await _owner_view(callback.bot.id)
    await _show(callback, text, markup)


@op_admin_router.callback_query(F.data == "op_owner:notify_stop")
async def cb_owner_notify_stop(callback: CallbackQuery):
    await op_service.stop_notify(callback.bot.id, callback.from_user.id)
    await callback.answer("Рассылка остановлена.")
    text, markup = await _owner_view(callback.bot.id)
    await _show(callback, text, markup)


# ── Пользовательская часть ───────────────────────────────────────


@op_router.callback_query(F.data == "op:check")
async def cb_op_check(callback: CallbackQuery, state: FSMContext, lang: str = "ru"):
    gate = await op_service.op_gate(callback.bot, callback.from_user.id, force=True)
    await callback.answer()
    from handlers.start import callback_target, enter_bot

    target = callback_target(callback)
    if not gate.blocked:
        text = (
            op_service.tr(op_service.OK_TEXT, lang)
            if gate.required
            else op_service.tr(op_service.OFF_TEXT, lang)
        )
        message = callback.message
        if message is not None:
            try:
                await message.edit_text(text)
            except TelegramBadRequest:
                await target.answer(text)
        else:
            await target.answer(text)
        await enter_bot(target, state, callback.from_user, callback.bot)
        return
    await _edit_requirement(callback, gate, lang)


async def _edit_requirement(callback: CallbackQuery, gate, lang: str) -> None:
    text = op_service.op_screen_text(gate, lang)
    markup = op_service.op_screen_kb(gate, lang)
    message = callback.message
    if message is not None:
        try:
            await message.edit_text(text, reply_markup=markup)
            return
        except TelegramBadRequest:
            pass
    from handlers.start import callback_target

    await callback_target(callback).answer(text, reply_markup=markup)

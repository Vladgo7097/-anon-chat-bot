import logging
import re
from html import escape
import config
from aiogram import F, BaseMiddleware
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from handlers import admin_router
from keyboards import texts as T
from keyboards.inline import admin_kb, admin_report_kb, admin_broadcast_audience_kb, admin_user_card_kb, admin_broadcast_status_kb, admin_ads_kb, admin_ad_card_kb, admin_ads_settings_kb, admin_ads_settings_cancel_kb
from services.admin import AdminService, StatsService, authorize
from services.ads import AdCampaignService, CHANNEL_USERNAME_RE
from services.payments import PaymentService
from services.chat_manager import get_online_count, get_partner


logger = logging.getLogger(__name__)
CUSTOM_EMOJI = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>')


async def safe_send(message, text, **kwargs):
    """Send text, falling back to the plain emoji if Telegram refuses an id.

    One unusable custom emoji id makes Telegram reject the entire message with
    DOCUMENT_INVALID, which left the admin panel unreachable. The fallback keeps
    the emoji character and drops only the tag around it, so the panel still
    opens while the bad id is fixed.
    """
    try:
        return await message.answer(text, **kwargs)
    except TelegramBadRequest as error:
        if "DOCUMENT_INVALID" not in str(error):
            raise
        logger.warning("event=custom_emoji_rejected chat_id=%s", message.chat.id)
        return await message.answer(CUSTOM_EMOJI.sub(r"\1", text), **kwargs)


class AdminOnly(BaseMiddleware):
    async def __call__(self,handler,event,data):
        try:
            authorize(event.from_user.id)
        except PermissionError:
            await event.answer("⛔ Нет доступа.")
            return
        return await handler(event,data)


admin_router.message.middleware(AdminOnly())
admin_router.callback_query.middleware(AdminOnly())


class AdminBroadcast(StatesGroup):
    CONTENT=State()


class AdminMessage(StatesGroup):
    CONTENT=State()


class AdminAdCampaign(StatesGroup):
    CHANNEL=State()
    TITLE=State()
    SUBTITLE=State()
    BODY=State()
    LIMITS=State()


class AdminAdSettings(StatesGroup):
    COOLDOWN=State()


@admin_router.message(Command("season"))
async def cmd_season(message: Message):
    parts=message.text.split()
    if len(parts)!=5 or not parts[4].isdigit():
        await message.answer("/season код начало_ISO конец_ISO скидка\nДаты с часовым поясом, например 2026-10-01T00:00:00+00:00. Включение: /setting seasonal_enabled 1")
        return
    result=await AdminService().seasonal(message.from_user.id,parts[1],parts[2],parts[3],int(parts[4]))
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Событие сохранено. Оно действует только в заданный период и при seasonal_enabled=1." if result.ok else "Проверь код, даты с часовым поясом и скидку 1–90%.")


def overview_text(s):
    metric=lambda key: f"{s[key]}%" if s.get(key) is not None else "— (недостаточно данных)"
    growth=metric('growth_7d_pct') if s.get('growth_7d_pct') is not None else "— (недостаточно данных за прошлую неделю)"
    stars_usd=f" (≈${s['stars_usd']})" if s.get('stars_usd') is not None else ""
    return f"<tg-emoji emoji-id=\"6129805886383723340\">🛠</tg-emoji> Админ-панель\n\n<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Пользователей: {s['users']}\n<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> Новых за период: {s['new']}\nНовых за 7 дней: {s['new_7d']} ({growth} к предыдущим 7 дням)\nРегистрацию завершили: {s['onboarding_completed']} ({metric('onboarding_conversion')})\nНачали поиск: {s['searchers']}\nПолучили пару: {s['matched']} ({metric('search_conversion')})\n<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Завершённых диалогов: {s['chats']}\n<tg-emoji emoji-id=\"5258258882022612173\">⏱</tg-emoji> Средняя длительность: {s['avg_seconds']} сек.\n<tg-emoji emoji-id=\"5312160339335347417\">💰</tg-emoji> Оплатили: {s['buyers']} ({metric('purchase_conversion')})\nПолучено: {s['stars']}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>{stars_usd}\n<tg-emoji emoji-id=\"5283130719806175947\">💎</tg-emoji> Активных Premium: {s['premium']}\n<tg-emoji emoji-id=\"5350417283783084711\">👍</tg-emoji> Средний рейтинг: {metric('rating')}\nВозврат на следующий день: {metric('retention_d1')}\nВозврат через 7 дней: {metric('retention_d7')}"


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message,state: FSMContext):
    await state.clear()
    s=await StatsService().overview()
    await safe_send(message, overview_text(s)+f"\n<tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Онлайн: {await get_online_count()}",reply_markup=admin_kb())


@admin_router.callback_query(F.data=="admin:back")
async def cb_admin_back(callback: CallbackQuery):
    await callback.answer()
    s=await StatsService().overview()
    await safe_send(callback.message, overview_text(s)+f"\n<tg-emoji emoji-id=\"5215522595922779944\">🟢</tg-emoji> Онлайн: {await get_online_count()}",reply_markup=admin_kb())


def funnel_text(f, days, min_step_pct=0):
    total = f["stages"][0][1]
    head = f"<tg-emoji emoji-id=\"5399909394525737759\">📉</tg-emoji> Воронка регистрации за {days} дн."
    if not total:
        return head+"\n\nЗа период никто не запускал бота."
    lines, warnings, previous, previous_label = [], [], None, None
    for label, count in f["stages"]:
        step_pct = round(100*count/previous) if previous else 0
        share = f"{round(100*count/total)}% от всех"
        if previous is not None:
            share += f", {step_pct}% от предыдущего"
            if min_step_pct and previous and step_pct < min_step_pct:
                warnings.append(f"⚠️ Просадка на шаге «{label}»: {step_pct}% от «{previous_label}» (порог {min_step_pct}%)")
        lines.append(f"{label}: {count} ({share})")
        previous, previous_label = count, label
    text = head+"\n\n"+"\n".join(lines)
    if warnings:
        text += "\n\n"+"\n".join(warnings)
    text += f"\n\nНе нажали ни одной кнопки после /start: {f['stages'][0][1]-f['stages'][1][1]}"
    if f["age_rejected"]:
        text += f"\nОтклонено на шаге возраста: {f['age_rejected']}"
    if f["avg_minutes"] is not None:
        text += f"\nСреднее время регистрации: {f['avg_minutes']} мин."
    if len(f["daily"]) > 1:
        text += "\n\nПо дням (новых / дошли до конца):\n"+"\n".join(
            f"{day}: {new} / {done} ({round(100*done/new) if new else 0}%)"
            for day, new, done in f["daily"])
    return text


def funnel_kb(days):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Сегодня", callback_data="admin:funnel:1"),
        InlineKeyboardButton(text="7 дней", callback_data="admin:funnel:7"),
        InlineKeyboardButton(text="30 дней", callback_data="admin:funnel:30")], [
        InlineKeyboardButton(text="События", callback_data=f"admin:events:{days}"),
        InlineKeyboardButton(text="Назад", callback_data="admin:back")]])


@admin_router.callback_query(F.data=="admin:funnel")
@admin_router.callback_query(F.data.startswith("admin:funnel:"))
async def cb_funnel(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    days=int(value) if value.isdigit() else 1
    if days not in {1,7,30}:
        await callback.answer("Недопустимый период.")
        return
    from services.settings import bot_value
    min_step_pct = await bot_value("funnel_min_step_pct", 0)
    await callback.answer()
    await safe_send(callback.message, funnel_text(await StatsService().onboarding_funnel(days),days,min_step_pct),
                                  reply_markup=funnel_kb(days))


@admin_router.callback_query(F.data.in_({"admin:stats","admin:money"}))
@admin_router.callback_query(F.data.startswith("admin:events:"))
@admin_router.callback_query(F.data.startswith("admin:period:"))
async def cb_stats(callback: CallbackQuery):
    days=int(callback.data.split(":")[-1]) if callback.data.split(":")[-1].isdigit() else 1
    if days not in {1,7,30}:
        await callback.answer("Недопустимый период.")
        return
    s=await StatsService().overview(days)
    text=overview_text(s)
    if callback.data=="admin:money":
        text+="\n\nПо продуктам:\n"+"\n".join(
            f"{k}: {v}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> · "
            f"{s['product_orders'].get(k, 0)} покупок · {s['product_shares'].get(k, 0)}%"
            for k,v in s['products'].items())
    if callback.data.startswith("admin:events:"):
        text+="\n\nУникальные пользователи по событиям:\n"+"\n".join(f"{k}: {v}" for k,v in s['funnel'].items())
    await callback.answer()
    await safe_send(callback.message, text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="За неделю",callback_data="admin:period:7"),
        InlineKeyboardButton(text="За месяц",callback_data="admin:period:30"),
        InlineKeyboardButton(text="CSV",callback_data=f"admin:export:{days}")],[
        InlineKeyboardButton(text="Назад",callback_data="admin:back")]]))


@admin_router.callback_query(F.data.startswith("admin:export:"))
async def cb_export(callback: CallbackQuery):
    import csv,io
    value=callback.data.split(":")[-1]
    if value not in {"1","7","30"}:
        await callback.answer("Недопустимый период")
        return
    s=await StatsService().overview(int(value))
    buffer=io.StringIO()
    writer=csv.writer(buffer)
    writer.writerow(["metric","value"])
    writer.writerows((k,v) for k,v in s.items() if not isinstance(v,dict))
    await callback.answer()
    await callback.message.answer_document(BufferedInputFile(buffer.getvalue().encode('utf-8-sig'),filename='statistics.csv'))


@admin_router.callback_query(F.data=="admin:reports")
@admin_router.callback_query(F.data.startswith("admin:reports:"))
async def cb_reports(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    offset=int(value) if value.isdigit() else 0
    reports=await AdminService().reports(callback.from_user.id,offset)
    await callback.answer()
    if not reports:
        await callback.message.answer("<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> Жалоб нет.",reply_markup=admin_kb())
        return
    r=reports[0]
    names,previous,bans=await AdminService().report_details(callback.from_user.id,r)
    await callback.message.answer(f"<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> Жалоба #{r.id}\nОт: {escape(names.get(r.reporter_id,'Удалённый профиль'))}\nНа: {escape(names.get(r.reported_id,'Удалённый профиль'))}\nПричина: {escape(r.reason)}\nДетали: {escape(r.message_content or 'Не указаны')}\nИстория: {previous} других жалоб, {bans} банов\nДата: {r.created_at:%d.%m.%Y %H:%M} UTC",reply_markup=admin_report_kb(r.id,offset+1))


@admin_router.callback_query(F.data.startswith("admin:ban1:"))
@admin_router.callback_query(F.data.startswith("admin:banf:"))
@admin_router.callback_query(F.data.startswith("admin:dismiss:"))
async def cb_resolve(callback: CallbackQuery):
    parts=callback.data.split(":")
    if not parts[-1].isdigit():
        await callback.answer("Недопустимая жалоба")
        return
    result=await AdminService().resolve(callback.from_user.id,int(parts[-1]),parts[1])
    await callback.answer("Решение сохранено" if result.ok else "Жалоба уже обработана")
    await callback.message.edit_reply_markup(reply_markup=None)


@admin_router.callback_query(F.data=="admin:bans")
async def cb_bans(callback: CallbackQuery):
    users=await AdminService().bans(callback.from_user.id)
    await callback.answer()
    text="\n".join(f"{u.anon_id} — "+(f"до {u.ban_expires:%d.%m.%Y}" if u.ban_expires else "навсегда") for u in users)
    await callback.message.answer("<tg-emoji emoji-id=\"5312229088876847138\">🚫</tg-emoji> Активные баны (до 50):\n"+text+"\n\n/ban_user ID часы причина\n/unban ID")


@admin_router.callback_query(F.data=="admin:users")
async def cb_users(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("<tg-emoji emoji-id=\"5215522595922779944\">👥</tg-emoji> Введи anon_id или telegram_id пользователя:")


async def user_card_text(admin_id, user):
    reports_count = await AdminService().reports_against(admin_id, user.telegram_id)
    premium = f"до {user.premium_expires:%d.%m.%Y}" if user.premium_expires else "нет"
    return (
        f"<tg-emoji emoji-id=\"5348232622898167572\">👤</tg-emoji> {escape(user.anon_id or '?')}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"<tg-emoji emoji-id=\"5258105663359294787\">📅</tg-emoji> Регистрация: {user.created_at:%d.%m.%Y}\n"
        f"<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Диалогов: {user.chats_count}\n"
        f"<tg-emoji emoji-id=\"5280880410346152584\">🚩</tg-emoji> Жалоб на него: {reports_count}\n"
        f"<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> Premium: {premium}\n"
        f"<tg-emoji emoji-id=\"5312160339335347417\">💰</tg-emoji> Всего потратил: {user.total_stars_spent}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>")


@admin_router.callback_query(F.data.startswith("admin:user_card:"))
async def cb_user_card(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    user=await AdminService().find_user(callback.from_user.id,value)
    if not user:
        await callback.answer("Пользователь не найден",show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(await user_card_text(callback.from_user.id, user),
        reply_markup=admin_user_card_kb(user.telegram_id))


@admin_router.callback_query(F.data.startswith("admin:message:"))
async def cb_message_user(callback: CallbackQuery, state: FSMContext):
    target_id=int(callback.data.split(":")[-1])
    await state.set_state(AdminMessage.CONTENT)
    await state.update_data(target_id=target_id)
    await callback.answer()
    await callback.message.answer(f"<tg-emoji emoji-id=\"5224455462178549802\">💬</tg-emoji> Введи текст сообщения для пользователя {target_id}:")


@admin_router.message(AdminMessage.CONTENT, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def send_message_to_user(message: Message, state: FSMContext):
    data=await state.get_data()
    target_id=data.get("target_id")
    await state.clear()
    if not target_id:
        await message.answer("Ошибка: не указан получатель.")
        return
    import uuid
    request_key=f"msg:{message.chat.id}:{message.message_id}:{uuid.uuid4().hex[:8]}"
    result=await AdminService().send_message_to_user(message.from_user.id,target_id,message.text,request_key=request_key)
    if result.ok:
        await message.answer(f"<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сообщение отправлено пользователю {target_id}.")
    else:
        await message.answer(f"❌ Ошибка: {result.code}")


@admin_router.callback_query(F.data.startswith("admin:uban:"))
async def cb_admin_ban_user(callback: CallbackQuery):
    target_id=int(callback.data.split(":")[-1])
    await callback.answer()
    await callback.message.answer(
        f"⛔ Забанить {target_id}?\n"
        f"/ban_user {target_id} часы причина\n"
        f"(0 часов = навсегда)")


@admin_router.callback_query(F.data.startswith("admin:ugrant:"))
async def cb_admin_grant_user(callback: CallbackQuery):
    target_id=int(callback.data.split(":")[-1])
    await callback.answer()
    await callback.message.answer(
        f"<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Выдать Premium {target_id}?\n"
        f"/grant {target_id} дни причина")


@admin_router.message(Command("user","ban_user","unban","grant"))
async def cmd_user_action(message: Message):
    parts=message.text.split(maxsplit=3)
    if len(parts)<2:
        await message.answer("Укажи anon_id или telegram_id.")
        return
    user=await AdminService().find_user(message.from_user.id,parts[1])
    if not user:
        await message.answer("Пользователь не найден.")
        return
    command=parts[0].split('@')[0]
    if command=="/user":
        await message.answer(await user_card_text(message.from_user.id, user),
            reply_markup=admin_user_card_kb(user.telegram_id))
        return
    if command=="/unban":
        result=await AdminService().unban(message.from_user.id,user.telegram_id)
    elif len(parts)==4 and parts[2].isdigit():
        if command=="/grant":
            result=await AdminService().grant(message.from_user.id,user.telegram_id,int(parts[2]),parts[3],request_key=f"grant:{message.chat.id}:{message.message_id}")
        else:
            result=await AdminService().ban(message.from_user.id,user.telegram_id,parts[3],int(parts[2]) or None)
    else:
        await message.answer("Укажи ID, длительность числом и причину.")
        return
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сохранено" if result.ok else "Проверь параметры.")


async def outside_chat(message: Message):
    return await get_partner(message.from_user.id) is None


@admin_router.message(
    StateFilter(None),
    F.from_user.id.func(lambda user_id: user_id in config.ADMIN_IDS),
    F.text.func(lambda text: bool(text) and (text.strip().isdigit() or text.strip().startswith("#"))),
    outside_chat,
)
async def admin_text_search(message: Message):
    text=message.text.strip()
    if text.isdigit() or text.startswith('#'):
        user=await AdminService().find_user(message.from_user.id,text.lstrip('#'))
        if user:
            await message.answer(await user_card_text(message.from_user.id, user),
                reply_markup=admin_user_card_kb(user.telegram_id))


@admin_router.callback_query(F.data=="admin:settings")
async def cb_settings(callback: CallbackQuery):
    products=await PaymentService().products()
    await callback.answer()
    await callback.message.answer("<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Цены:\n"+"\n".join(f"{p.code}: {p.price_stars}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>" for p in products)+
        "\n\n/price код цена\n/setting chat_delay_next 7\n/setting search_timeout 60\n/setting inactive_chat_ttl 300\n/setting marketing_enabled 0"
        "\n/setting stars_usd_cents_per_100 130 (курс: центов США за 100 звёзд; 0 — не показывать $)"
        "\n/setting funnel_min_step_pct 50 (предупреждать в воронке о просадке ниже этого % между шагами; 0 — выключено)")


@admin_router.message(Command("price","setting"))
async def cmd_setting(message: Message):
    p=message.text.split()
    if len(p)!=3 or not p[2].isdigit():
        await message.answer("Нужны название параметра и числовое значение.")
        return
    service=AdminService()
    result=await (service.price(message.from_user.id,p[1],int(p[2])) if p[0].split('@')[0]=="/price" else service.setting(message.from_user.id,p[1],int(p[2])))
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сохранено" if result.ok else "Недопустимое значение.")


SEGMENT_TITLES = {"all": "все пользователи", "premium": "Premium",
                  "inactive": "неактивные 7+ дней", "nopay": "без покупок"}
STATUS_TITLES = {"draft": "черновик", "queued": "готовим список",
                 "sending": "отправка", "completed": "готово"}
FILLED, EMPTY, THIN_SPACE = "█", "░", " "


def amount(value):
    return f"{value:,}".replace(",", THIN_SPACE)


def progress_bar(done, total, width=16):
    if total <= 0:
        return EMPTY*width
    filled = max(0, min(width, round(width*done/total)))
    return FILLED*filled + EMPTY*(width-filled)


def broadcast_card(job, counts):
    """One renderer for the live progress message and the status screen."""
    sent, failed = counts.get("sent", 0), counts.get("failed", 0)
    skipped, expanded = counts.get("skipped", 0), sum(counts.values())
    processed = sent + failed + skipped
    # Until expansion finishes, the audience snapshot is the honest denominator.
    scale = max(job.total, expanded, 1)
    lines = ["<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> "
             f"Рассылка #{job.id} · {SEGMENT_TITLES.get(job.segment, job.segment)}"]
    if job.status == "draft":
        lines.append("\nЧерновик, ещё не запущена.")
    else:
        lines.append(f"\n<code>[{progress_bar(processed, scale)}]</code> {round(100*processed/scale)}%\n")
        lines.append(f"Отправлено: {amount(sent)}")
        blocked = f" (заблокировали бота: {amount(job.blocked)})" if job.blocked else ""
        lines.append(f"Не доставлено: {amount(failed)}{blocked}")
        if skipped:
            lines.append(f"Пропущено: {amount(skipped)}")
        lines.append(f"Обработано: {amount(processed)} из {amount(max(job.total, expanded))}")
    lines.append(f"\nСтатус: {STATUS_TITLES.get(job.status, job.status)}")
    if job.started_at:
        lines.append(f"Запущена: {job.started_at:%d.%m.%Y %H:%M} UTC")
    if job.finished_at:
        lines.append(f"Завершена: {job.finished_at:%d.%m.%Y %H:%M} UTC")
    return "\n".join(lines)


def broadcast_card_kb(job_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Обновить", icon_custom_emoji_id="5260450573768990626",
                             callback_data=f"admin:broadcasts:{job_id}"),
        InlineKeyboardButton(text="К списку", callback_data="admin:broadcasts")]])


@admin_router.callback_query(F.data=="admin:broadcast")
async def cb_broadcast(callback: CallbackQuery):
    service = AdminService()
    sizes = {segment: await service.audience_size(callback.from_user.id, segment)
             for segment in ("all", "premium", "inactive", "nopay")}
    await callback.answer()
    await callback.message.answer("<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> Выбери аудиторию:",
                                  reply_markup=admin_broadcast_audience_kb(sizes))


@admin_router.callback_query(F.data.startswith("bc:"))
async def cb_audience(callback: CallbackQuery,state: FSMContext):
    audience=callback.data.split(":")[-1]
    if audience not in {"all","premium","inactive","nopay"}:
        await callback.answer("Неизвестная аудитория")
        return
    await state.set_state(AdminBroadcast.CONTENT)
    await state.update_data(audience=audience)
    await callback.answer()
    await callback.message.answer("Введи текст. Перед отправкой будет предпросмотр и подтверждение.")


@admin_router.message(AdminBroadcast.CONTENT, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def broadcast_text(message: Message,state: FSMContext):
    audience=(await state.get_data()).get("audience","all")
    result=await AdminService().broadcast(message.from_user.id,audience,message.text)
    if not result.ok:
        await message.answer("Текст должен содержать от 1 до 4000 символов.")
        return
    await state.clear()
    job=result.data['job']
    await message.answer(f"<tg-emoji emoji-id=\"5253959125838090076\">👁</tg-emoji> Предпросмотр · {audience}\n\n{message.text}",parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Запустить рассылку",icon_custom_emoji_id="5188481279963715781",callback_data=f"admin:send:{job.id}",style="danger")]]))


@admin_router.callback_query(F.data.startswith("admin:send:"))
async def cb_send(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимая рассылка")
        return
    service=AdminService()
    result=await service.confirm_broadcast(callback.from_user.id,int(value))
    await callback.answer("Добавлено в очередь" if result.ok else "Рассылка уже запущена")
    if not result.ok:
        return
    job=result.data["job"]
    # The worker keeps editing this message until the broadcast finishes.
    progress=await callback.message.answer(broadcast_card(job,{}),reply_markup=broadcast_card_kb(job.id))
    await service.attach_progress(callback.from_user.id,job.id,progress.chat.id,progress.message_id)


@admin_router.callback_query(F.data=="admin:broadcasts")
@admin_router.callback_query(F.data.startswith("admin:broadcasts:"))
async def cb_broadcast_status(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    job_id=int(value) if value.isdigit() else None
    if job_id:
        found=await AdminService().broadcast_progress(callback.from_user.id,job_id)
        if not found:
            await callback.answer("Рассылка не найдена",show_alert=True)
            return
        job,counts=found
        text,markup=broadcast_card(job,counts),broadcast_card_kb(job.id)
        try:
            await callback.message.edit_text(text,reply_markup=markup)
            await callback.answer("Обновлено")
        except TelegramBadRequest as error:
            # Refreshing an unchanged card is normal; anything else gets a new message.
            if "message is not modified" in error.message:
                await callback.answer("Без изменений")
            else:
                await callback.answer()
                await callback.message.answer(text,reply_markup=markup)
    else:
        jobs=await AdminService().broadcast_status(callback.from_user.id)
        await callback.answer()
        if not jobs:
            await callback.message.answer("<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> Рассылок пока нет.",reply_markup=admin_kb())
            return
        await callback.message.answer("<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> Последние рассылки:",reply_markup=admin_broadcast_status_kb(jobs))


def ad_campaign_card_text(campaign):
    from handlers.chat import ad_card_text
    preview = ad_card_text({"title": campaign.title, "subtitle": campaign.subtitle, "body": campaign.body,
                            "channel_username": campaign.channel_username})
    def limit(v):
        return "без лимита" if not v else str(v)
    joins = (f"Вступлений по рекламной ссылке: {campaign.joins_total}" if campaign.invite_link else
             "Вступления не отслеживаются — добавь бота админом канала и перезапусти кампанию.")
    stats = (f"\n\n<b>Кампания #{campaign.id}</b>\n"
             f"Канал: @{campaign.channel_username}\n"
             f"Показов: {campaign.impressions_total} · дневной лимит: {limit(campaign.daily_limit)} · "
             f"общий лимит: {limit(campaign.total_limit)}\n"
             f"{joins}\n"
             f"Клики не измеряются — Telegram не уведомляет бота о нажатии ссылки.")
    return preview + stats


@admin_router.callback_query(F.data == "admin:ads")
async def cb_ads(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    campaigns = await AdCampaignService().list(callback.from_user.id)
    await callback.answer()
    text = "<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> Рекламные кампании" if campaigns else \
        "<tg-emoji emoji-id=\"5215668805199473901\">📢</tg-emoji> Кампаний пока нет."
    await callback.message.answer(text, reply_markup=admin_ads_kb(campaigns))


async def ads_settings_text(cooldown):
    minutes = cooldown // 60
    return (f"<tg-emoji emoji-id=\"6129805886383723340\">⚙️</tg-emoji> Настройки рекламы\n\n"
            f"Частота показа: не чаще раза в {minutes} мин ({cooldown} сек) одному пользователю.\n"
            f"Действует сразу на все кампании.")


@admin_router.callback_query(F.data == "admin:ads:settings")
async def cb_ads_settings(callback: CallbackQuery, state: FSMContext):
    from services.settings import bot_value
    await state.clear()
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.answer()
    await callback.message.answer(await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown))


@admin_router.callback_query(F.data.startswith("admin:ads:settings:set:"))
async def cb_ads_settings_set(callback: CallbackQuery):
    from services.settings import bot_value
    value = callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимое значение")
        return
    result = await AdminService().setting(callback.from_user.id, "ad_cooldown_seconds", int(value))
    await callback.answer("Сохранено" if result.ok else "Недопустимое значение")
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.message.edit_text(await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown))


@admin_router.callback_query(F.data == "admin:ads:settings:custom")
async def cb_ads_settings_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAdSettings.COOLDOWN)
    await callback.answer()
    await callback.message.answer(
        "Введи новую частоту показа в секундах (0–86400, например 900):",
        reply_markup=admin_ads_settings_cancel_kb())


@admin_router.callback_query(F.data == "admin:ads:settings:cancel")
async def cb_ads_settings_cancel(callback: CallbackQuery, state: FSMContext):
    from services.settings import bot_value
    await state.clear()
    await callback.answer("Отменено")
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await callback.message.answer(await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown))


@admin_router.message(AdminAdSettings.COOLDOWN, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ads_settings_cooldown(message: Message, state: FSMContext):
    from services.settings import bot_value
    if not message.text.isdigit():
        await message.answer("Нужно неотрицательное число секунд.", reply_markup=admin_ads_settings_cancel_kb())
        return
    result = await AdminService().setting(message.from_user.id, "ad_cooldown_seconds", int(message.text))
    if not result.ok:
        await message.answer("Значение вне диапазона 0–86400.", reply_markup=admin_ads_settings_cancel_kb())
        return
    await state.clear()
    cooldown = await bot_value("ad_cooldown_seconds", 1800)
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Сохранено.")
    await message.answer(await ads_settings_text(cooldown), reply_markup=admin_ads_settings_kb(cooldown))


@admin_router.callback_query(F.data == "admin:ad:new")
async def cb_ad_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminAdCampaign.CHANNEL)
    await callback.answer()
    await callback.message.answer(
        "Username канала или группы-рекламодателя, с @ (например @anon_chat_news):\n/cancel — отменить.")


@admin_router.message(AdminAdCampaign.CHANNEL, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ad_channel(message: Message, state: FSMContext):
    raw = message.text.strip()
    if not raw.startswith("@"):
        await message.answer("Нужно с @ в начале, например @anon_chat_news. Попробуй ещё раз.")
        return
    value = raw.lstrip("@")
    if not CHANNEL_USERNAME_RE.fullmatch(value):
        await message.answer("Username: 5–32 символа после @, латиница/цифры/подчёркивание. Попробуй ещё раз.")
        return
    await state.update_data(channel_username=value)
    await state.set_state(AdminAdCampaign.TITLE)
    await message.answer("Название с эмодзи, до 64 символов (например «🌍 Мир вокруг»):")


@admin_router.message(AdminAdCampaign.TITLE, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ad_title(message: Message, state: FSMContext):
    if not 1 <= len(message.text) <= 64:
        await message.answer("Название должно быть от 1 до 64 символов.")
        return
    await state.update_data(title=message.text)
    await state.set_state(AdminAdCampaign.SUBTITLE)
    await message.answer("Подзаголовок, до 64 символов (например «Путешествия · 89K подписчиков»). Пришли «-», чтобы пропустить:")


@admin_router.message(AdminAdCampaign.SUBTITLE, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ad_subtitle(message: Message, state: FSMContext):
    if len(message.text) > 64:
        await message.answer("Подзаголовок должен быть не длиннее 64 символов.")
        return
    await state.update_data(subtitle="" if message.text.strip() == "-" else message.text)
    await state.set_state(AdminAdCampaign.BODY)
    await message.answer("Текст объявления, до 400 символов:")


@admin_router.message(AdminAdCampaign.BODY, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ad_body(message: Message, state: FSMContext):
    if not 1 <= len(message.text) <= 400:
        await message.answer("Текст объявления должен быть от 1 до 400 символов.")
        return
    await state.update_data(body=message.text)
    await state.set_state(AdminAdCampaign.LIMITS)
    await message.answer(
        "Лимиты показов: два числа через пробел — дневной и общий (0 = без лимита). Например «0 5000»:")


@admin_router.message(AdminAdCampaign.LIMITS, F.text, ~F.text.in_(T.SYSTEM_TEXTS), ~F.text.startswith("/"))
async def ad_limits(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("Нужны два неотрицательных числа через пробел, например «0 5000».")
        return
    data = await state.get_data()
    result = await AdCampaignService().create(message.from_user.id, data["channel_username"], data["title"],
        data["subtitle"], data["body"], int(parts[0]), int(parts[1]))
    await state.clear()
    if not result.ok:
        await message.answer(f"Не удалось создать кампанию: {result.code}")
        return
    campaign = await AdCampaignService().get(message.from_user.id, result.data["campaign_id"])
    await message.answer("<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Черновик сохранён. Так увидят объявление подписчики:")
    await message.answer(ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign))


def _ad_id(callback):
    parts = callback.data.split(":")
    return int(parts[-1]) if parts[-1].isdigit() else None


@admin_router.callback_query(F.data.startswith("admin:ad:launch:"))
async def cb_ad_launch(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if not campaign:
        await callback.answer("Кампания не найдена")
        return
    invite_link, note = None, ""
    try:
        # A tracked invite link is what lets a later join be attributed to
        # this campaign; it only succeeds if the bot is an admin there.
        link = await callback.bot.create_chat_invite_link(
            chat_id=f"@{campaign.channel_username}", name=f"ad-{campaign.id}")
        invite_link = link.invite_link
    except (TelegramBadRequest, TelegramForbiddenError):
        note = ("\n\n⚠️ Ссылка для учёта вступлений не создана: бот не админ канала "
                f"@{campaign.channel_username}. Кампания запущена с обычной ссылкой; "
                "добавь бота админом и перезапусти, если нужна статистика вступлений.")
    result = await AdCampaignService().set_status(callback.from_user.id, campaign_id, "active", invite_link=invite_link)
    await callback.answer("Запущено" if result.ok else "Действие недоступно")
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if campaign:
        await callback.message.answer(ad_campaign_card_text(campaign)+note, reply_markup=admin_ad_card_kb(campaign))


@admin_router.callback_query(F.data.startswith("admin:ad:pause:"))
@admin_router.callback_query(F.data.startswith("admin:ad:resume:"))
@admin_router.callback_query(F.data.startswith("admin:ad:complete:"))
async def cb_ad_status(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    action = callback.data.split(":")[2]
    status = {"pause": "paused", "resume": "active", "complete": "completed"}[action]
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    result = await AdCampaignService().set_status(callback.from_user.id, campaign_id, status)
    await callback.answer("Сохранено" if result.ok else "Действие недоступно")
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id)
    if campaign:
        await callback.message.answer(ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign))


@admin_router.callback_query(F.data.startswith("admin:ad:delete:"))
async def cb_ad_delete(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    if campaign_id is None:
        await callback.answer("Недопустимая кампания")
        return
    result = await AdCampaignService().delete_draft(callback.from_user.id, campaign_id)
    await callback.answer("Удалено" if result.ok else "Действие недоступно")
    if result.ok:
        campaigns = await AdCampaignService().list(callback.from_user.id)
        await callback.message.answer("Черновик удалён.", reply_markup=admin_ads_kb(campaigns))


@admin_router.callback_query(F.data.regexp(r"^admin:ad:\d+$"))
async def cb_ad_view(callback: CallbackQuery):
    campaign_id = _ad_id(callback)
    campaign = await AdCampaignService().get(callback.from_user.id, campaign_id) if campaign_id is not None else None
    if not campaign:
        await callback.answer("Кампания не найдена", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(ad_campaign_card_text(campaign), reply_markup=admin_ad_card_kb(campaign))

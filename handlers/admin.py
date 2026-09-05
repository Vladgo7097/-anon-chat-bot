from html import escape
from aiogram import F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from handlers import admin_router
from keyboards.inline import admin_kb, admin_report_kb, admin_broadcast_audience_kb, admin_user_card_kb, admin_broadcast_status_kb
from services.admin import AdminService, StatsService, authorize
from services.payments import PaymentService
from services.chat_manager import get_online_count


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


@admin_router.message(Command("season"))
async def cmd_season(message: Message):
    parts=message.text.split()
    if len(parts)!=5 or not parts[4].isdigit():
        await message.answer("/season код начало_ISO конец_ISO скидка\nДаты с часовым поясом, например 2026-10-01T00:00:00+00:00. Включение: /setting seasonal_enabled 1")
        return
    result=await AdminService().seasonal(message.from_user.id,parts[1],parts[2],parts[3],int(parts[4]))
    await message.answer("<emoji document_id=\"5280880410346152584\">✅</emoji> Событие сохранено. Оно действует только в заданный период и при seasonal_enabled=1." if result.ok else "Проверь код, даты с часовым поясом и скидку 1–90%.")


def overview_text(s):
    metric=lambda key: f"{s[key]}%" if s.get(key) is not None else "— (недостаточно данных)"
    return f"<emoji document_id=\"6129805886383723340\">🛠</emoji> Админ-панель\n\n<emoji document_id=\"5215522595922779944\">👥</emoji> Пользователей: {s['users']}\n<emoji document_id=\"5348232622898167572\">👤</emoji> Новых за период: {s['new']}\n<emoji document_id=\"5224455462178549802\">💬</emoji> Завершённых диалогов: {s['chats']}\n<emoji document_id=\"5258258882022612173\">⏱</emoji> Средняя длительность: {s['avg_seconds']} сек.\n<emoji document_id=\"5312160339335347417\">💰</emoji> Оплачено: {s['stars']}<emoji document_id=\"5920281855378068765\">⭐</emoji>\n<emoji document_id=\"5283130719806175947\">💎</emoji> Активных Premium: {s['premium']}\n<emoji document_id=\"5350417283783084711\">👍</emoji> Средний рейтинг: {metric('rating')}\nВозврат на следующий день: {metric('retention_d1')}\nВозврат через 7 дней: {metric('retention_d7')}"


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message,state: FSMContext):
    await state.clear()
    s=await StatsService().overview()
    await message.answer(overview_text(s)+f"\n<emoji document_id=\"5215522595922779944\">🟢</emoji> Онлайн: {await get_online_count()}",reply_markup=admin_kb())


@admin_router.callback_query(F.data=="admin:back")
async def cb_admin_back(callback: CallbackQuery):
    await callback.answer()
    s=await StatsService().overview()
    await callback.message.answer(overview_text(s)+f"\n<emoji document_id=\"5215522595922779944\">🟢</emoji> Онлайн: {await get_online_count()}",reply_markup=admin_kb())


@admin_router.callback_query(F.data.in_({"admin:stats","admin:money","admin:funnel"}))
@admin_router.callback_query(F.data.startswith("admin:period:"))
async def cb_stats(callback: CallbackQuery):
    days=int(callback.data.split(":")[-1]) if callback.data.startswith("admin:period:") else 1
    if days not in {1,7,30}:
        await callback.answer("Недопустимый период.")
        return
    s=await StatsService().overview(days)
    text=overview_text(s)
    if callback.data=="admin:money":
        text+="\n\nПо продуктам:\n"+"\n".join(f"{k}: {v}<emoji document_id=\"5920281855378068765\">⭐</emoji>" for k,v in s['products'].items())
    if callback.data=="admin:funnel":
        text+="\n\nУникальные пользователи за период:\n"+"\n".join(f"{k}: {v}" for k,v in s['funnel'].items())
    await callback.answer()
    await callback.message.answer(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="За неделю",callback_data="admin:period:7"),
        InlineKeyboardButton(text="За месяц",callback_data="admin:period:30"),
        InlineKeyboardButton(text="CSV",callback_data=f"admin:export:{days}")]]))


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
        await callback.message.answer("<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалоб нет.",reply_markup=admin_kb())
        return
    r=reports[0]
    names,previous,bans=await AdminService().report_details(callback.from_user.id,r)
    await callback.message.answer(f"<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалоба #{r.id}\nОт: {escape(names.get(r.reporter_id,'Удалённый профиль'))}\nНа: {escape(names.get(r.reported_id,'Удалённый профиль'))}\nПричина: {escape(r.reason)}\nДетали: {escape(r.message_content or 'Не указаны')}\nИстория: {previous} других жалоб, {bans} банов\nДата: {r.created_at:%d.%m.%Y %H:%M} UTC",reply_markup=admin_report_kb(r.id,offset+1))


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
    await callback.message.answer("<emoji document_id=\"5312229088876847138\">🚫</emoji> Активные баны (до 50):\n"+text+"\n\n/ban_user ID часы причина\n/unban ID")


@admin_router.callback_query(F.data=="admin:users")
async def cb_users(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("<emoji document_id=\"5215522595922779944\">👥</emoji> Введи anon_id или telegram_id пользователя:")


@admin_router.callback_query(F.data.startswith("admin:user_card:"))
async def cb_user_card(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    user=await AdminService().find_user(callback.from_user.id,value)
    if not user:
        await callback.answer("Пользователь не найден",show_alert=True)
        return
    from html import escape as esc
    premium=f"до {user.premium_expires:%d.%m.%Y}" if user.premium_expires else "нет"
    await callback.answer()
    await callback.message.answer(
        f"<emoji document_id=\"5348232622898167572\">👤</emoji> {esc(user.anon_id or '?')}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"<emoji document_id=\"5258105663359294787\">📅</emoji> Регистрация: {user.created_at:%d.%m.%Y}\n"
        f"<emoji document_id=\"5224455462178549802\">💬</emoji> Диалогов: {user.chats_count}\n"
        f"<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалоб на него: —\n"
        f"<emoji document_id=\"5920281855378068765\">⭐</emoji> Premium: {premium}\n"
        f"<emoji document_id=\"5312160339335347417\">💰</emoji> Всего потратил: {user.total_stars_spent}<emoji document_id=\"5920281855378068765\">⭐</emoji>",
        reply_markup=admin_user_card_kb(user.telegram_id))


@admin_router.callback_query(F.data.startswith("admin:message:"))
async def cb_message_user(callback: CallbackQuery, state: FSMContext):
    target_id=int(callback.data.split(":")[-1])
    await state.set_state(AdminMessage.CONTENT)
    await state.update_data(target_id=target_id)
    await callback.answer()
    await callback.message.answer(f"<emoji document_id=\"5224455462178549802\">💬</emoji> Введи текст сообщения для пользователя {target_id}:")


@admin_router.message(AdminMessage.CONTENT, F.text)
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
        await message.answer(f"<emoji document_id=\"5280880410346152584\">✅</emoji> Сообщение отправлено пользователю {target_id}.")
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
        f"<emoji document_id=\"5312160339335347417\">🎁</emoji> Выдать Premium {target_id}?\n"
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
        from html import escape as esc
        premium=f"до {user.premium_expires:%d.%m.%Y}" if user.premium_expires else "нет"
        await message.answer(
            f"<emoji document_id=\"5348232622898167572\">👤</emoji> {esc(user.anon_id or '?')}\n"
            f"Telegram ID: {user.telegram_id}\n"
            f"<emoji document_id=\"5258105663359294787\">📅</emoji> Регистрация: {user.created_at:%d.%m.%Y}\n"
            f"<emoji document_id=\"5224455462178549802\">💬</emoji> Диалогов: {user.chats_count}\n"
            f"<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалоб на него: —\n"
            f"<emoji document_id=\"5920281855378068765\">⭐</emoji> Premium: {premium}\n"
            f"<emoji document_id=\"5312160339335347417\">💰</emoji> Всего потратил: {user.total_stars_spent}<emoji document_id=\"5920281855378068765\">⭐</emoji>",
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
    await message.answer("<emoji document_id=\"5280880410346152584\">✅</emoji> Сохранено" if result.ok else "Проверь параметры.")


@admin_router.message(F.text.func(lambda t: t and not t.startswith('/') and t not in {"<emoji document_id=\"6129805886383723340\">🛠</emoji> Админ-панель","<emoji document_id=\"5931472654660800739\">📊</emoji> Статистика","<emoji document_id=\"5312160339335347417\">💰</emoji> Монетизация","<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалобы","<emoji document_id=\"5312229088876847138\">🚫</emoji> Баны","<emoji document_id=\"5215522595922779944\">👥</emoji> Пользователи","<emoji document_id=\"5215668805199473901\">📢</emoji> Рассылка","<emoji document_id=\"6129805886383723340\">⚙️</emoji> Настройки бота","<emoji document_id=\"5283130719806175947\">📈</emoji> Воронка","<emoji document_id=\"5460865451586250451\">📋</emoji> Рассылки (статус)"}))
async def admin_text_search(message: Message):
    text=message.text.strip()
    if text.isdigit() or text.startswith('#'):
        user=await AdminService().find_user(message.from_user.id,text.lstrip('#'))
        if user:
            from html import escape as esc
            premium=f"до {user.premium_expires:%d.%m.%Y}" if user.premium_expires else "нет"
            await message.answer(
                f"<emoji document_id=\"5348232622898167572\">👤</emoji> {esc(user.anon_id or '?')}\n"
                f"Telegram ID: {user.telegram_id}\n"
                f"<emoji document_id=\"5258105663359294787\">📅</emoji> Регистрация: {user.created_at:%d.%m.%Y}\n"
                f"<emoji document_id=\"5224455462178549802\">💬</emoji> Диалогов: {user.chats_count}\n"
                f"<emoji document_id=\"5280880410346152584\">🚩</emoji> Жалоб на него: —\n"
                f"<emoji document_id=\"5920281855378068765\">⭐</emoji> Premium: {premium}\n"
                f"<emoji document_id=\"5312160339335347417\">💰</emoji> Всего потратил: {user.total_stars_spent}<emoji document_id=\"5920281855378068765\">⭐</emoji>",
                reply_markup=admin_user_card_kb(user.telegram_id))


@admin_router.callback_query(F.data=="admin:settings")
async def cb_settings(callback: CallbackQuery):
    products=await PaymentService().products()
    await callback.answer()
    await callback.message.answer("<emoji document_id=\"6129805886383723340\">⚙️</emoji> Цены:\n"+"\n".join(f"{p.code}: {p.price_stars}<emoji document_id=\"5920281855378068765\">⭐</emoji>" for p in products)+
        "\n\n/price код цена\n/setting chat_delay_next 7\n/setting search_timeout 60\n/setting inactive_chat_ttl 300\n/setting marketing_enabled 0")


@admin_router.message(Command("price","setting"))
async def cmd_setting(message: Message):
    p=message.text.split()
    if len(p)!=3 or not p[2].isdigit():
        await message.answer("Нужны название параметра и числовое значение.")
        return
    service=AdminService()
    result=await (service.price(message.from_user.id,p[1],int(p[2])) if p[0].split('@')[0]=="/price" else service.setting(message.from_user.id,p[1],int(p[2])))
    await message.answer("<emoji document_id=\"5280880410346152584\">✅</emoji> Сохранено" if result.ok else "Недопустимое значение.")


@admin_router.callback_query(F.data=="admin:broadcast")
async def cb_broadcast(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("<emoji document_id=\"5215668805199473901\">📢</emoji> Выбери аудиторию:",reply_markup=admin_broadcast_audience_kb())


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


@admin_router.message(AdminBroadcast.CONTENT,F.text)
async def broadcast_text(message: Message,state: FSMContext):
    audience=(await state.get_data()).get("audience","all")
    result=await AdminService().broadcast(message.from_user.id,audience,message.text)
    if not result.ok:
        await message.answer("Текст должен содержать от 1 до 4000 символов.")
        return
    await state.clear()
    job=result.data['job']
    await message.answer(f"<emoji document_id=\"5253959125838090076\">👁</emoji> Предпросмотр · {audience}\n\n{message.text}",parse_mode=None,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="<emoji document_id=\"5188481279963715781\">🚀</emoji> Запустить рассылку",callback_data=f"admin:send:{job.id}",style="danger")]]))


@admin_router.callback_query(F.data.startswith("admin:send:"))
async def cb_send(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    if not value.isdigit():
        await callback.answer("Недопустимая рассылка")
        return
    result=await AdminService().confirm_broadcast(callback.from_user.id,int(value))
    await callback.answer("Добавлено в очередь" if result.ok else "Рассылка уже запущена")


@admin_router.callback_query(F.data=="admin:broadcasts")
@admin_router.callback_query(F.data.startswith("admin:broadcasts:"))
async def cb_broadcast_status(callback: CallbackQuery):
    value=callback.data.split(":")[-1]
    job_id=int(value) if value.isdigit() else None
    if job_id:
        job=await AdminService().broadcast_status(callback.from_user.id,job_id)
        if not job:
            await callback.answer("Рассылка не найдена",show_alert=True)
            return
        await callback.answer()
        await callback.message.answer(
            f"<emoji document_id=\"5215668805199473901\">📢</emoji> Рассылка #{job.id}\n"
            f"Аудитория: {job.segment}\n"
            f"Статус: {job.status}\n"
            f"Отправлено: {job.sent}\n"
            f"Ошибок: {job.failed}\n"
            f"Заблокировано: {job.blocked}\n"
            f"Создана: {job.created_at:%d.%m.%Y %H:%M UTC}\n"
            f"Завершена: {job.finished_at or '—'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:broadcasts")]]))
    else:
        jobs=await AdminService().broadcast_status(callback.from_user.id)
        await callback.answer()
        if not jobs:
            await callback.message.answer("<emoji document_id=\"5215668805199473901\">📢</emoji> Рассылок пока нет.",reply_markup=admin_kb())
            return
        await callback.message.answer("<emoji document_id=\"5215668805199473901\">📢</emoji> Последние рассылки:",reply_markup=admin_broadcast_status_kb(jobs))

import asyncio
import logging
import time
import re
from html import escape
from pathlib import Path
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from services.notifications import NotificationService
from services.chats import ChatService
from services import funnel_alert
from services import op as op_service
from utils import bot_health
from keyboards.reply import main_menu_kb, chat_kb_reply
from utils import bots

logger = logging.getLogger(__name__)


def notification_content(notification):
    if notification.type not in {"match", "rating", "unban", "inactive", "reward", "startup", "reveal_mutual"}:
        return notification.text, None
    if notification.type in {"startup", "reveal_mutual"}:
        return notification.text, "HTML"
    # Escape dynamic text, then restore only the supported custom emoji markup.
    text = escape(notification.text)
    text = re.sub(
        r'&lt;tg-emoji emoji-id=&quot;(\d+)&quot;&gt;(.*?)&lt;/tg-emoji&gt;',
        r'<tg-emoji emoji-id="\1">\2</tg-emoji>', text,
    )
    return text, "HTML"


async def refresh_broadcast_progress(bot, service):
    """Keep the admin progress message in step with the outbox."""
    from handlers.admin import broadcast_card, broadcast_card_kb
    for update in await service.progress_updates():
        job = update["job"]
        # The card sits in the chat that started the broadcast, which may
        # belong to the other bot -- editing it through the wrong token is a 403.
        target = await bots.bot_for(update["chat_id"], bot)
        try:
            await target.edit_message_text(broadcast_card(job, update["counts"], update.get("poll_totals")),
                                           chat_id=update["chat_id"], message_id=update["message_id"],
                                           reply_markup=broadcast_card_kb(job.id, job.status))
        except TelegramRetryAfter as error:
            await service.defer_progress(job.id, error.retry_after)
            continue
        except TelegramBadRequest as error:
            if "message is not modified" in error.message:
                if update["final"]:
                    await service.drop_progress(job.id)
                continue
            logger.warning("event=broadcast_progress_unavailable error_code=%s", type(error).__name__)
            await service.drop_progress(job.id)
        except TelegramForbiddenError:
            await service.drop_progress(job.id)
        else:
            if update["final"]:
                await service.drop_progress(job.id)


async def run(bot):
    service=NotificationService()
    last=0
    last_funnel=time.monotonic()
    last_beat=0.0
    while True:
        try:
            now=time.time()
            Path('/tmp/anon-worker-heartbeat').write_text(str(now))
            # Файл виден только внутри этого контейнера, а админка живёт в
            # соседнем: без зеркала в Redis она приняла бы работающий воркер
            # за мёртвый. Раз в 15 секунд -- чаще не нужно, EX и так жрёт.
            if now-last_beat>15:
                last_beat=now
                try:
                    from services.chat_manager import get_redis
                    await (await get_redis()).set("worker:heartbeat",str(now),ex=600)
                except Exception:
                    logger.debug("event=heartbeat_mirror_failed", exc_info=True)
            if time.monotonic()-last>60:
                await ChatService().reconcile()
                await service.schedule()
                await op_service.tick()
                last=time.monotonic()
            # The funnel watcher pages the owner on arrivals-without-completions;
            # ten minutes is fast enough to catch a step breaking the same day.
            if time.monotonic()-last_funnel>600:
                await funnel_alert.check()
                last_funnel=time.monotonic()
            # Telegram never announces a dead token or one someone else is
            # polling -- the bot just goes quiet and keeps retrying forever.
            # probe() asks each token directly, report() turns repeated
            # failures into a page instead of a week of silence.
            await bot_health.probe()
            await bot_health.report()
            await service.expand_broadcast()
            await op_service.notify_tick()
            await refresh_broadcast_progress(bot, service)
            notification=await service.next()
            if notification:
                try:
                    # Nothing routes through the dispatcher here, so the
                    # recipient's own language is looked up per notification.
                    from utils.db import user_lang
                    from keyboards import texts as T
                    # Both bots share this table, so a notice has to leave through
                    # the bot this recipient actually talks to. `bot` is only the
                    # fallback for a row that does not name one yet.
                    target = await bots.bot_for(notification.user_id, bot)
                    # Рассылка ОП уходит тем ботом, чью админку настраивали:
                    # права на getChatMember проверены именно у него.
                    if notification.type == "op_notice" and notification.bot_id:
                        target = bots.resolve(notification.bot_id) or target
                    lang = await user_lang(notification.user_id, bots.default_lang(target))
                    markup=None
                    if notification.type=="match":
                        markup=chat_kb_reply(lang)
                    elif notification.type == "reveal_request":
                        from handlers.reveal import reveal_request_keyboard
                        markup = reveal_request_keyboard(notification.callback_data, lang)
                    elif notification.type == "rating_prompt":
                        from handlers.chat import rating_keyboard
                        markup = rating_keyboard(notification.callback_data, lang)
                    elif notification.type=="chat_ended":
                        markup=main_menu_kb(lang)
                    elif notification.type == "reconnect_prompt":
                        markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text=T.tr(T.WORKER_BUTTONS, lang)["similar"],
                                callback_data="search:repeat", style="primary")]])
                    elif notification.type == "op_notice":
                        markup = await op_service.op_notice_kb(notification.bot_id, lang)
                    elif notification.callback_data:
                        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=T.tr(T.WORKER_BUTTONS, lang)["open"],callback_data=notification.callback_data,style="primary")]])
                    poll = await service.broadcast_poll(notification.broadcast_id) if notification.type=="broadcast" and notification.broadcast_id else None
                    if poll:
                        options, question = poll
                        sent = await target.send_poll(notification.user_id, question, options, is_anonymous=True)
                        await service.record_poll(sent.poll.id, notification.broadcast_id, len(options))
                    else:
                        text, parse_mode = notification_content(notification)
                        await target.send_message(notification.user_id,text,reply_markup=markup,parse_mode=parse_mode)
                    await service.finish(notification.id,"sent")
                except TelegramForbiddenError:
                    await service.finish(notification.id,"failed","forbidden")
                    if notification.type in {"match", "reveal_request"}:
                        await ChatService().end_chat(notification.user_id,notification.callback_data,"unreachable")
                except TelegramRetryAfter as error:
                    await service.finish(notification.id,"queued","rate_limit",error.retry_after)
                except TelegramBadRequest:
                    await service.finish(notification.id,"failed","bad_request")
                except Exception:
                    await service.finish(notification.id,"queued","network",min(300,2**notification.attempt_count))
            # Broadcasts get a much slower pace than one-off notifications --
            # confirmed on prod: a bulk send at the normal 0.1s/~10 msg/s pace
            # tripped Telegram's flood protection and got the whole broadcast
            # a ~24h TelegramRetryAfter, stalling hundreds of recipients.
            if notification and notification.type == "broadcast":
                await asyncio.sleep(0.35)
            else:
                await asyncio.sleep(0.1 if notification else 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event=worker_error")
            await asyncio.sleep(5)

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
from keyboards.reply import main_menu_kb, chat_kb_reply

logger = logging.getLogger(__name__)


def notification_content(notification):
    if notification.type not in {"match", "rating", "unban", "inactive", "reward", "startup", "owner_new_user"}:
        return notification.text, None
    if notification.type in {"startup", "owner_new_user"}:
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
        try:
            await bot.edit_message_text(broadcast_card(job, update["counts"]),
                                        chat_id=update["chat_id"], message_id=update["message_id"],
                                        reply_markup=broadcast_card_kb(job.id))
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
    while True:
        try:
            Path('/tmp/anon-worker-heartbeat').write_text(str(time.time()))
            if time.monotonic()-last>60:
                await ChatService().reconcile()
                await service.schedule()
                last=time.monotonic()
            await service.expand_broadcast()
            await refresh_broadcast_progress(bot, service)
            notification=await service.next()
            if notification:
                try:
                    markup=None
                    if notification.type=="match":
                        markup=chat_kb_reply()
                    elif notification.type == "reveal_request":
                        from handlers.reveal import reveal_request_keyboard
                        markup = reveal_request_keyboard(notification.callback_data)
                    elif notification.type == "rating_prompt":
                        from handlers.chat import rating_keyboard
                        markup = rating_keyboard(notification.callback_data)
                    elif notification.type=="chat_ended":
                        markup=main_menu_kb()
                    elif notification.type == "reconnect_prompt":
                        markup=InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="Найти похожего собеседника",
                                callback_data="search:repeat", style="primary")]])
                    elif notification.callback_data:
                        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть",callback_data=notification.callback_data,style="primary")]])
                    text, parse_mode = notification_content(notification)
                    await bot.send_message(notification.user_id,text,reply_markup=markup,parse_mode=parse_mode)
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
            await asyncio.sleep(0.1 if notification else 1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event=worker_error")
            await asyncio.sleep(5)

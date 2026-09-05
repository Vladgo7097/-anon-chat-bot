import asyncio
import logging
import time
from pathlib import Path
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from services.notifications import NotificationService
from services.chats import ChatService
from keyboards.reply import main_menu_kb, chat_kb_reply

logger = logging.getLogger(__name__)


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
            notification=await service.next()
            if notification:
                try:
                    markup=None
                    if notification.type=="match":
                        markup=chat_kb_reply()
                    elif notification.type=="chat_ended":
                        markup=main_menu_kb()
                    elif notification.callback_data:
                        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть",callback_data=notification.callback_data,style="primary")]])
                    await bot.send_message(notification.user_id,notification.text,reply_markup=markup,parse_mode=None)
                    await service.finish(notification.id,"sent")
                except TelegramForbiddenError:
                    await service.finish(notification.id,"failed","forbidden")
                    if notification.type=="match":
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

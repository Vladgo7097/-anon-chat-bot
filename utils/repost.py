"""Перелить сообщение на бота, который знает чат получателя.

copy_message копирует внутри одного бота: из чата, который бот знает, в чат,
который он знает тоже. Как только пара собрана из пользователей разных ботов,
для бота-отправителя чат партнёра просто не существует -- Telegram отвечает
400, и сообщение не доходит. Ровно это и рвалось: 162 обрыва за шесть часов,
все на кросс-бот парах.

Здесь нет магии, только перелив. Текст уходит как есть, медиа скачиваются у
бота-отправителя и отправляются заново ботом-получателем: file_id живёт
только внутри своего бота, другого способа показать картинку второму боту нет.

Пара на одном боте идёт прежним copy_message -- без скачивания, без потери
оригинальных метаданных, без лишней нагрузки на сеть.
"""
import logging

from aiogram.types import (BufferedInputFile, InputMediaAnimation,
                           InputMediaPhoto, InputMediaVideo)

logger = logging.getLogger(__name__)

# kind -> (поле с файлом в Message, метод отправки, класс для альбома, есть ли caption)
_MEDIA = {
    "photo":      ("photo", "send_photo", InputMediaPhoto, True),
    "voice":      ("voice", "send_voice", None, True),
    "sticker":    ("sticker", "send_sticker", None, False),
    "video":      ("video", "send_video", InputMediaVideo, True),
    "video_note": ("video_note", "send_video_note", None, False),
    "animation":  ("animation", "send_animation", InputMediaAnimation, True),
}


async def copy(target_bot, chat_id, *, source, **kwargs):
    """copy_message внутри одного бота, перелитие -- между ними.

    source -- сам Message, он у зовущего уже есть. Bot API не умеет достать
    сообщение по идентификатору: раньше здесь стоял несуществующий
    get_messages, и каждое кросс-бот сообщение падало в AttributeError,
    не дойдя до получателя и не оставив ответа отправителю.
    """
    if target_bot is source.bot:
        return await target_bot.copy_message(chat_id=chat_id,
                                             from_chat_id=source.chat.id,
                                             message_id=source.message_id,
                                             **kwargs)
    return await repost(target_bot, chat_id, source, **kwargs)


async def copy_many(target_bot, chat_id, *, sources):
    """copy_messages внутри одного бота, send_media_group -- между ними."""
    if not sources:
        return []
    if target_bot is sources[0].bot:
        return await target_bot.copy_messages(
            chat_id=chat_id, from_chat_id=sources[0].chat.id,
            message_ids=[item.message_id for item in sources])
    return await repost_many(target_bot, chat_id, sources)


async def repost(target_bot, chat_id, message, **kwargs):
    """Отдать уже полученное сообщение чужим ботом. None, если не вышло."""
    kind = message.content_type
    if kind == "text":
        # parse_mode=None обязателен: отправляется чужой текст, а у бота по
        # умолчанию включён HTML, который поломается на незакрытой скобке.
        return await target_bot.send_message(chat_id, message.text,
                                             entities=message.entities,
                                             parse_mode=None, **kwargs)
    media = _MEDIA.get(kind)
    if media is None:
        logger.warning("event=repost_unsupported kind=%s chat_id=%s", kind, chat_id)
        return None
    payload = await _download(message, media)
    if payload is None:
        return None
    send = getattr(target_bot, media[1])
    return await send(chat_id, payload, parse_mode=None,
                      **_caption(message, media[3]), **kwargs)


async def repost_many(target_bot, chat_id, messages):
    """Перелить альбом одним send_media_group, сохранив его группировку."""
    if not messages:
        return []
    parts, kept = [], []
    for message in messages:
        media = _MEDIA.get(message.content_type)
        if media is None or media[2] is None:
            # Альбом без готового InputMedia класса переливается по одному:
            # группировку сохранить не выйдет, но содержимое дойдёт.
            sent = await repost(target_bot, chat_id, message)
            if sent is None:
                return None
            kept.append(sent)
            continue
        payload = await _download(message, media)
        if payload is None:
            return None
        parts.append(media[2](media=payload, caption=message.caption,
                              caption_entities=message.caption_entities))
    if parts:
        kept.extend(await target_bot.send_media_group(chat_id, media=parts))
    return kept


def _caption(message, has_caption):
    """У стикера и видеокружка нет caption -- передавать его нельзя вообще."""
    if not has_caption:
        return {}
    return {"caption": message.caption, "caption_entities": message.caption_entities}


async def _download(message, media):
    """Снять файл у бота, которому сообщение принадлежит."""
    holder = getattr(message, "bot", None)
    field = media[0]
    attachment = getattr(message, field, None)
    # photo приходит списком PhotoSize от мелкого к крупному: берём крупный.
    if isinstance(attachment, list):
        attachment = attachment[-1] if attachment else None
    if holder is None or attachment is None:
        logger.warning("event=repost_no_attachment field=%s", field)
        return None
    try:
        file = await holder.get_file(attachment.file_id)
        data = await holder.download(file)
    except Exception:
        logger.warning("event=repost_download_failed field=%s", field, exc_info=True)
        return None
    payload = data.read() if hasattr(data, "read") else bytes(data)
    return BufferedInputFile(payload, filename=f"{field}.bin")

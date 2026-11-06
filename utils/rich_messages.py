# language: Python, file: utils/rich_messages.py, target: Bot API 10.3 (aiogram 3.31+)
# Блоки Rich Messages: параграфы, сворачиваемые <details>, отправка sendRichMessage.
import re
from html import unescape

from aiogram.methods import SendRichMessage

_EMOJI_TAG = re.compile(r'<tg-emoji emoji-id="(\d+)">([^<]*)</tg-emoji>')


def card_blocks(text):
    """Текст в blocks-режиме: каждая строка — block "paragraph".

    RichText = строка ИЛИ массив сущностей: кастомные эмодзи уходят как
    {"type": "custom_emoji", "custom_emoji_id": ..., "alternative_text": ...}.
    HTML-парсинга в blocks-режиме нет, поэтому сущности открываю и строю
    массив вручную (теги tg-emoji в текстах локалей — источник эмодзи-сущностей).
    """
    blocks = []
    for line in unescape(text).split("\n"):
        parts, pos = [], 0
        for m in _EMOJI_TAG.finditer(line):
            if m.start() > pos:
                parts.append(line[pos : m.start()])
            parts.append(
                {
                    "type": "custom_emoji",
                    "custom_emoji_id": m.group(1),
                    "alternative_text": m.group(2),
                }
            )
            pos = m.end()
        if pos < len(line):
            parts.append(line[pos:])
        if not parts:
            continue
        blocks.append({"type": "paragraph", "text": parts})
    return blocks


def details_block(summary: str, text: str, is_open: bool = False):
    """RichBlockDetails (InputRichBlockDetails): сворачиваемый блок.

    summary — всегда видимая строка-заголовок, text — скрытое тело
    (полный текст внутри <details>); is_open=True — раскрыт изначально.
    """
    block = {
        "type": "details",
        "summary": summary,
        "blocks": card_blocks(text),
    }
    if is_open:
        block["is_open"] = True
    return block


def send_rich(bot, chat_id: int, blocks: list, reply_markup=None):
    """SendRichMessage с любым набором блоков; reply_markup сохраняет
    прежние inline-кнопки под сообщением (параметр метода 10.1+)."""
    from aiogram.types import InputRichMessage

    rich_message = {"blocks": blocks}
    if reply_markup is not None:
        return bot(
            SendRichMessage(
                chat_id=chat_id,
                rich_message=InputRichMessage(**rich_message),
                reply_markup=reply_markup,
            )
        )
    return bot(
        SendRichMessage(chat_id=chat_id, rich_message=InputRichMessage(**rich_message))
    )

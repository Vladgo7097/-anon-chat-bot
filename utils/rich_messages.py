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


# ── Единый стиль экранов: кнопки-блоки и простой HTML в параграфах ──

NBSP = "\u00a0"


def _pad_labels(buttons):
    """Ширины у RichMessageButton нет, клиент кроит кнопку по тексту —
    выравниваем метки NBSP до длины самой длинной (как в карточке профиля)."""
    texts = [b[0] if isinstance(b, (list, tuple)) else b["text"] for b in buttons]
    target = max(len(t) for t in texts) if texts else 0
    out = []
    for b in buttons:
        if isinstance(b, (list, tuple)):
            text, callback, style = (tuple(b) + (None,))[:3]
        else:
            text, callback, style = b["text"], b.get("callback"), b.get("style")
        short = target - len(text)
        if short > 0:
            half = short // 2
            text = NBSP * half + text + NBSP * (short - half)
        out.append((text, callback, style))
    return out


def buttons_block_row(buttons, align="left"):
    """Один блок "buttons": ряд InputRichBlockButtons (Бот API 10.x).
    button: (текст, callback_data) или (текст, callback_data, стиль);
    стиль — только из {"danger", "success", "primary", "link"}, иначе
    Telegram роняет запрос ("Invalid button style specified")."""
    padded = _pad_labels(buttons)
    return {
        "type": "buttons",
        "align": align,
        "buttons": [
            {"text": t, "callback_data": c, **({"style": s} if s else {})}
            for t, c, s in padded
        ],
    }


def rich_buttons(buttons, per_row=2, center_tail=0):
    """Кнопки в стиле карточки профиля: ряды по per_row,
    последние center_tail кнопок — отдельным центрированным блоком."""
    if not buttons:
        return []
    if center_tail:
        body, tail = buttons[:-center_tail], buttons[-center_tail:]
    else:
        body, tail = buttons, []
    blocks = []
    i = 0
    while i < len(body):
        blocks.append(buttons_block_row(body[i : i + per_row]))
        i += per_row
    if tail:
        blocks.append(buttons_block_row(tail, align="center"))
    return blocks


_BOLD_TAG = re.compile(r"<b>(.*?)</b>", re.S)


def html_parts(line):
    """RichText строки с простым HTML: <b> → entity "bold",
    tg-emoji → entity custom_emoji, остальное строкой.

    Мини-парсер: во вложенность <b> внутри эмодзи не лезет — шаблоны
    экранов жирнят только целые фразы без tg-emoji внутри."""
    parts, pos = [], 0
    for b in _BOLD_TAG.finditer(line):
        parts += plain_parts(line[pos : b.start()])
        parts.append({"type": "bold", "text": b.group(1)})
        pos = b.end()
    parts += plain_parts(line[pos:])
    return parts


def plain_parts(line):
    """Части строки без HTML: строка + custom_emoji-сущности."""
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
    return parts


def html_blocks(text):
    """Параграфы HTML-шаблона (жирность + кастомные эмодзи как сущности,
    теги сами срезаю — html-парсинга в blocks-режиме нет)."""
    blocks = []
    for line in unescape(text).split("\n"):
        parts = html_parts(line)
        if parts:
            blocks.append({"type": "paragraph", "text": parts})
    return blocks

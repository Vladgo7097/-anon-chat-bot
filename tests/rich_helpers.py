# helpers.py: восстановление текста rich-сообщений (SendRichMessage) для тестов.
# Параграфы и summary «деталей» собираются в текст; скрытые blocks <details>
# получаются отдельным вызовом — visible/hidden поведение проверяем github-jr.
from html import unescape


def _render(parts):
    if isinstance(parts, str):
        return parts
    out = ""
    for pt in parts:
        if hasattr(pt, "alternative_text") and not hasattr(
            pt, "custom_emoji_id_offset"
        ):
            out += pt.alternative_text
        elif hasattr(pt, "alternative_text"):
            out += pt.alternative_text
        elif isinstance(pt, dict):
            out += pt.get("alternative_text", "")
        else:
            out += str(pt)
    return out


def _render_line(pt):
    if not hasattr(pt, "alternative_text"):
        return str(pt)
    return pt.alternative_text


def rich_text(method, hidden=False):
    """Текст rich-метода: visible = параграфы + summary; hidden=True добавляет
    и содержимое сворачиваемых details-блоков."""
    rm = method.rich_message
    lines = []
    for b in rm.blocks:
        btype = getattr(b, "type", None)
        if btype == "paragraph":
            lines.append(_render_paragraph(b))
        elif btype == "details":
            lines.append(_render_plain(b.summary))
            if hidden:
                for inner in b.blocks:
                    if getattr(inner, "type", None) == "paragraph":
                        lines.append(_render_paragraph(inner))
    return "\n".join(lines)


def _render_paragraph(b):
    parts = b.text
    if isinstance(parts, str):
        return unescape(parts)
    line = ""
    for pt in parts:
        if isinstance(pt, dict):
            line += _entity_text(pt)
        elif hasattr(pt, "text") and hasattr(pt, "type"):
            # entity bold/quotation: вложенный текст — тоже RichTextUnion
            inner = pt.text
            line += (
                inner
                if isinstance(inner, str)
                else "".join(
                    _entity_text(dict(t)) if isinstance(t, dict) else str(t)
                    for t in inner
                )
            )
        elif hasattr(pt, "alternative_text"):
            line += pt.alternative_text
        else:
            line += str(pt)
    return line


def _entity_text(pt):
    if pt.get("type") == "custom_emoji":
        return pt.get("alternative_text", "")
    return pt.get("text", "")


def _render_plain(s):
    if isinstance(s, str):
        return unescape(s)
    return s


import re as _re

_TAG = _re.compile(r'<tg-emoji emoji-id="\d+">([^<]*)</tg-emoji>')


def body_lines(method, expected):
    """Разница строк rich-сообщения и шаблона.

    Шаблоны хранят сырые tg-emoji-теги и html-сущности; rich-рендер отдаёт
    alternative_text сущности без пустых строк. Нормализуем шаблон под
    рендер и сверяем построчно; summary «деталей» тоже убираем.
    """
    from html import unescape

    summary_like = {
        "📖 Показать подробности",
        "📖 Показать подробно",
        "📖 Что дают фильтры",
        "📖 Всё о Premium",
        "📖 Хранение данных и санкции",
        "📖 Show details",
        "📖 What the filters give",
        "📖 All about Premium",
        "📖 Data retention and consequences",
        "📖 Показати подробиці",
        "📖 Що дають фільтри",
        "📖 Все про Premium",
        "📖 Зберігання даних і санкції",
    }
    actual = {l for l in rich_text(method, hidden=True).split("\n") if l.strip()}
    want = set(
        _TAG.sub(
            r"\1",
            unescape(expected),
        ).split("\n")
    )
    want = {l for l in want if l.strip()}
    return actual - want - summary_like, want - actual - summary_like


def button_texts(method):
    """Ярлыки кнопок rich-метода — блоки "buttons"; NBSP-выравнивание срезаю."""
    labels = []
    for b in method.rich_message.blocks:
        if getattr(b, "type", None) != "buttons":
            continue
        for btn in b.buttons:
            text = btn.text if not isinstance(btn, dict) else btn.get("text", "")
            labels.append(text.replace("\u00a0", " ").strip())
    return labels


def callback_texts(method):
    """callback_data кнопок rich-метода в порядке ярлыков button_texts()."""
    data = []
    for b in method.rich_message.blocks:
        if getattr(b, "type", None) != "buttons":
            continue
        for btn in b.buttons:
            cb = (
                btn.callback_data
                if not isinstance(btn, dict)
                else btn.get("callback_data")
            )
            data.append(cb)
    return data

"""Карта кастомных эмодзи: скан исходников, реальные символы из Telegram, образцы.

Один вызов /icons (handlers/admin.py) показывает владельцу каждую кнопку,
каждое текстовое место с тегом и живой рендер всех иконок. Скан идёт по
исходникам в контейнере (Dockerfile уже копирует всё дерево), поэтому карта
всегда описывает тот же код, который реально запущен на проде.
"""

import re
from dataclasses import dataclass
from pathlib import Path

TAG_RE = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>')
ICON_RE = re.compile(r'icon_custom_emoji_id="(\d+)"')
# Тестовое дерево и сторонние копии -- не часть работающего интерфейса.
SKIP_DIRS = {
    "__pycache__",
    ".venv",
    "tests",
    "migrations",
    "fixtures",
    "anon-owner-notify-20260907",
}
FETCH_CHUNK = 100  # лимит Bot API на один getCustomEmojiStickers


@dataclass(frozen=True)
class EmojiRef:
    emoji_id: str
    kind: str  # button | text
    file: str  # keyboards/inline.py
    line: int
    char: str | None  # есть только у текстов -- символ внутри тега


def repo_root() -> Path:
    """Корень дерева, в котором живёт запущенный бот (контейнер /app, тесты - cwd)."""
    here = Path.cwd()
    for candidate in (here, *here.parents[:2]):
        if (candidate / "keyboards" / "texts.py").is_file():
            return candidate
    return here


def scan_emoji_refs(root: Path) -> list[EmojiRef]:
    refs: list[EmojiRef] = []
    for py in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in py.parts):
            continue
        try:
            lines = py.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rel = py.relative_to(root).as_posix()
        for no, line in enumerate(lines, 1):
            for match in ICON_RE.finditer(line):
                refs.append(EmojiRef(match.group(1), "button", rel, no, None))
            for match in TAG_RE.finditer(line):
                refs.append(EmojiRef(match.group(1), "text", rel, no, match.group(2)))
    return refs


def group_by_id(refs: list[EmojiRef]) -> list[tuple[str, list[EmojiRef]]]:
    """Идентификаторы по убыванию числа мест: чем шире иконка, тем раньше."""
    buckets: dict[str, list[EmojiRef]] = {}
    for ref in refs:
        buckets.setdefault(ref.emoji_id, []).append(ref)
    return sorted(buckets.items(), key=lambda item: -len(item[1]))


def chunked(seq, size):
    for start in range(0, len(seq), size):
        yield seq[start : start + size]


async def fetch_characters(bot, emoji_ids: list[str]):
    """Реальные символы и наборы от Telegram; отсутствующие в ответе - мусорные id.

    Возвращает (карта id -> (символ, набор), всегда_ok). Сбой сети не ломает
    карту: держим твёрдый фолбэк на символы из тегов и без меток живости.
    """
    chars: dict[str, tuple[str, str]] = {}
    ok, expected = True, [i for i in emoji_ids if i]
    for chunk in chunked(expected, FETCH_CHUNK):
        try:
            stickers = await bot.get_custom_emoji_stickers(chunk)
        except Exception:
            ok = False
            break
        for sticker in stickers or []:
            chars[sticker.custom_emoji_id] = (sticker.emoji, sticker.set_name)
    # Полнота: если нашли почти все - считаем недостающие реально битыми.
    ok = ok and all(i in chars for i in expected)
    return chars, ok


def build_map_pages(grouped, backmap, chars, fetch_ok) -> list[str]:
    """Компактная карта: одна строка на иконку, живой тег + число мест."""
    from html import escape

    total_spots = sum(len(refs) for _, refs in grouped)
    head = (
        f"🎨 <b>Карта кастомных эмодзи</b>\n"
        f"Иконок: <b>{len(grouped)}</b> · мест: <b>{total_spots}</b>"
        + (
            " · символы из Telegram\n\n"
            if fetch_ok
            else "\n(<i>символы из тегов, Telegram недоступен</i>)\n\n"
        )
    )
    lines = []
    for emoji_id, refs in grouped:
        char = chars.get(emoji_id, (None, None))[0]
        if char is None:
            char = next((r.char for r in refs if r.char), "•")
        tag = f'<tg-emoji emoji-id="{emoji_id}">{escape(char)}</tg-emoji>'
        key = backmap.get(emoji_id)
        slot_note = f" · слот: {escape(str(key))}" if key else ""
        buttons = sum(1 for r in refs if r.kind == "button")
        texts = len(refs) - buttons
        alive = "✅" if fetch_ok and emoji_id in chars else ("⚠️" if fetch_ok else "·")
        lines.append(
            f"{alive} {tag} <code>{emoji_id}</code> · "
            f"кнопок {buttons} · текстов {texts}{slot_note}"
        )
    # Держим один наряд в пределах Telegram: партия≈45 иконок - одно сообщение.
    pages, buf = [head], head
    for line in lines:
        if len(buf) + len(line) + 1 > 3800:
            pages.append("")
        pages[-1] += line + "\n"
        buf = pages[-1]
    return pages


def demo_inline_kb(grouped, chars, per_row: int = 4):
    """Все иконки настоящими inline-кнопками: владелец видит рендер, не код.

    Нажатие ведёт на карточку иконки (admin:icon:{id}) - там назначение на слот.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows, row = [], []
    for no, (emoji_id, refs) in enumerate(grouped, 1):
        char = chars.get(emoji_id, (None, None))[0]
        char = char or next((r.char for r in refs if r.char), "") or "•"
        row.append(
            InlineKeyboardButton(
                text=f"{no} {char}",
                icon_custom_emoji_id=emoji_id,
                callback_data=f"admin:icon:{emoji_id}",
            )
        )
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def slots_overview() -> list[tuple[str, str | None]]:
    """Слоты каталога reply-клавиатур: (ключ, действующая иконка)."""
    from services import emoji_settings

    return [(key, emoji_settings.effective_id(key)) for key in list(_slot_keys())]


def _slot_keys() -> list[str]:
    from keyboards import texts as T

    return list(T.EMOJI_IDS.keys())


def assign_slots_kb(emoji_id: str, chars):
    """Кнопки-слоты, на которые можно назначить иконку emoji_id."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows, row = [], []
    for key, _current in slots_overview():
        label = _slot_label(key)
        row.append(
            InlineKeyboardButton(
                text=label,
                icon_custom_emoji_id=emoji_id,
                callback_data=f"admin:set:{_slot_index(key)}:{emoji_id}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _slot_label(key) -> str:
    from keyboards import texts as T

    return T.label(key, "ru")


def _slot_index(key) -> int:
    return _slot_keys().index(key)


def _slot_by_index(index: int):
    keys = _slot_keys()
    if 0 <= index < len(keys):
        return keys[index]
    return None


def slots_kb(chars, per_row: int = 3):
    """Все слоты каталога: каждая кнопка несёт действующую иконку слота."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows, row = [], []
    for key, current in slots_overview():
        char = chars.get(current, (None, None))[0] if current else None
        mark = " ✏" if key in emoji_settings_overrides() else ""
        suffix = "" if current else " —"
        row.append(
            InlineKeyboardButton(
                text=f"{char or '—'} {_slot_label(key)}{mark}{suffix}",
                icon_custom_emoji_id=current or None,
                callback_data=f"admin:slot:{_slot_index(key)}",
            )
        )
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def emoji_settings_overrides() -> dict[str, str]:
    from services import emoji_settings

    return dict(emoji_settings._cache)


async def send_emoji_map(message, backmap):
    """Карта: компактный список, демо-инлайн с карточками, слоты, подсказка."""
    from aiogram.types import InlineKeyboardButton

    refs = scan_emoji_refs(repo_root())
    grouped = group_by_id(refs)
    chars, ok_fetch = await fetch_characters(message.bot, [i for i, _ in grouped])
    for page in build_map_pages(grouped, backmap, chars, ok_fetch):
        await message.answer(page)
    demo = demo_inline_kb(grouped, chars)
    demo.inline_keyboard.append(
        [
            InlineKeyboardButton(
                text="⚙️ Слоты меню (назначение иконок)",
                callback_data="admin:slots",
            )
        ]
    )
    await message.answer(
        "🔬 <b>Живой образец</b> — нажми иконку, чтобы осмотреть/назначить её:",
        reply_markup=demo,
    )
    from keyboards.reply import main_menu_kb

    await message.answer(
        "⌨️ <b>Reply-клавиатура меню</b> — текущий каркас с иконками слотов.\n"
        "Вернуться к рабочему меню — кнопка «Меню» или любая из восьми.",
        reply_markup=main_menu_kb(),
    )

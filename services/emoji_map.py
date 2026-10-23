"""Карта кастомных эмодзи: скан исходников, реальные символы из Telegram, образцы.

Один вызов /icons (handlers/admin.py) показывает владельцу каждую кнопку,
каждое текстовое место с тегом и живой рендер всех иконок. Скан идёт по
исходникам в контейнере (Dockerfile уже копирует всё дерево), поэтому карта
всегда описывает тот же код, который реально запущен на проде.
"""

from dataclasses import dataclass
import re
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


def _where(refs: list[EmojiRef], limit: int = 3) -> str:
    spots = [f"{ref.file}:{ref.line}" for ref in refs[:limit]]
    rest = len(refs) - limit
    text = "\n".join(spots)
    if rest > 0:
        text += f" … и ещё {rest}"
    return text


def build_map_pages(grouped, backmap, chars, fetch_ok, per_page: int = 14) -> list[str]:
    """Карта страницами: каждый id - строка с живым рендером и местами в коде."""
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
    pages = [head]
    for no, (emoji_id, refs) in enumerate(grouped, 1):
        char = chars.get(emoji_id, (None, None))[0]
        if char is None:
            char = next((r.char for r in refs if r.char), "•")
        tag = f'<tg-emoji emoji-id="{emoji_id}">{escape(char)}</tg-emoji>'
        key = backmap.get(emoji_id)
        label = f" · ключ <code>{escape(str(key))}</code>" if key else ""
        buttons = sum(1 for r in refs if r.kind == "button")
        texts = len(refs) - buttons
        alive = "✅" if fetch_ok and emoji_id in chars else ("⚠️" if fetch_ok else "·")
        # Заголовок с живым тегом сам и есть образец: как выглядит у юзеров.
        line = (
            f"{alive} <b>#{no}</b> {tag} <code>{emoji_id}</code>{label}\n"
            f"кнопок: {buttons} · текстов: {texts}\n"
            f"<pre>{escape(_where(refs))}</pre>\n"
        )
        if len(pages[-1]) + len(line) > 3600:
            pages.append("")
        pages[-1] += line
    return pages


def demo_inline_kb(grouped, chars, per_row: int = 4):
    """Все иконки настоящими inline-кнопками:_owner видит рендер, не код."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows, row = [], []
    for no, (emoji_id, refs) in enumerate(grouped, 1):
        char = chars.get(emoji_id, (None, None))[0]
        char = char or next((r.char for r in refs if r.char), "") or "•"
        row.append(
            InlineKeyboardButton(
                text=f"{no} {char}",
                icon_custom_emoji_id=emoji_id,
                callback_data="admin:emoji:demo",
            )
        )
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_emoji_map(message, backmap):
    """Карта: страницы текста, демонстрационный инлайн-скрин, живое меню, подсказка."""
    refs = scan_emoji_refs(repo_root())
    grouped = group_by_id(refs)
    chars, fetch_ok = await fetch_characters(message.bot, [i for i, _ in grouped])
    for page in build_map_pages(grouped, backmap, chars, fetch_ok):
        await message.answer(page)
    await message.answer(
        "🔬 <b>Живой образец</b> — каждая кнопка несёт свою иконку:",
        reply_markup=demo_inline_kb(grouped, chars),
    )
    from keyboards.reply import main_menu_kb

    await message.answer(
        "⌨️ <b>Reply-клавиатура меню</b> — отправляет настоящий persistent-каркас,\n"
        "вернуться к рабочему меню — кнопка «Меню» или любая из восьми:",
        reply_markup=main_menu_kb(),
    )

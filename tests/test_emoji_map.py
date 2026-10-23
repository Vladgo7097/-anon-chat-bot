import asyncio

from aiogram.types import InlineKeyboardMarkup

from keyboards import texts as T
from keyboards.reply import main_menu_kb
from services.emoji_map import (
    EmojiRef,
    build_map_pages,
    chunked,
    demo_inline_kb,
    fetch_characters,
    group_by_id,
    repo_root,
    scan_emoji_refs,
)

IDEA_BUTTON_ID = "5967432491684860012"  # иконка кнопки «Идея» в reply-меню


def test_scan_finds_button_and_text_refs():
    refs = scan_emoji_refs(repo_root())
    assert refs, "скан не должен быть пуст на живом дереве"
    assert any(r.emoji_id == IDEA_BUTTON_ID and r.kind == "button" for r in refs), (
        "иконка кнопки Идея должна находиться в каталоге"
    )
    text_refs = [r for r in refs if r.kind == "text" and r.char]
    assert text_refs, "теги <tg-emoji> с символами должны находиться"


def test_group_order_most_used_first():
    refs = scan_emoji_refs(repo_root())
    grouped = group_by_id(refs)
    counts = [len(refs) for _, refs in grouped]
    assert counts == sorted(counts, reverse=True)
    ids = [emoji_id for emoji_id, _ in grouped]
    assert IDEA_BUTTON_ID in ids


def test_fetch_characters_fail_soft():
    class BrokenBot:
        async def get_custom_emoji_stickers(self, ids):  # noqa: ANN001
            raise RuntimeError("сеть OFF")

    # сервис обязан возвращать кортеж и не ронять на сбое сети
    chars, ok = asyncio.run(fetch_characters(BrokenBot(), ["777"]))
    assert ok is False and chars == {}


def test_build_map_pages_escapes_and_chunks():
    refs = [
        EmojiRef("111", "text", "evil<w>.py", 3, "<&>"),
        EmojiRef("111", "button", "ok.py", 5, None),
    ]
    grouped = group_by_id(refs)
    pages = build_map_pages(grouped, {"111": "FIRE"}, {}, fetch_ok=False)
    joined = "\n".join(pages)
    assert "&lt;&amp;&gt;" in joined
    assert "&lt;w&gt;" in joined
    assert '<tg-emoji emoji-id="111">' in joined
    assert all(len(p) < 4096 for p in pages)


def test_demo_inline_kb_shape():
    refs = [EmojiRef(str(100 + n), "button", "a.py", n, None) for n in range(1, 11)]
    kb = demo_inline_kb(group_by_id(refs), {}, per_row=4)
    assert isinstance(kb, InlineKeyboardMarkup)
    rows = kb.inline_keyboard
    assert all(len(r) == 4 for r in rows[:-1]) and 0 < len(rows[-1]) <= 4
    flat = [b for r in rows for b in r]
    assert len({b.icon_custom_emoji_id for b in flat}) == 10
    assert {b.callback_data for b in flat} == {"admin:emoji:demo"}


def test_chunked():
    assert list(chunked([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    assert list(chunked([], 3)) == []


def test_menu_sample_is_real_reply_kb():
    kb = main_menu_kb("ru")
    labels = {btn.text for row in kb.keyboard for btn in row}
    assert T.IDEA in labels, "в сэмпле должна быть настоящая кнопка Идея"


def test_backmap_covers_idea_button():
    backmap = {str(v): k for k, v in T.EMOJI_IDS.items()}
    # ключ каталога -- сам лейбл кнопки, id у IDEA заимствован у INVITE
    assert backmap.get(IDEA_BUTTON_ID) == T.IDEA
    assert T.EMOJI_IDS.get(T.IDEA) == IDEA_BUTTON_ID

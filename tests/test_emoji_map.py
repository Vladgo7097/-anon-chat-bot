"""Карта иконок (/icons) и слоты reply-каталога.

Скан дерева и компактная карта проверяются на живом репо; переопределения
слотов - на тестовой базе (DatabaseCase); сборка клавиатур - напрямую через
кэш переопределений.
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.types import InlineKeyboardMarkup

from keyboards import texts as T
from keyboards.reply import kb_button, main_menu_kb
from services.emoji_map import (
    EmojiRef,
    assign_slots_kb,
    build_map_pages,
    chunked,
    demo_inline_kb,
    fetch_characters,
    group_by_id,
    repo_root,
    scan_emoji_refs,
    slots_kb,
    slots_overview,
)
from services.emoji_settings import (
    clear_override,
    effective_id,
    load_cache,
    set_override,
)
from test_chat_duration import DatabaseCase

IDEA_BUTTON_ID = "5967432491684860012"  # иконка кнопки «Идея» в reply-меню


class ScanAndMapTests(unittest.TestCase):
    def test_scan_finds_button_and_text_refs(self):
        refs = scan_emoji_refs(repo_root())
        self.assertTrue(refs)
        self.assertTrue(
            any(r.emoji_id == IDEA_BUTTON_ID and r.kind == "button" for r in refs)
        )
        self.assertTrue(any(r.kind == "text" and r.char for r in refs))

    def test_group_order_most_used_first(self):
        refs = scan_emoji_refs(repo_root())
        grouped = group_by_id(refs)
        counts = [len(refs) for _, refs in grouped]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertIn(IDEA_BUTTON_ID, [emoji_id for emoji_id, _ in grouped])

    def test_fetch_characters_fail_soft(self):
        class BrokenBot:
            async def get_custom_emoji_stickers(self, ids):
                raise RuntimeError("сеть OFF")

        chars, ok = asyncio.run(fetch_characters(BrokenBot(), ["777"]))
        self.assertFalse(ok)
        self.assertEqual(chars, {})

    def test_compact_map_pages_one_line_per_icon_and_escaped(self):
        refs = [
            EmojiRef("111", "text", "evil<w>.py", 3, "<&>"),
            EmojiRef("111", "button", "ok.py", 5, None),
        ]
        pages = build_map_pages(group_by_id(refs), {"111": "FIRE"}, {}, False)
        joined = "\n".join(pages)
        self.assertIn("&lt;&amp;&gt;", joined)
        self.assertNotIn("<w>", joined)  # имя файла без сырых углов
        self.assertIn('<tg-emoji emoji-id="111">', joined)
        self.assertIn("кнопок 1 · текстов 1", joined)
        self.assertTrue(all(len(p) < 4096 for p in pages))

    def test_compact_map_is_not_taller_than_two_messages(self):
        refs = scan_emoji_refs(repo_root())
        pages = build_map_pages(group_by_id(refs), {}, {}, True)
        self.assertLessEqual(len(pages), 2, "карта должна остаться компактной")

    def test_demo_buttons_open_icon_cards(self):
        refs = [EmojiRef(str(100 + n), "button", "a.py", n, None) for n in range(1, 11)]
        kb = demo_inline_kb(group_by_id(refs), {}, per_row=4)
        self.assertIsInstance(kb, InlineKeyboardMarkup)
        flat = [b for row in kb.inline_keyboard for b in row]
        self.assertTrue(
            all(
                b.icon_custom_emoji_id and b.callback_data.startswith("admin:icon:")
                for b in flat
            )
        )
        self.assertEqual(len({b.icon_custom_emoji_id for b in flat}), 10)

    def test_chunked(self):
        self.assertEqual(list(chunked([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]])
        self.assertEqual(list(chunked([], 3)), [])


class SlotOverviewTests(unittest.TestCase):
    def setUp(self):
        from services import emoji_settings

        emoji_settings._cache = {}

    def test_slots_mirror_catalog_without_overrides(self):
        overview = dict(slots_overview())
        # IDEA перезапущена без иконки по правилу dj: слот жив, дефолт пуст.
        self.assertIsNone(overview.get(T.IDEA))
        self.assertIsNone(effective_id(T.IDEA))

    def test_menu_sample_is_real_reply_kb(self):
        kb = main_menu_kb("ru")
        labels = {btn.text for row in kb.keyboard for btn in row}
        self.assertIn(T.IDEA, labels)


class OverrideFlowTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await load_cache()

    async def asyncTearDown(self):
        from services import emoji_settings

        emoji_settings._cache = {}
        await super().asyncTearDown()

    async def test_set_override_changes_render_and_persists(self):
        await set_override(T.IDEA, "999", by_user=1)
        self.assertEqual(effective_id(T.IDEA), "999")
        button = kb_button(T.IDEA, "ru")
        self.assertEqual(button.icon_custom_emoji_id, "999")
        # перезагрузка кэша из базы не теряет запись
        await load_cache()
        self.assertEqual(effective_id(T.IDEA), "999")

    async def test_empty_override_hides_icon(self):
        await set_override(T.STATS, "", by_user=1)
        self.assertIsNone(effective_id(T.STATS))
        self.assertIsNone(kb_button(T.STATS, "ru").icon_custom_emoji_id)

    async def test_clear_override_returns_to_code_default(self):
        await set_override(T.STATS, "777", by_user=1)
        await clear_override(T.STATS)
        self.assertEqual(effective_id(T.STATS), T.EMOJI_IDS[T.STATS])

    async def test_unknown_slot_falls_back_to_none(self):
        self.assertIsNone(effective_id("Несуществующий слот"))


class AdminKeyboardShapeTests(unittest.TestCase):
    def test_slots_kb_lists_catalog_with_live_icons(self):
        kb = slots_kb({})
        flat = [b for row in kb.inline_keyboard for b in row]
        self.assertGreaterEqual(len(flat), 20, f"слотов каталога: {len(flat)}")
        self.assertTrue(all(b.callback_data.startswith("admin:slot:") for b in flat))
        texts = " | ".join(b.text for b in flat)
        self.assertIn("Предложить идею", texts)

    def test_assign_kb_targets_every_slot(self):
        kb = assign_slots_kb("777")
        flat = [b for row in kb.inline_keyboard for b in row]
        self.assertTrue(all(b.icon_custom_emoji_id == "777" for b in flat))
        self.assertTrue(all(b.callback_data.startswith("admin:iconset:") for b in flat))

    def test_slots_for_id_marks_default_and_override(self):
        from services import emoji_settings
        from services.emoji_map import slots_for_id

        emoji_settings._cache = {}
        slots = slots_for_id(IDEA_BUTTON_ID)
        # Иконка у Invite: IDEA без иконки, заимствования больше нет.
        self.assertIn((T.INVITE, "дефолт"), slots)
        self.assertNotIn(T.IDEA, [k for k, _ in slots])
        emoji_settings._cache = {T.INVITE: IDEA_BUTTON_ID}
        slots = slots_for_id(IDEA_BUTTON_ID)
        self.assertIn((T.INVITE, "переопределение"), slots)

    def test_slot_payload_roundtrip_label_and_index(self):
        from services.emoji_map import (
            slot_keys,
            slot_from_payload,
            slot_payload,
        )

        for key in slot_keys():
            self.assertEqual(slot_from_payload(slot_payload(key, 64 - 11 - 2)), key)
        # узкий бюджет выталкивает длинный лейбл в индексный фолбэк по каталогу
        longest = max(slot_keys(), key=lambda k: len(k.encode("utf-8")))
        self.assertTrue(slot_from_payload(slot_payload(longest, 7)) is not None)
        self.assertIsNone(slot_from_payload("пустой-вне-каталога"))

    def test_callbacks_fit_telegram_budget(self):
        from services.emoji_map import slot_keys, slot_payload

        sample_id = "0" * 19  # длина реального custom-emoji id
        for key in slot_keys():
            slot_cb = f"admin:slot:{slot_payload(key, 64 - 11 - 2)}"
            set_cb = f"admin:iconset:{slot_payload(key, 64 - 14 - 20 - 2)}:{sample_id}"
            self.assertLessEqual(len(slot_cb.encode("utf-8")), 64)
            self.assertLessEqual(len(set_cb.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main()

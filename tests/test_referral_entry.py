"""Referral entry points after the menu swap.

The program's only referral CTA used to be a persistent-menu button; as of
the idea button the menu slot is taken back, so the referral invitation now
lives where its statistics live - in the rewards card. The stale
``search:invite`` callback stays attached for buttons already sent to old
chats; new keyboards never render it, and the search timeout screen keeps
its contextual "notify me" action only.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.search import nobody_keyboard
from keyboards import texts as T
from keyboards.reply import main_menu_kb


class ReferralEntryTests(unittest.TestCase):
    def test_the_main_menu_no_longer_carries_the_referral_button(self):
        # Идеи вытеснили рефералку из меню: кнопка уходит, ссылки остаются
        # в карточке наград. Три языка, единый каркас клавиатуры.
        for lang in ("ru", "en", "uk"):
            with self.subTest(lang=lang):
                labels = [
                    button.text for row in main_menu_kb(lang).keyboard for button in row
                ]
                self.assertEqual(labels.count(T.LABELS[T.INVITE][lang]), 0)
                self.assertEqual(labels.count(T.LABELS[T.IDEA][lang]), 1)

    def test_the_search_timeout_screen_does_not_repeat_the_referral(self):
        for lang in ("ru", "en", "uk"):
            with self.subTest(lang=lang):
                callbacks = [
                    button.callback_data
                    for row in nobody_keyboard(lang).inline_keyboard
                    for button in row
                ]
                self.assertEqual(callbacks, ["search:wait"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

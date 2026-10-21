"""One referral entry point: the persistent main menu.

The search timeout screen used to repeat the same invite CTA, so the button
appeared in two unrelated places. The callback stays for buttons already sent
to old chats; new keyboards never render it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.search import nobody_keyboard
from keyboards import texts as T
from keyboards.reply import main_menu_kb


class ReferralEntryTests(unittest.TestCase):
    def test_the_main_menu_has_exactly_one_referral_button(self):
        for lang in ("ru", "en", "uk"):
            with self.subTest(lang=lang):
                labels = [
                    button.text for row in main_menu_kb(lang).keyboard for button in row
                ]
                self.assertEqual(labels.count(T.LABELS[T.INVITE][lang]), 1)

    def test_the_search_timeout_screen_does_not_repeat_the_referral(self):
        for lang in ("ru", "en", "uk"):
            with self.subTest(lang=lang):
                callbacks = [
                    button.callback_data
                    for row in nobody_keyboard(lang).inline_keyboard
                    for button in row
                ]
                self.assertEqual(callbacks, ["search:wait"])

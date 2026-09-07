"""Every custom emoji the bot sends must carry a real emoji as its fallback.

Telegram rejects (or renders as the bare numeric id) a <tg-emoji> whose body is
not an emoji. Two ways that has happened here: a word used as the body, and an
escape sequence left literal because the string it was written in was raw.
"""
import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The id is either 15+ digits or an f-string placeholder filled in at runtime;
# the body is whatever sits between the tags.
TAG = re.compile(r"<tg-emoji\s+emoji-id=\\?\"([^\"\\]*)\\?\">(.*?)</tg-emoji>")
PLACEHOLDER = re.compile(r"^\{[^{}]+\}$")
# A ZWJ sequence such as 👨‍🦳 is several code points but still one emoji.
LONGEST_EMOJI = 12
SOURCES = sorted(p for p in ROOT.rglob("*.py")
                 if "__pycache__" not in p.parts and ".pytest_cache" not in p.parts)


def tags():
    for path in SOURCES:
        text = io.open(path, encoding="utf-8").read()
        for match in TAG.finditer(text):
            line = text[:match.start()].count("\n")+1
            yield f"{path.relative_to(ROOT)}:{line}", match.group(1), match.group(2)


class CustomEmojiMarkupTests(unittest.TestCase):
    def test_the_sources_actually_contain_custom_emoji(self):
        self.assertGreater(len(list(tags())), 50)

    def test_every_literal_id_is_a_telegram_document_id(self):
        for where, emoji_id, _ in tags():
            with self.subTest(where):
                if PLACEHOLDER.match(emoji_id):
                    continue  # chosen at runtime, checked where it is built
                self.assertTrue(emoji_id.isdigit(), f"{where}: id {emoji_id!r} is not numeric")
                self.assertGreaterEqual(len(emoji_id), 15, f"{where}: id {emoji_id!r} is too short")

    def test_no_fallback_is_a_word_or_an_unrendered_escape(self):
        for where, _, body in tags():
            with self.subTest(where):
                if PLACEHOLDER.match(body):
                    continue
                self.assertTrue(body, f"{where}: empty fallback")
                self.assertNotIn("\\", body, f"{where}: {body!r} is a literal escape, not a character")
                self.assertFalse(body.isascii(),
                                 f"{where}: {body!r} is plain text, Telegram needs an emoji")
                self.assertLessEqual(len(body), LONGEST_EMOJI,
                                     f"{where}: {body!r} is longer than one emoji")


class ProfileScreenTests(unittest.TestCase):
    def source(self):
        return io.open(ROOT/"handlers"/"profile.py", encoding="utf-8").read()

    def test_the_id_line_uses_a_custom_emoji(self):
        line = next(l for l in self.source().split("\n") if "Твой профиль" in l)
        self.assertNotIn("🆔 ID:", line, "the ID line lost its custom emoji")
        self.assertIn("</tg-emoji> ID:", line)

    def test_the_gender_icon_pairs_every_id_with_its_own_emoji(self):
        from handlers.profile import GENDER_ICONS
        for gender, (emoji_id, fallback) in GENDER_ICONS.items():
            with self.subTest(gender):
                self.assertTrue(emoji_id.isdigit())
                self.assertGreaterEqual(len(emoji_id), 15)
                self.assertFalse(fallback.isascii())
                self.assertLessEqual(len(fallback), LONGEST_EMOJI)

    def test_men_and_women_do_not_share_one_icon(self):
        from handlers.profile import GENDER_ICONS
        self.assertNotEqual(GENDER_ICONS["male"], GENDER_ICONS["female"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""A single unusable custom emoji id must not take the admin panel down.

Telegram answers DOCUMENT_INVALID for an id that does not exist, and refuses
the whole message — which is how a typo in one id made /admin unreachable.
"""
import json
import os
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aiogram.exceptions import TelegramBadRequest
from handlers.admin import CUSTOM_EMOJI, safe_send
from test_message_markup import TAG, tags

TEXT = ('<tg-emoji emoji-id="5312160339335347417">💰</tg-emoji> Оплачено: 5\n'
        '<tg-emoji emoji-id="5920281855378068765">⭐</tg-emoji> Premium: нет')


def rejection(description):
    return TelegramBadRequest(method=SimpleNamespace(), message=description)


class PlainTextFallbackTests(unittest.TestCase):
    def test_the_emoji_survives_and_no_tag_is_left_behind(self):
        plain = CUSTOM_EMOJI.sub(r"\1", TEXT)
        self.assertNotIn("tg-emoji", plain)
        self.assertIn("💰 Оплачено: 5", plain)
        self.assertIn("⭐ Premium: нет", plain)

    def test_a_stray_closing_tag_is_never_produced(self):
        # The first attempt at this fallback stripped only the opening tags,
        # so Telegram then failed with "Unexpected end tag".
        self.assertNotIn("</tg-emoji>", CUSTOM_EMOJI.sub(r"\1", TEXT))


class SafeSendTests(unittest.IsolatedAsyncioTestCase):
    def message(self, *effects):
        return SimpleNamespace(chat=SimpleNamespace(id=1),
                               answer=AsyncMock(side_effect=list(effects)))

    async def test_a_good_message_is_sent_once_and_untouched(self):
        message = self.message("sent")
        await safe_send(message, TEXT, reply_markup="kb")
        message.answer.assert_awaited_once_with(TEXT, reply_markup="kb")

    async def test_a_rejected_custom_emoji_is_retried_as_plain_text(self):
        message = self.message(rejection("Bad Request: DOCUMENT_INVALID"), "sent")
        await safe_send(message, TEXT)
        self.assertEqual(message.answer.await_count, 2)
        retried = message.answer.await_args.args[0]
        self.assertNotIn("tg-emoji", retried)
        self.assertIn("💰", retried)

    async def test_any_other_failure_is_not_swallowed(self):
        message = self.message(rejection("Bad Request: chat not found"))
        with self.assertRaises(TelegramBadRequest):
            await safe_send(message, TEXT)
        message.answer.assert_awaited_once()


@unittest.skipUnless(os.getenv("BOT_TOKEN"), "needs BOT_TOKEN to ask Telegram")
class LiveCustomEmojiTests(unittest.TestCase):
    """Opt-in: asks Telegram whether every id in the sources actually exists.

    Run it with the bot token in the environment before a deploy; an id that
    does not resolve here is the one that will break a whole screen.
    """

    def test_every_id_resolves(self):
        ids = sorted({emoji_id for _, emoji_id, _ in tags() if emoji_id.isdigit()})
        found = set()
        for start in range(0, len(ids), 100):
            query = urllib.parse.quote(json.dumps(ids[start:start+100]))
            url = (f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}"
                   f"/getCustomEmojiStickers?custom_emoji_ids={query}")
            with urllib.request.urlopen(url) as response:
                found.update(s["custom_emoji_id"] for s in json.load(response)["result"])
        missing = {emoji_id: [w for w, i, _ in tags() if i == emoji_id]
                   for emoji_id in ids if emoji_id not in found}
        self.assertEqual(missing, {}, f"unusable custom emoji ids: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

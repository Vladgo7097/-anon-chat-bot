"""Every inline button in the code must reach a handler; no dead callback_data."""
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram.types import CallbackQuery, Chat, Message, User

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDERS = {
    "report_id": "1", "job.id": "1", "days": "1", "telegram_id": "1", "offset": "1",
    "p.code": "premium_1m", "session_id": "abc", "chat.session_id": "abc", "sid": "abc",
    "reason": "spam",
}
USER = User(id=1, is_bot=False, first_name="a")
CHAT = Chat(id=1, type="private")
MESSAGE = Message(message_id=1, date=datetime(2026, 1, 1, tzinfo=timezone.utc), chat=CHAT, from_user=USER, text="x")


def emitted_callbacks():
    """Collect callback_data literals from the source, filling f-string slots."""
    values = set()
    for path in sorted(ROOT.glob("*/*.py")):
        if path.parts[-2] == "tests":
            continue
        for raw in re.findall(r'callback_data=f?"([^"]+)"', path.read_text(encoding="utf-8")):
            resolved = re.sub(r"\{([^}]+)\}", lambda m: PLACEHOLDERS.get(m.group(1), "1"), raw)
            values.add(resolved)
    return sorted(values)


class CallbackRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_button_has_a_handler(self):
        from handlers import load_routers

        routers = load_routers()
        values = emitted_callbacks()
        self.assertGreater(len(values), 30, "callback_data literals were not collected")
        unrouted = []
        for data in values:
            query = CallbackQuery(id="1", from_user=USER, chat_instance="1", message=MESSAGE, data=data)
            if not any([handler for router in routers for handler in router.callback_query.handlers
                        if (await handler.check(query))[0]]):
                unrouted.append(data)
        self.assertEqual(unrouted, [], f"callback_data without a handler: {unrouted}")

    async def test_callback_data_fits_telegram_limit(self):
        for data in emitted_callbacks():
            with self.subTest(data=data):
                # Real session ids are 32 hex chars; placeholders are shorter.
                sized = data.replace("abc", "0" * 32)
                self.assertLessEqual(len(sized.encode("utf-8")), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)

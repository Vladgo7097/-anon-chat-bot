"""The complaint and reveal branches, plus the notices the worker and the error
trap assemble on their own: every string reaches the user in the language that
user asked in, and none of the lookups behind that can take a send down."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.exceptions import TelegramForbiddenError

from handlers.report import _partner_notice, cmd_report
from handlers.reveal import cb_consent, reveal_keyboard, reveal_request_keyboard, show_reveal
from keyboards import texts as T
from keyboards.reply import chat_kb_reply, main_menu_kb
from utils.db import user_lang

LANGS = ("ru", "en", "uk")


def message(lang="ru"):
    return SimpleNamespace(from_user=SimpleNamespace(id=123, language_code=lang),
                           answer=AsyncMock())


def callback(data, lang="ru"):
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    return SimpleNamespace(data=data, bot=bot, answer=AsyncMock(),
                           from_user=SimpleNamespace(id=123, language_code=lang),
                           message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()))


def active_chat(session="session-1"):
    return SimpleNamespace(active=AsyncMock(return_value=SimpleNamespace(session_id=session)))


class ReportScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_reason_screen_speaks_each_language(self):
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.report.ChatService", return_value=active_chat()):
                target = message(lang)
                await cmd_report(target, lang)
                self.assertEqual(target.answer.await_args.args[0],
                                 T.tr(T.REPORT_TEXT, lang)["ask"])

    async def test_reason_buttons_follow_the_screen_not_the_source_file(self):
        # The keys come from services.reports, which keeps Russian labels for
        # its own moderator notice -- the buttons must not inherit them.
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.report.ChatService", return_value=active_chat()):
                target = message(lang)
                await cmd_report(target, lang)
                labels = list(T.tr(T.REPORT_REASONS, lang).values())
                shown = [b.text for row in target.answer.await_args.kwargs["reply_markup"].inline_keyboard
                         for b in row]
                self.assertEqual(shown, labels)
                if lang != "ru":
                    # A translation may match the Russian label on a row or two,
                    # never on all six at once.
                    self.assertNotEqual(shown, list(T.REPORT_REASONS["ru"].values()))

    async def test_the_no_active_dialog_refusal_is_translated(self):
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.report.ChatService") as service:
                service.return_value.active = AsyncMock(return_value=None)
                target = message(lang)
                await cmd_report(target, lang)
                self.assertEqual(target.answer.await_args.args[0],
                                 T.tr(T.REPORT_TEXT, lang)["not_in_chat"])

    async def test_the_peer_is_told_in_their_own_language(self):
        # The complainer and the peer are rarely the same client: the "the chat
        # ended" notice has to follow the receiving side.
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.report.user_lang", AsyncMock(return_value=lang)):
                bot = SimpleNamespace(send_message=AsyncMock())
                await _partner_notice(bot, 456, "partner_left")
                self.assertEqual(bot.send_message.await_args.args[1],
                                 T.tr(T.REPORT_TEXT, lang)["partner_left"])
                self.assertEqual(
                    [b.text for row in bot.send_message.await_args.kwargs["reply_markup"].keyboard
                     for b in row],
                    [b.text for row in main_menu_kb(lang).keyboard for b in row])

    async def test_a_refusing_peer_never_breaks_the_complaint(self):
        with patch("handlers.report.user_lang", AsyncMock(return_value="uk")):
            bot = SimpleNamespace(send_message=AsyncMock(
                side_effect=TelegramForbiddenError(method="sendMessage",
                                                   message="bot was blocked")))
            await _partner_notice(bot, 456, "partner_left")
            bot.send_message.assert_awaited_once()


class RevealScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_reveal_offer_is_translated(self):
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.reveal.ChatService", return_value=active_chat()), \
                 patch("utils.db.is_premium", AsyncMock(return_value=True)):
                target = message(lang)
                await show_reveal(target, 123, lang)
                self.assertEqual(target.answer.await_args_list[0].args[0],
                                 T.tr(T.REVEAL_TEXT, lang)["intro"])
                self.assertEqual(
                    [b.text for row in target.answer.await_args_list[0].kwargs["reply_markup"].inline_keyboard
                     for b in row],
                    [T.tr(T.REVEAL_BUTTONS, lang)["offer"]])

    async def test_both_reveal_keyboards_carry_the_translated_labels(self):
        for lang in LANGS:
            buttons = T.tr(T.REVEAL_BUTTONS, lang)
            with self.subTest(lang=lang, board="offer"):
                shown = [b.text for row in reveal_keyboard("s", lang).inline_keyboard for b in row]
                self.assertEqual(shown, [buttons["offer"]])
            with self.subTest(lang=lang, board="request"):
                shown = [b.text for row in reveal_request_keyboard("s", lang).inline_keyboard for b in row]
                self.assertEqual(shown, [buttons["agree"], buttons["decline"]])

    async def test_the_mutual_notice_goes_out_in_each_receivers_language(self):
        for lang in LANGS:
            with self.subTest(lang=lang), \
                 patch("handlers.reveal.user_lang", AsyncMock(return_value=lang)):
                target = callback("v1:reveal:session-1", lang)
                result = SimpleNamespace(ok=True, code="MUTUAL", data={"profiles": {
                    1: {"username": "one", "photo": None, "anon_id": "#ONE"},
                    2: {"username": "two", "photo": None, "anon_id": "#TWO"}}})
                with patch("services.reveal.RevealService.consent", AsyncMock(return_value=result)):
                    await cb_consent(target, lang)
                self.assertEqual(target.bot.send_message.await_args.args[1],
                                 T.tr(T.REVEAL_TEXT, lang)["mutual"].format(contact="@one"))

    async def test_the_refusal_receipt_is_translated(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                target = callback("v1:reveal:no:session-1", lang)
                result = SimpleNamespace(ok=True, code="DECLINED", data={})
                with patch("services.reveal.RevealService.decline", AsyncMock(return_value=result)):
                    await cb_consent(target, lang)
                self.assertEqual(target.message.edit_text.await_args.args[0],
                                 T.tr(T.REVEAL_TEXT, lang)["declined"])


class OutsideDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def test_every_table_has_all_three_languages(self):
        tables = (T.REPORT_TEXT, T.REPORT_REASONS, T.REVEAL_TEXT, T.REVEAL_BUTTONS,
                  T.ERRORS, T.WORKER_BUTTONS, T.RATING_BUTTONS, T.UPSELL_TEXT)
        for table in tables:
            for lang in LANGS:
                with self.subTest(table=id(table), lang=lang):
                    self.assertIn(lang, table)
                    self.assertTrue(table[lang])

    def test_the_original_strings_survive_as_russian(self):
        # Guards the wording that shipped before the table existed: a typo here
        # would silently change what every Russian user reads.
        self.assertEqual(T.REPORT_REASONS["ru"]["spam"], "Спам/реклама")
        self.assertEqual(T.REVEAL_BUTTONS["ru"]["offer"], "Предложить раскрыться")
        self.assertEqual(T.ERRORS["ru"]["retry"],
                         "Не удалось выполнить действие. Попробуй ещё раз.")
        self.assertEqual(T.WORKER_BUTTONS["ru"]["similar"], "Найти похожего собеседника")
        self.assertEqual(T.RATING_BUTTONS["ru"]["yes"], "Да")

    def test_the_worker_keyboards_take_a_language(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                shown = [b.text for row in chat_kb_reply(lang).keyboard for b in row]
                self.assertEqual(shown, [T.label(T.NEXT, lang), T.label(T.STOP, lang),
                                         T.label(T.REPORT, lang), T.label(T.REVEAL, lang)])

    async def test_a_failing_language_lookup_falls_back_instead_of_raising(self):
        # This picks a label. Letting it raise would cost the whole send.
        with patch("utils.db.UserRepository") as repo:
            repo.return_value.get = AsyncMock(side_effect=OSError("no database"))
            self.assertEqual(await user_lang(123, "uk"), "uk")
            self.assertEqual(await user_lang(123), "ru")

    async def test_an_unknown_language_falls_back_to_russian(self):
        with patch("utils.db.UserRepository") as repo:
            repo.return_value.get = AsyncMock(return_value=SimpleNamespace(language="de"))
            self.assertEqual(await user_lang(123), "ru")

    async def test_the_client_language_is_used_when_the_row_has_none(self):
        with patch("utils.db.UserRepository") as repo:
            repo.return_value.get = AsyncMock(return_value=SimpleNamespace(language=None))
            self.assertEqual(await user_lang(123, "uk"), "uk")

    async def test_a_missing_row_uses_the_supplied_default(self):
        with patch("utils.db.UserRepository") as repo:
            repo.return_value.get = AsyncMock(return_value=None)
            self.assertEqual(await user_lang(123, "en"), "en")


if __name__ == "__main__":
    unittest.main()

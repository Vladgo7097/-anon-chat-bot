"""Фото из-под одного бота должно доезжать до второго.

file_id знает только тот бот, который его выдал: для второго бота это
неизвестное имя, и Telegram отвечает 400. Раньше карточка профиля после
такого ответа молча превращалась в текст, а взаимное раскрытие уходило
текстом в очередь. Здесь переливается байты.

Мок повторяет поведение Telegram ровно настолько, насколько нужно: бот
принимает любой перелитый BufferedInputFile и роняет 400 на чужом file_id.

Офлайн: сеть замокана, БД не трогается.
"""
import asyncio
import io
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto

from utils import bots, photos


BAD_FILE = TelegramBadRequest(method=SendPhoto(chat_id=1, photo="x"),
                              message='Telegram server says - Bad Request: Unknown "file"')


def make_bot(bot_id, *, owns=()):
    """Бот: file_id из `owns` знает, остальные отвергает 400, а байты принимает всегда."""
    bot = SimpleNamespace(id=bot_id, accepts=set(owns),
                          get_file=AsyncMock(), download=AsyncMock(),
                          send_photo=AsyncMock())

    async def send(chat_id, photo, **kwargs):
        if isinstance(photo, str) and photo not in bot.accepts:
            raise BAD_FILE
        return SimpleNamespace(chat_id=chat_id, photo=photo)

    async def get_file(file_id):
        if file_id not in bot.accepts:
            raise BAD_FILE
        return SimpleNamespace(file_id=file_id)

    bot.send_photo.side_effect = send
    bot.get_file.side_effect = get_file
    bot.download.return_value = io.BytesIO(b"photo-bytes")
    return bot


def fake_message(bot):
    """Сообщение, чей answer_photo отвергает чужой file_id как Telegram."""
    async def answer(photo, **kwargs):
        if isinstance(photo, str) and photo not in bot.accepts:
            raise BAD_FILE
        return SimpleNamespace(photo=photo)
    return SimpleNamespace(bot=bot, answer_photo=AsyncMock(side_effect=answer))


class PhotosCase(unittest.TestCase):
    def setUp(self):
        photos._bytes.clear()

    def run_async(self, coro):
        return asyncio.run(coro)

    # -- свой файл --------------------------------------------------------

    def test_own_file_id_goes_through_untouched(self):
        target = make_bot(111, owns=("own-file-id",))
        with mock.patch.object(bots, "all", return_value=(target,)):
            self.run_async(photos.send_photo(target, 5, "own-file-id", caption="c"))
        target.send_photo.assert_awaited_once_with(5, "own-file-id", caption="c")
        target.get_file.assert_not_awaited()

    # -- чужой файл -------------------------------------------------------

    def test_foreign_file_id_is_moved_from_the_other_bot(self):
        owner = make_bot(111, owns=("foreign",))
        listener = make_bot(222)
        with mock.patch.object(bots, "all", return_value=(owner, listener)):
            self.run_async(photos.send_photo(listener, 5, "foreign", caption="c"))
        sent = listener.send_photo.await_args.args
        self.assertEqual(sent[0], 5)
        self.assertEqual(sent[1].data, b"photo-bytes")
        owner.get_file.assert_awaited_once_with("foreign")

    def test_moved_photo_is_reused_instead_of_downloaded_again(self):
        owner = make_bot(111, owns=("foreign",))
        listener = make_bot(222)
        with mock.patch.object(bots, "all", return_value=(owner, listener)):
            self.run_async(photos.send_photo(listener, 5, "foreign"))
            self.run_async(photos.send_photo(listener, 6, "foreign"))
        # Второй показ идёт из кэша, без повторного get_file.
        self.assertEqual(owner.get_file.await_count, 1)
        # Первый вызов шлёт дважды (чужой file_id, затем перелитые байты),
        # второй -- один раз уже из кэша.
        self.assertEqual(listener.send_photo.await_count, 3)

    def test_cache_is_scoped_per_listening_bot(self):
        owner = make_bot(111, owns=("foreign",))
        first = make_bot(222)
        second = make_bot(333)
        with mock.patch.object(bots, "all", return_value=(owner, first, second)):
            self.run_async(photos.send_photo(first, 5, "foreign"))
            self.run_async(photos.send_photo(second, 5, "foreign"))
        # Кэш первого бота не должен выдаваться второму: у них свои файлы.
        self.assertEqual(owner.get_file.await_count, 2)

    def test_a_cache_that_exceeds_its_limit_shrinks(self):
        owner = make_bot(111, owns=tuple(f"file-{i}" for i in range(photos.LIMIT + 5)))
        listener = make_bot(222)
        with mock.patch.object(bots, "all", return_value=(owner, listener)):
            for index in range(photos.LIMIT + 5):
                self.run_async(photos.send_photo(listener, 5, f"file-{index}"))
        self.assertLessEqual(len(photos._bytes), photos.LIMIT)

    # -- никто не может перелить ------------------------------------------

    def test_no_bot_owns_the_file_so_the_error_propagates(self):
        first = make_bot(111)
        second = make_bot(222)
        with mock.patch.object(bots, "all", return_value=(first, second)):
            with self.assertRaises(TelegramBadRequest):
                self.run_async(photos.send_photo(second, 5, "lost"))
        # Вызывающий откатывается на текст, как и до правки.
        second.send_photo.assert_awaited_once()

    def test_a_blind_owner_is_skipped_for_the_next_one(self):
        blind = make_bot(111)
        owner = make_bot(333, owns=("foreign",))
        listener = make_bot(222)
        with mock.patch.object(bots, "all", return_value=(blind, owner, listener)):
            self.run_async(photos.send_photo(listener, 5, "foreign"))
        # Слепой бот пропущен, файл снят со следующего; слушатель шлёт дважды:
        # сперва чужой file_id, затем перелитые байты.
        self.assertEqual(listener.send_photo.await_count, 2)
        self.assertEqual(listener.send_photo.await_args.args[1].data, b"photo-bytes")

    # -- answer_photo -----------------------------------------------------

    def test_answer_photo_moves_too(self):
        owner = make_bot(111, owns=("foreign",))
        listener = make_bot(222)
        message = fake_message(listener)
        with mock.patch.object(bots, "all", return_value=(owner, listener)):
            self.run_async(photos.answer_photo(message, "foreign", caption="c"))
        # Первый вызов -- чужой file_id, он и падает; второй уже с байтами.
        self.assertEqual(message.answer_photo.await_count, 2)
        self.assertEqual(message.answer_photo.await_args.args[0].data, b"photo-bytes")

    def test_answer_photo_reuses_the_cache(self):
        owner = make_bot(111, owns=("foreign",))
        listener = make_bot(222)
        message = fake_message(listener)
        with mock.patch.object(bots, "all", return_value=(owner, listener)):
            self.run_async(photos.answer_photo(message, "foreign"))
            self.run_async(photos.answer_photo(message, "foreign"))
        # Первый вызов шлёт дважды (чужой file_id, затем перелитые байты),
        # второй -- один раз уже из кэша, и get_file больше не нужен.
        self.assertEqual(owner.get_file.await_count, 1)
        self.assertEqual(message.answer_photo.await_count, 3)


if __name__ == "__main__":
    unittest.main()

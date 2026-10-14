"""Кросс-бот пересылка: пара на разных ботах должна всё равно разговаривать.

copy_message работает только внутри одного бота, и до правки каждое сообщение
между пользователем первого бота и пользователем второго падало в 400 --
162 обрыва за шесть часов на одних только кросс-бот парах.

Bot API не умеет достать сообщение по идентификатору. Поэтому зовущий отдаёт
перелитию сам Message, а SourceBot ниже роняет AttributeError на любом
атрибуте, кроме get_file/download: так фальшивый API не пролезет ни в код,
ни в тест. Ровно так фальшивый `get_messages` в фикстуре и пропустил баг,
который в проде рвал каждый кросс-бот обмен в AttributeError.

Офлайн: сеть замокана, БД не трогается.
"""
import asyncio
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

from utils import repost


class SourceBot:
    """Бот-отправитель: умеет ровно то, что и настоящий Bot, -- отдать файл.

    Всё остальное падает с AttributeError, а не создаётся на лету, как это
    делает Mock. Перелить сообщение по его id негде -- и тест обязан об этом
    напомнить, если кто-то попытается снова.
    """

    id = 111

    def __init__(self):
        self.get_file = AsyncMock(return_value=SimpleNamespace(file_id="f"))
        self.download = AsyncMock(return_value=io.BytesIO(b"bytes"))

    def __getattr__(self, name):
        raise AttributeError(f"Bot has no attribute {name!r}")


def target_bot():
    """Бот-получатель: любой send_* отвечает отправленным сообщением."""
    ok = lambda: SimpleNamespace(message_id=1)
    return SimpleNamespace(
        id=222, copy_message=AsyncMock(return_value=ok()),
        copy_messages=AsyncMock(return_value=[ok()]),
        send_message=AsyncMock(return_value=ok()),
        send_photo=AsyncMock(return_value=ok()),
        send_voice=AsyncMock(return_value=ok()),
        send_sticker=AsyncMock(return_value=ok()),
        send_video=AsyncMock(return_value=ok()),
        send_video_note=AsyncMock(return_value=ok()),
        send_animation=AsyncMock(return_value=ok()),
        send_media_group=AsyncMock(return_value=[ok()]),
        # Нужны, чтобы пара на одном боте могла доказать: ничего не качали.
        get_file=AsyncMock(), download=AsyncMock())


def message(kind, bot, **fields):
    # entities/caption_entities есть у реального Message всегда, даже в None.
    base = dict(content_type=kind, text=None, entities=None,
                caption=None, caption_entities=None, bot=bot,
                chat=SimpleNamespace(id=1), message_id=9)
    base.update(fields)
    return SimpleNamespace(**base)


def photo(bot, sizes=("small", "big"), caption=None, **extra):
    return message("photo", bot, caption=caption,
                   photo=[SimpleNamespace(file_id=f"{name}") for name in sizes],
                   **extra)


def run(coro):
    return asyncio.run(coro)


class SameBotCase(unittest.TestCase):
    def test_pair_on_one_bot_keeps_using_copy_message(self):
        bot = target_bot()
        run(repost.copy(bot, 5, source=message("text", bot, text="привет")))
        bot.copy_message.assert_awaited_once_with(chat_id=5, from_chat_id=1, message_id=9)
        # Никакого скачивания: пара на одном боте не должна дорожать.
        bot.get_file.assert_not_awaited()
        bot.send_message.assert_not_awaited()

    def test_album_on_one_bot_keeps_using_copy_messages(self):
        bot = target_bot()
        sources = [message("photo", bot, photo=[SimpleNamespace(file_id="a")], message_id=9),
                   message("photo", bot, photo=[SimpleNamespace(file_id="b")], message_id=10)]
        run(repost.copy_many(bot, 5, sources=sources))
        bot.copy_messages.assert_awaited_once_with(chat_id=5, from_chat_id=1,
                                                   message_ids=[9, 10])
        bot.get_file.assert_not_awaited()
        bot.send_media_group.assert_not_awaited()

    def test_an_empty_album_is_noop_not_an_api_call(self):
        bot = target_bot()
        self.assertEqual(run(repost.copy_many(bot, 5, sources=[])), [])
        bot.copy_messages.assert_not_awaited()


class CrossBotTextCase(unittest.TestCase):
    def test_text_is_sent_by_the_receiving_bot(self):
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=message("text", source, text="привет", entities=["e"])))
        args = target.send_message.await_args
        self.assertEqual(args.args, (5, "привет"))
        self.assertEqual(args.kwargs["entities"], ["e"])
        # Чужой текст уходит без разбора разметки: у бота по умолчанию HTML.
        self.assertIsNone(args.kwargs["parse_mode"])

    def test_reply_reference_survives_the_move(self):
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=message("text", source, text="привет"),
                        reply_parameters="rp"))
        self.assertEqual(target.send_message.await_args.kwargs["reply_parameters"], "rp")

    def test_the_message_comes_from_the_update_and_is_never_fetched(self):
        # SourceBot роняет AttributeError на всём, кроме get_file/download:
        # если бы здесь снова появился запрос сообщения, тест упал бы сам.
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5, source=message("text", source, text="привет")))
        source.get_file.assert_not_awaited()
        target.copy_message.assert_not_awaited()
        target.send_message.assert_awaited_once()


class CrossBotMediaCase(unittest.TestCase):
    def test_photo_is_taken_at_its_largest_size(self):
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=photo(source, caption="подпись")))
        source.get_file.assert_awaited_once_with("big")
        sent = target.send_photo.await_args
        self.assertEqual(sent.args[0], 5)
        self.assertEqual(sent.args[1].data, b"bytes")
        self.assertEqual(sent.kwargs["caption"], "подпись")
        self.assertIsNone(sent.kwargs["parse_mode"])

    def test_voice_re_downloads_and_resends(self):
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=message("voice", source, voice=SimpleNamespace(file_id="v"))))
        target.send_voice.assert_awaited_once()
        self.assertEqual(target.send_voice.await_args.args[1].data, b"bytes")

    def test_sticker_carries_no_caption_at_all(self):
        # send_sticker не принимает caption -- передать его значило бы 400.
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=message("sticker", source, sticker=SimpleNamespace(file_id="s"))))
        target.send_sticker.assert_awaited_once()
        self.assertNotIn("caption", target.send_sticker.await_args.kwargs)

    def test_video_note_carries_no_caption_at_all(self):
        source = SourceBot()
        target = target_bot()
        run(repost.copy(target, 5,
                        source=message("video_note", source,
                                       video_note=SimpleNamespace(file_id="n"))))
        target.send_video_note.assert_awaited_once()
        self.assertNotIn("caption", target.send_video_note.await_args.kwargs)

    def test_unsupported_kind_sends_nothing_rather_than_crashing(self):
        source = SourceBot()
        target = target_bot()
        self.assertIsNone(run(repost.copy(target, 5, source=message("contact", source))))
        target.send_message.assert_not_awaited()
        target.send_photo.assert_not_awaited()

    def test_a_failed_download_does_not_send_a_half_message(self):
        source = SourceBot()
        target = target_bot()
        source.get_file.side_effect = TelegramBadRequest(
            method=GetFile(file_id="x"), message="Bad Request: bad file")
        self.assertIsNone(run(repost.copy(target, 5, source=photo(source))))
        target.send_photo.assert_not_awaited()


class CrossBotAlbumCase(unittest.TestCase):
    def test_album_keeps_its_grouping(self):
        source = SourceBot()
        target = target_bot()
        sources = [photo(source, sizes=("a",), caption="подпись", message_id=9),
                   photo(source, sizes=("b",), caption="подпись", message_id=10)]
        run(repost.copy_many(target, 5, sources=sources))
        target.send_media_group.assert_awaited_once()
        media = target.send_media_group.await_args.kwargs["media"]
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].caption, "подпись")

    def test_album_falls_back_to_single_sends_when_grouping_is_impossible(self):
        # Стикеры не умеют в media group -- каждый уходит отдельно.
        source = SourceBot()
        target = target_bot()
        sources = [message("sticker", source, sticker=SimpleNamespace(file_id="s1"),
                           message_id=9),
                   message("sticker", source, sticker=SimpleNamespace(file_id="s2"),
                           message_id=10)]
        run(repost.copy_many(target, 5, sources=sources))
        self.assertEqual(target.send_sticker.await_count, 2)
        target.send_media_group.assert_not_awaited()

    def test_one_undownloadable_part_aborts_the_whole_album(self):
        source = SourceBot()
        target = target_bot()
        source.get_file.side_effect = [SimpleNamespace(file_id="f"),
                                       TelegramBadRequest(method=GetFile(file_id="x"),
                                                          message="bad")]
        sources = [photo(source, sizes=("a",), message_id=9),
                   photo(source, sizes=("b",), message_id=10)]
        self.assertIsNone(run(repost.copy_many(target, 5, sources=sources)))
        target.send_media_group.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

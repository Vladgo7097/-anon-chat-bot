"""Кросс-бот пересылка: пара на разных ботах должна всё равно разговаривать.

copy_message работает только внутри одного бота, и до правки каждое сообщение
между пользователем первого бота и пользователем второго падало в 400 --
162 обрыва за шесть часов на одних только кросс-бот парах.

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


def source_bot():
    """Бот-отправитель: сообщения отдаёт сам тест, файлы -- всегда успешно."""
    bot = SimpleNamespace(id=111, get_messages=AsyncMock(),
                          get_file=AsyncMock(), download=AsyncMock())
    bot.get_file.return_value = SimpleNamespace(file_id="f")
    bot.download.return_value = io.BytesIO(b"bytes")
    return bot


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
        send_media_group=AsyncMock(return_value=[ok()]))


def message(kind, bot, **fields):
    # entities/caption_entities есть у реального Message всегда, даже в None.
    base = dict(content_type=kind, text=None, entities=None,
                caption=None, caption_entities=None, bot=bot)
    base.update(fields)
    return SimpleNamespace(**base)


def photo(bot, sizes=("small", "big"), caption=None):
    return message("photo", bot, caption=caption,
                   photo=[SimpleNamespace(file_id=f"{name}") for name in sizes])


def run(coro):
    return asyncio.run(coro)


class SameBotCase(unittest.TestCase):
    def test_pair_on_one_bot_keeps_using_copy_message(self):
        bot = target_bot()
        bot.get_messages = AsyncMock()
        run(repost.copy(bot, bot, 5, from_chat_id=1, message_id=9))
        bot.copy_message.assert_awaited_once_with(chat_id=5, from_chat_id=1, message_id=9)
        # Никакого скачивания: пара на одном боте не должна дорожать.
        bot.get_messages.assert_not_awaited()

    def test_album_on_one_bot_keeps_using_copy_messages(self):
        bot = target_bot()
        run(repost.copy_many(bot, bot, 5, from_chat_id=1, message_ids=[9, 10]))
        bot.copy_messages.assert_awaited_once_with(chat_id=5, from_chat_id=1,
                                                   message_ids=[9, 10])
        bot.send_media_group.assert_not_awaited()


class CrossBotTextCase(unittest.TestCase):
    def test_text_is_sent_by_the_receiving_bot(self):
        source = source_bot()
        source.get_messages.return_value = message("text", source,
                                                   text="привет", entities=["e"])
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9))
        args = target.send_message.await_args
        self.assertEqual(args.args, (5, "привет"))
        self.assertEqual(args.kwargs["entities"], ["e"])
        # Чужой текст уходит без разбора разметки: у бота по умолчанию HTML.
        self.assertIsNone(args.kwargs["parse_mode"])

    def test_reply_reference_survives_the_move(self):
        source = source_bot()
        source.get_messages.return_value = message("text", source, text="привет")
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9,
                        reply_parameters="rp"))
        self.assertEqual(target.send_message.await_args.kwargs["reply_parameters"], "rp")

    def test_nothing_is_sent_when_the_source_message_is_gone(self):
        source = source_bot()
        source.get_messages.return_value = None
        target = target_bot()
        self.assertIsNone(run(repost.copy(target, source, 5, from_chat_id=1, message_id=9)))
        target.send_message.assert_not_awaited()


class CrossBotMediaCase(unittest.TestCase):
    def test_photo_is_taken_at_its_largest_size(self):
        source = source_bot()
        source.get_messages.return_value = photo(source, caption="подпись")
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9))
        source.get_file.assert_awaited_once_with("big")
        sent = target.send_photo.await_args
        self.assertEqual(sent.args[0], 5)
        self.assertEqual(sent.args[1].data, b"bytes")
        self.assertEqual(sent.kwargs["caption"], "подпись")
        self.assertIsNone(sent.kwargs["parse_mode"])

    def test_voice_re_downloads_and_resends(self):
        source = source_bot()
        source.get_messages.return_value = message(
            "voice", source, voice=SimpleNamespace(file_id="v"))
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9))
        target.send_voice.assert_awaited_once()
        self.assertEqual(target.send_voice.await_args.args[1].data, b"bytes")

    def test_sticker_carries_no_caption_at_all(self):
        # send_sticker не принимает caption -- передать его значило бы 400.
        source = source_bot()
        source.get_messages.return_value = message(
            "sticker", source, sticker=SimpleNamespace(file_id="s"))
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9))
        target.send_sticker.assert_awaited_once()
        self.assertNotIn("caption", target.send_sticker.await_args.kwargs)

    def test_video_note_carries_no_caption_at_all(self):
        source = source_bot()
        source.get_messages.return_value = message(
            "video_note", source, video_note=SimpleNamespace(file_id="n"))
        target = target_bot()
        run(repost.copy(target, source, 5, from_chat_id=1, message_id=9))
        target.send_video_note.assert_awaited_once()
        self.assertNotIn("caption", target.send_video_note.await_args.kwargs)

    def test_unsupported_kind_sends_nothing_rather_than_crashing(self):
        source = source_bot()
        source.get_messages.return_value = message("contact", source)
        target = target_bot()
        self.assertIsNone(run(repost.copy(target, source, 5, from_chat_id=1, message_id=9)))
        target.send_message.assert_not_awaited()
        target.send_photo.assert_not_awaited()

    def test_a_failed_download_does_not_send_a_half_message(self):
        source = source_bot()
        source.get_messages.return_value = photo(source)
        source.get_file.side_effect = TelegramBadRequest(
            method=GetFile(file_id="x"), message="Bad Request: bad file")
        target = target_bot()
        self.assertIsNone(run(repost.copy(target, source, 5, from_chat_id=1, message_id=9)))
        target.send_photo.assert_not_awaited()


class CrossBotAlbumCase(unittest.TestCase):
    def test_album_keeps_its_grouping(self):
        source = source_bot()
        source.get_messages.return_value = [photo(source, sizes=("a",), caption="подпись"),
                                            photo(source, sizes=("b",), caption="подпись")]
        target = target_bot()
        run(repost.copy_many(target, source, 5, from_chat_id=1, message_ids=[9, 10]))
        target.send_media_group.assert_awaited_once()
        media = target.send_media_group.await_args.kwargs["media"]
        self.assertEqual(len(media), 2)
        self.assertEqual(media[0].caption, "подпись")

    def test_album_falls_back_to_single_sends_when_grouping_is_impossible(self):
        # Стикеры не умеют в media group -- каждый уходит отдельно.
        source = source_bot()
        source.get_messages.return_value = [
            message("sticker", source, sticker=SimpleNamespace(file_id="s1")),
            message("sticker", source, sticker=SimpleNamespace(file_id="s2"))]
        target = target_bot()
        run(repost.copy_many(target, source, 5, from_chat_id=1, message_ids=[9, 10]))
        self.assertEqual(target.send_sticker.await_count, 2)
        target.send_media_group.assert_not_awaited()

    def test_one_undownloadable_part_aborts_the_whole_album(self):
        source = source_bot()
        source.get_messages.return_value = [photo(source, sizes=("a",)),
                                            photo(source, sizes=("b",))]
        source.get_file.side_effect = [SimpleNamespace(file_id="f"),
                                       TelegramBadRequest(method=GetFile(file_id="x"),
                                                          message="bad")]
        target = target_bot()
        self.assertIsNone(run(repost.copy_many(target, source, 5,
                                               from_chat_id=1, message_ids=[9, 10])))
        target.send_media_group.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

"""file_id выдаётся ботом, который получил файл, и для второго бота это неизвестное имя.

База одна на двоих, поэтому карточка профиля и взаимное раскрытие рисуются
тем ботом, с которым говорит зритель. Если фото было загружено через другого
бота, Telegram отвечает 400, и раньше карточка молча сползала в текст.

Здесь картинка переливается: байты снимает бот, у которого файл есть, и отдаёт
их повторно через бота-слушателя -- Telegram мгновенно выдаёт новый file_id,
и чужая фотография становится своей. Результат кэшируется по паре
(бот-слушатель, исходный file_id), чтобы повторный показ шёл напрямую.
"""
import logging
from collections import OrderedDict

from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest

from utils import bots

logger = logging.getLogger(__name__)

# Профильное фото -- десятки-сотни килобайт, а показов на бота немного;
# 32 записи держат память в пределах мегабайтов и убирают повторный download.
LIMIT = 32
_bytes: OrderedDict[tuple[int, str], bytes] = OrderedDict()


async def answer_photo(message, file_id, **kwargs):
    """message.answer_photo, который не боится чужого file_id."""
    cached = _cached(message.bot.id, file_id)
    if cached is not None:
        return await message.answer_photo(cached, **kwargs)
    try:
        return await message.answer_photo(file_id, **kwargs)
    except TelegramBadRequest:
        moved = await _reupload(message.bot, file_id)
        if moved is None:
            raise
        return await message.answer_photo(moved, **kwargs)


async def send_photo(bot, chat_id, file_id, **kwargs):
    """bot.send_photo, который не боится чужого file_id."""
    cached = _cached(bot.id, file_id)
    if cached is not None:
        return await bot.send_photo(chat_id, cached, **kwargs)
    try:
        return await bot.send_photo(chat_id, file_id, **kwargs)
    except TelegramBadRequest:
        moved = await _reupload(bot, file_id, chat_id=chat_id)
        if moved is None:
            raise
        return await bot.send_photo(chat_id, moved, **kwargs)


def _cached(bot_id, file_id):
    key = (bot_id, file_id)
    data = _bytes.get(key)
    if data is not None:
        _bytes.move_to_end(key)
        return BufferedInputFile(data, filename="photo.jpg")
    return None


def _remember(bot_id, file_id, data):
    _bytes[(bot_id, file_id)] = data
    _bytes.move_to_end((bot_id, file_id))
    while len(_bytes) > LIMIT:
        _bytes.popitem(last=False)


async def _reupload(target, file_id, chat_id=None):
    """Найти в процессе бота, у которого файл есть, и перелить байты к слушателю.

    Владельца заранее никто не знает: пользователь мог загрузить фото через
    первого бота, а позже перейти на второго, и тогда колонка bot_id уже не
    соответствует выдавшему file_id. Поэтому перебираются все боты процесса --
    вернуть None означает, что перелить нечем, и вызывающий откатится на
    текст, как и раньше.
    """
    for owner in bots.all():
        if owner.id == target.id:
            continue
        try:
            file = await owner.get_file(file_id)
            data = await owner.download(file)
        except Exception:
            # Нет доступа к файлу у этого бота -- пробуем следующего.
            logger.debug("event=photo_move_miss owner_id=%s target_id=%s", owner.id, target.id)
            continue
        payload = data.read() if hasattr(data, "read") else bytes(data)
        _remember(target.id, file_id, payload)
        logger.info("event=photo_moved owner_id=%s target_id=%s chat_id=%s",
                    owner.id, target.id, chat_id)
        return BufferedInputFile(payload, filename="photo.jpg")
    logger.warning("event=photo_move_failed target_id=%s chat_id=%s", target.id, chat_id)
    return None

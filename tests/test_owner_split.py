"""Уведомления владельца разделены по ботам: кто запустил первый, кто второй.

Два бота держат разные аудитории из одной базы, и раньше сводка вперемешку
скрывала, какой именно растёт, а лимит был общий -- реклама, зашедшая во
второй бот, съедала квоту первого. Теперь лимит, дедуп и сводка считают
каждого бота отдельно, а «запустил бота» и «дошёл до меню» -- два разных
сигнала: приток и качество.

Офлайн: живой Postgres не нужен, сеть замокана.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from test_chat_duration import DatabaseCase
from models.operations import BotSetting, Notification
from models.user import User
from repositories.users import UserRepository
from services.onboarding import OnboardingService
from utils.time import utcnow


class OwnerSplitCase(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.OWNER_ID", 99)
        p.start()
        self.addCleanup(p.stop)

    async def notices(self, kind):
        async with self.factory() as s:
            return (await s.scalars(select(Notification).where(
                Notification.type == kind).order_by(Notification.id))).all()

    async def cap(self, value):
        async with self.factory.begin() as s:
            s.add(BotSetting(key="owner_notice_limit", value_json={"value": value}))


class SplitByBotTests(OwnerSplitCase):
    async def test_the_same_person_on_both_bots_reports_once_per_bot(self):
        """Человек, открывший оба бота, виден дважды -- по разу на бота.

        Бот в ключе дедупа -- не деталь: без него наблюдение «кто запускает
        первый, кто второй» свелось бы к одному счётчику на человека.
        Второй запуск отличается по заголовку, чтобы владелец не принял
        того же человека за нового."""
        await UserRepository().get_or_create(7001, "u", "N", bot_id=111)
        await UserRepository().get_or_create(7001, "u", "N", bot_id=222)
        rows = await self.notices("owner_new_user")
        self.assertEqual(len(rows), 2)
        self.assertEqual({n.bot_id for n in rows}, {111, 222})
        self.assertIn("<b>Запуск</b> · Бот", rows[0].text)
        self.assertIn("Запуск · другой бот", rows[1].text)

    async def test_ping_ponging_between_bots_is_not_a_flood(self):
        """Дедуп стоит на паре (человек, бот): перекидывание туда-сюда
        флудом не становится, сколько бы раз это ни повторилось."""
        for _ in range(3):
            await UserRepository().get_or_create(7002, "u", "N", bot_id=111)
            await UserRepository().get_or_create(7002, "u", "N", bot_id=222)
        self.assertEqual(len(await self.notices("owner_new_user")), 2)

    async def test_a_user_without_a_recorded_bot_is_attributed_once(self):
        """NULL bot_id -- не «первый бот»: это ещё не записанный бот."""
        await UserRepository().get_or_create(7003, "u", "N")
        rows = await self.notices("owner_new_user")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].bot_id)

    async def test_the_cap_is_per_bot_not_summed(self):
        """Реклама, зашедшая во второй бот, не съедает квоту первого."""
        await self.cap(2)
        for offset in range(5):
            await UserRepository().get_or_create(7100 + offset, "u", "N", bot_id=111)
        for offset in range(5):
            await UserRepository().get_or_create(7200 + offset, "u", "N", bot_id=222)
        rows = await self.notices("owner_new_user")
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for n in rows if n.bot_id == 111), 2)
        self.assertEqual(sum(1 for n in rows if n.bot_id == 222), 2)

    async def test_an_unrecorded_bot_is_its_own_bucket(self):
        """NULL -- отдельная корзина, а не подмесь к первому боту.

        Иначе «запустили 37» прятало бы часть потока в чужой счётчик."""
        await self.cap(1)
        await UserRepository().get_or_create(7300, "u", "N", bot_id=111)
        await UserRepository().get_or_create(7301, "u", "N")
        rows = await self.notices("owner_new_user")
        self.assertEqual(len(rows), 2)

    async def test_the_notice_carries_the_button_a_pair_of_actions(self):
        from keyboards.inline import owner_signup_kb
        await UserRepository().get_or_create(7400, "u", "N", bot_id=222)
        rows = await self.notices("owner_new_user")
        self.assertEqual(rows[0].callback_data, "admin:user_card:7400")
        self.assertEqual(rows[0].bot_id, 222)
        kb = owner_signup_kb(7400)
        self.assertEqual(len(kb.inline_keyboard), 2)
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "admin:user_card:7400")
        # «Написать» ведёт сразу в FSM-состояние AdminMessage: раньше это
        # стоило двух нажатий через карточку.
        self.assertEqual(kb.inline_keyboard[1][0].callback_data, "admin:message:7400")


class ActivationNoticeTests(OwnerSplitCase):
    async def _complete_onboarding(self, telegram_id, bot_id=None, payload=""):
        if bot_id is not None:
            await UserRepository().get_or_create(telegram_id, "u", "N", bot_id=bot_id)
        await OnboardingService().start(telegram_id, "u", "N", payload=payload)
        await OnboardingService().advance(telegram_id, "lang", "ru")
        await OnboardingService().advance(telegram_id, "gender", "female")
        await OnboardingService().advance(telegram_id, "age", 24)
        return await OnboardingService().advance(telegram_id, "accept")

    async def test_reaching_the_menu_is_a_second_signal_with_the_source(self):
        """Запуск показывает приток, «дошёл до меню» -- качество.

        К этому моменту заполнены пол, возраст и язык, а deep-link уже
        записал referrer_id и start_source: в момент запуска их ещё нет."""
        referrer, _ = await UserRepository().get_or_create(8100, "inviter", "R", bot_id=222)
        result = await self._complete_onboarding(
            8200, bot_id=222, payload=f"ref_{referrer.referral_code}")
        self.assertEqual(result.code, "MENU")
        # Реферал сам запустил бота -- его уведомление отдельное, здесь
        # интересен только приглашённый.
        launches = [n for n in await self.notices("owner_new_user")
                    if n.callback_data == "admin:user_card:8200"]
        self.assertEqual(len(launches), 1)
        activations = await self.notices("owner_activated")
        self.assertEqual(len(activations), 1)
        self.assertEqual(activations[0].bot_id, 222)
        self.assertIn("Пришёл: реферальная ссылка", activations[0].text)
        self.assertIn("привёл", activations[0].text)
        self.assertIn("#", activations[0].text)
        self.assertIn("24 · ж", activations[0].text)

    async def test_a_direct_visit_says_so_instead_of_showing_a_payload(self):
        await self._complete_onboarding(8300, bot_id=111)
        rows = await self.notices("owner_activated")
        self.assertEqual(len(rows), 1)
        self.assertIn("Пришёл: прямой заход", rows[0].text)

    async def test_an_updated_rules_version_does_not_notify_again(self):
        """Обновление правил возвращает в RULES каждого, кто уже был в меню.

        Без гейта дубль ушёл бы на весь базу."""
        await self._complete_onboarding(8400, bot_id=111)
        with mock.patch("config.CURRENT_RULES_VERSION", 999):
            await OnboardingService().advance(8400, "accept")
        rows = await self.notices("owner_activated")
        self.assertEqual(len(rows), 1)

    async def test_an_html_unsafe_name_is_escaped(self):
        """Имя в Telegram ничем не ограничено, а уведомление уходит с HTML.

        '<' и '&' в имени превращали бы сообщение владельца в мусор."""
        async with self.factory.begin() as s:
            s.add(User(telegram_id=8500, anon_id="#esc1", first_name="<b>Анна</b>",
                       gender="female", age=24, onboarding_completed=True,
                       onboarding_step="MENU", language="ru", bot_id=111,
                       rules_version=1, rules_accepted_at=utcnow()))
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 8500))
            await UserRepository().notify_owner_signup(s, user)
        rows = await self.notices("owner_activated")
        self.assertEqual(len(rows), 1)
        self.assertIn("&lt;b&gt;Анна&lt;/b&gt;", rows[0].text)
        self.assertNotIn("<b>Анна</b>", rows[0].text)


class DigestSplitTests(OwnerSplitCase):
    async def test_a_spike_is_reported_per_bot(self):
        """Сводка вперемешку скрывала, какой бот растёт."""
        await self.cap(1)
        for offset in range(4):
            await UserRepository().get_or_create(8600 + offset, "u", "N", bot_id=111)
        for offset in range(3):
            await UserRepository().get_or_create(8700 + offset, "u", "N", bot_id=222)
        # Уводим всё в прошедший, закрытый час.
        from datetime import timedelta
        now = utcnow()
        hour_end = now.replace(minute=0, second=0, microsecond=0)
        stamp = hour_end - timedelta(minutes=30)
        async with self.factory.begin() as s:
            await s.execute(User.__table__.update().where(
                User.telegram_id >= 8600).values(created_at=stamp))
            await s.execute(Notification.__table__.update().where(
                Notification.type == "owner_new_user").values(scheduled_at=stamp))
        from services.notifications import NotificationService
        await NotificationService().owner_digest(now)
        rows = await self.notices("owner_digest")
        self.assertEqual(len(rows), 2)
        by_bot = {n.bot_id: n.text for n in rows}
        self.assertIn("Запустили: 4", by_bot[111])
        self.assertIn("Запустили: 3", by_bot[222])
        self.assertIn("поштучно показано 1", by_bot[111])
        self.assertIn("поштучно показано 1", by_bot[222])


class BotLabelTests(unittest.TestCase):
    def test_the_primary_is_always_bot_one(self):
        """Порядок реестра, а не порядок токенов в .env, решает, кто первый."""
        from utils import bots
        registry = {8873771495: object(), 7828301604: object()}
        with mock.patch.object(bots, "_bot_by_id", registry):
            self.assertEqual(bots.label(8873771495), "Бот 1")
            self.assertEqual(bots.label(7828301604), "Бот 2")

    def test_an_unknown_bot_says_so_instead_of_guessing(self):
        from utils import bots
        self.assertEqual(bots.label(None), "Бот не определён")
        with mock.patch.object(bots, "_bot_by_id", {}):
            self.assertEqual(bots.label(123), "Бот 123")


if __name__ == "__main__":
    unittest.main()

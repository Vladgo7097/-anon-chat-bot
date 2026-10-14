"""Локальная админка: гейт по токену и выборки, на которых она стоит.

Эндпоинты зовутся напрямую, а не через HTTP-клиент, потому что живой цикл
и живая база в одном тесте -- это ровно то, чего здесь нет: база SQLite из
фабрики, а аутентификация проверяется отдельным набором на TestClient.
"""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import config  # noqa: E402
from models.base import Base  # noqa: E402
from models.chat import Chat  # noqa: E402
from models.operations import Notification  # noqa: E402
from models.user import User  # noqa: E402
import models.payment, models.report, models.ban  # noqa: E402,F401
from utils.time import utcnow  # noqa: E402
from web.admin import (_bots_split, _iso, _notice_subject, notifications,  # noqa: E402
                       overview, user_detail, users_list)

BOT1, BOT2 = 8873771495, 7828301604
OWNER = 777000111


class GateTests(unittest.TestCase):
    """Доступ держится на токене: без него панель не отвечает ничего.

    Запрос выполняется внутри with, а не после -- иначе патч откатывается
    раньше, чем клиент успеет обратиться к роутеру.
    """

    def _get(self, path, token, headers=None):
        from fastapi.testclient import TestClient
        from web.app import app
        with patch.object(config, "ADMIN_WEB_TOKEN", token):
            client = TestClient(app, raise_server_exceptions=False)
            return client.get(path, headers=headers or {})

    def test_without_token_the_whole_admin_is_off(self):
        self.assertEqual(self._get("/api/admin/overview", "").status_code, 404)

    def test_wrong_token_gets_401_not_content(self):
        r = self._get("/api/admin/overview", "right",
                      headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "BAD_TOKEN")

    def test_token_also_accepted_from_query_for_the_first_open(self):
        r = self._get("/api/admin/overview?key=wrong", "right")
        self.assertEqual(r.status_code, 401)

    def test_page_is_served_and_carries_no_token(self):
        r = self._get("/admin/", "right")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Админка", r.text)
        # Токен живёт в окружении сервера, а не в отданном HTML.
        self.assertNotIn("right", r.text)

    def test_admin_assets_do_not_get_the_sites_day_long_cache(self):
        from web.app import cache_control
        self.assertEqual(cache_control("/app/web/static/admin/app.js"), "no-cache")
        self.assertEqual(cache_control("/app/web/static/css/site.css"),
                         "public, max-age=86400")


class ParsingTests(unittest.TestCase):
    def test_subject_is_read_out_of_callback_data(self):
        self.assertEqual(_notice_subject(type("N", (), {"callback_data": "admin:user_card:42"})()), 42)
        self.assertIsNone(_notice_subject(type("N", (), {"callback_data": None})()))
        self.assertIsNone(_notice_subject(type("N", (), {"callback_data": "menu:back"})()))

    def test_naive_timestamps_are_read_as_utc(self):
        self.assertEqual(_iso(None), None)
        self.assertEqual(_iso(utcnow().replace(microsecond=0)).count("+00:00"), 1)


class AdminDbCase(unittest.IsolatedAsyncioTestCase):
    """SQLite-фабрика, подложенная и в web.admin, и в сервисный слой."""

    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "admin.db"))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in ("models.base", "web.admin", "services.admin",
                       "services.notifications", "services.settings",
                       "repositories.users"):
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        # Токенов в тестовом окружении нет, а подпись «Бот 1 / Бот 2» берётся
        # из них же: без этого и админка, и уведомления показывали бы id.
        for name, value in (("BOT_TOKEN", f"{BOT1}:1:x"),
                            ("BOT2_TOKEN", f"{BOT2}:1:y")):
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch("utils.bots._configured", None)
        p.start()
        self.addCleanup(p.stop)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        self.users = [
            User(telegram_id=101, anon_id="#AAA", username="alice", first_name="Alice",
                 bot_id=BOT1, gender="female", age=24, chats_count=3,
                 onboarding_completed=True, onboarding_step="MENU", rules_version=1,
                 premium_expires=utcnow() + timedelta(days=5), last_online=utcnow()),
            User(telegram_id=102, anon_id="#BBB", first_name="<Biba>",
                 bot_id=BOT2, is_banned=True, last_online=utcnow() - timedelta(hours=3)),
            User(telegram_id=103, anon_id="#CCC", username="zed",
                 bot_id=None, last_online=utcnow() - timedelta(days=2)),
        ]
        self.notices = [
            Notification(user_id=OWNER, type="owner_new_user",
                         dedupe_key="owner_new_user:101:1:777", text="Запуск",
                         callback_data="admin:user_card:101", bot_id=BOT1,
                         status="sent", sent_at=utcnow()),
            Notification(user_id=OWNER, type="owner_activated",
                         dedupe_key="owner_activated:102:2:777", text="Дошёл до меню",
                         callback_data="admin:user_card:102", bot_id=BOT2, status="queued"),
            Notification(user_id=OWNER, type="owner_digest",
                         dedupe_key="owner_digest:777:1:x", text="Сводка",
                         callback_data=None, bot_id=None, status="failed"),
            Notification(user_id=OWNER, type="admin_message",
                         dedupe_key="admin_message:1", text="Ответ админа",
                         callback_data=None, bot_id=BOT1, status="sent"),
        ]
        async with self.factory.begin() as s:
            s.add_all(self.users + self.notices)
            s.add(Chat(user1_id=101, user2_id=102, status="ended",
                       duration_seconds=125, end_reason="left",
                       started_at=utcnow() - timedelta(minutes=9)))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    # ---------- люди ----------

    async def test_everyone_is_listed_with_a_readable_bot(self):
        data = await users_list(q="", bot=None, banned=0, premium=0, only_new=0,
                                page=1, size=25, _=None)
        self.assertEqual(data["total"], 3)
        by_id = {u["telegram_id"]: u for u in data["items"]}
        self.assertEqual(by_id[101]["bot"], "Бот 1")
        self.assertEqual(by_id[102]["bot"], "Бот 2")
        self.assertEqual(by_id[103]["bot"], "Бот не определён")

    async def test_filters_narrow_the_list(self):
        async def only(**kw):
            base = dict(q="", bot=None, banned=0, premium=0, only_new=0,
                        page=1, size=25, _=None)
            base.update(kw)
            data = await users_list(**base)
            return [u["telegram_id"] for u in data["items"]]

        self.assertEqual(await only(bot=BOT1), [101])
        self.assertEqual(await only(bot=0), [103])
        self.assertEqual(await only(banned=1), [102])
        self.assertEqual(await only(premium=1), [101])
        self.assertEqual(await only(q="ali"), [101])
        self.assertEqual(await only(q="101"), [101])
        self.assertEqual(await only(q="#BBB"), [102])
        self.assertEqual(await only(q="zed"), [103])

    async def test_pagination_moves_the_window(self):
        first = await users_list(q="", bot=None, banned=0, premium=0, only_new=0,
                                 page=1, size=2, _=None)
        second = await users_list(q="", bot=None, banned=0, premium=0, only_new=0,
                                  page=2, size=2, _=None)
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(len(second["items"]), 1)
        # Страницы -- сдвиг, а не выдача одних и тех же людей заново.
        head = {u["telegram_id"] for u in first["items"]}
        tail = {u["telegram_id"] for u in second["items"]}
        self.assertFalse(head & tail)
        self.assertEqual(head | tail, {101, 102, 103})

    # ---------- уведомления владельцу ----------

    async def test_owner_kinds_are_separated_from_the_rest(self):
        owner = await notifications(kind="owner", bot=None, status="",
                                    page=1, size=30, _=None)
        self.assertEqual(owner["total"], 3)
        everything = await notifications(kind="all", bot=None, status="",
                                         page=1, size=30, _=None)
        self.assertEqual(everything["total"], 4)

    async def test_bot_filter_splits_the_stream(self):
        data = await notifications(kind="owner", bot=BOT2, status="",
                                   page=1, size=30, _=None)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["type"], "owner_activated")

    async def test_status_filter_and_subject_extraction(self):
        data = await notifications(kind="owner", bot=None, status="failed",
                                   page=1, size=30, _=None)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["telegram_id"], None)
        sent = await notifications(kind="owner", bot=None, status="sent",
                                   page=1, size=30, _=None)
        self.assertEqual(sent["items"][0]["telegram_id"], 101)
        self.assertEqual(sent["items"][0]["bot"], "Бот 1")

    async def test_counts_cover_all_three_owner_kinds(self):
        data = await notifications(kind="owner", bot=None, status="",
                                   page=1, size=30, _=None)
        self.assertEqual(data["counts"],
                         {"owner_new_user": 1, "owner_activated": 1, "owner_digest": 1})

    # ---------- карточка человека ----------

    async def test_card_pulls_profile_notices_and_chats(self):
        data = await user_detail(101, _=None)
        self.assertEqual(data["user"]["anon_id"], "#AAA")
        self.assertEqual(data["user"]["bot"], "Бот 1")
        self.assertEqual([n["type"] for n in data["notices"]], ["owner_new_user"])
        self.assertEqual(len(data["chats"]), 1)
        chat = data["chats"][0]
        self.assertEqual(chat["duration_seconds"], 125)
        self.assertEqual(chat["end_reason"], "left")
        self.assertEqual(chat["partner_anon_id"], "#BBB")
        self.assertEqual(chat["is_user1"], True)

    async def test_card_is_found_from_the_other_side_too(self):
        data = await user_detail(102, _=None)
        self.assertEqual(len(data["chats"]), 1)
        self.assertEqual(data["chats"][0]["partner_anon_id"], "#AAA")
        self.assertEqual(data["chats"][0]["is_user1"], False)

    # ---------- разбивка по ботам ----------

    async def test_split_counts_registrations_per_bot(self):
        rows = await _bots_split()
        by_id = {r["bot_id"]: r for r in rows}
        self.assertEqual(by_id[BOT1]["new_today"], 1)
        self.assertEqual(by_id[BOT1]["menu_today"], 1)
        self.assertEqual(by_id[BOT2]["new_today"], 1)
        self.assertEqual(by_id[BOT2]["menu_today"], 0)
        self.assertEqual(by_id[None]["new_today"], 1)

    async def test_split_still_answers_when_the_registry_is_empty(self):
        """Веб-процесс ботов не поднимает, но подписи обязаны совпасть."""
        from utils import bots
        with patch.object(bots, "_bot_by_id", {}), \
             patch.object(bots, "_configured", None), \
             patch.object(config, "BOT_TOKEN", f"{BOT1}:1:x"), \
             patch.object(config, "BOT2_TOKEN", f"{BOT2}:1:y"):
            rows = await _bots_split()
        labels = [r["label"] for r in rows]
        self.assertEqual(labels, ["Бот 1", "Бот 2", "Бот не определён"])

    # ---------- обзор ----------

    async def test_overview_feeds_the_recent_launch_panel(self):
        """Свежий приток вынесен на обзор: ради него туда и заходят."""
        with patch.object(config, "OWNER_ID", OWNER), \
             patch.object(config, "ADMIN_IDS", {OWNER}):
            data = await overview(1)
        self.assertIn("system", data)
        self.assertEqual(data["system"]["db"], True)
        # Свежее сверху: активация 102 новее, чем запуск 101.
        self.assertEqual([r["type"] for r in data["recent"]],
                         ["owner_activated", "owner_new_user"])
        self.assertEqual([r["telegram_id"] for r in data["recent"]], [102, 101])
        self.assertEqual(data["recent"][1]["bot"], "Бот 1")
        self.assertEqual({b["label"] for b in data["bots"]},
                         {"Бот 1", "Бот 2", "Бот не определён"})


if __name__ == "__main__":
    unittest.main()

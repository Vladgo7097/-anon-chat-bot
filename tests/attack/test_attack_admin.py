"""Red-team harness: admin surface — web token gate + Telegram admin panel.

Local only: SQLite factory + fastapi TestClient + a Bot whose session is an
AsyncMock. No network, no real Telegram, no touch of production files
outside tests/attack/.
"""

import asyncio
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiogram.types import Poll, PollOption, Update  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

import config  # noqa: E402
from models.base import Base  # noqa: E402
from models.operations import Broadcast  # noqa: E402
from models.report import Report  # noqa: E402
from models.user import User  # noqa: E402

import models.chat  # noqa: F401,E402
import models.payment  # noqa: F401,E402
import models.ban  # noqa: F401,E402
from services.result import ServiceResult  # noqa: E402
from utils.time import utcnow  # noqa: E402

from test_attack_fsm import (  # noqa: E402
    AttackCase,
    BOT,
    _Payload,
    _Sent,
    next_uid,
    next_update_id,
)
from types import SimpleNamespace  # noqa: E402


class _SentWithMeta(_Sent):
    """_Sent + chat/message_id: cb_send читает их у карточки прогресса."""

    def __init__(self, bot, payload):
        super().__init__(bot, payload)
        self.chat = SimpleNamespace(id=self.chat_id)
        self.message_id = 1

    async def answer(self, text, **kwargs):
        return _SentWithMeta(self.bot, _Payload(text, self.chat_id))


TOKEN = "S3CRET-TOKEN-VALUE"
H = {"Authorization": f"Bearer {TOKEN}"}
OWNER = 777000111  # web-админ: _admin_id() всегда пишет аудит от его имени
FOREIGN_ADMIN = 999000  # второй админ, чужие черновики рассылок
TELEGRAM_ADMIN = 777111222  # админ в телеграм-панели

# Модули, которым подкладывается SQLite-фабрика (рецепт tests/test_web_admin.py
# и tests/test_chat_duration.py). services.chats в списке нет: там нет
# атрибута async_session.
WEB_PATCH_MODULES = (
    "models.base",
    "web.admin",
    "services.admin",
    "services.notifications",
    "services.settings",
    "repositories.users",
    "repositories.chats",
    "services.matching",
    "services.reports",
    "services.ads",
    "services.reveal",
    "services.payments",
    "services.growth",
    "services.profile",
    "services.funnel_alert",
)


# --------------------------------------------------------------------------- #
# 1. Гейт web-админки: обход токена
# --------------------------------------------------------------------------- #
class GateMatrixTests(unittest.TestCase):
    """Гейт держит: обход токена не найден ни по одному вектору."""

    def setUp(self):
        # Обе «пропускающие» проверки уходят в overview, а это запрос к базе:
        # без фабрики падение было бы 500 из-за тестовой обвязки, а не из-за гейта.
        self.temp = TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "gate.db")
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [
            patch(module + ".async_session", self.factory)
            for module in (
                "models.base",
                "web.admin",
                "services.admin",
                "services.notifications",
                "services.settings",
                "repositories.users",
            )
        ]
        self.patches += [
            patch.object(config, "ADMIN_WEB_TOKEN", TOKEN),
            patch.object(config, "ADMIN_IDS", {OWNER}),
            patch.object(config, "OWNER_ID", OWNER),
            patch.object(config, "ADMIN_WEB_ID", 0),
        ]
        for p in self.patches:
            p.start()
        asyncio.run(self._create_schema())

    def tearDown(self):
        for p in self.patches:
            p.stop()
        asyncio.run(self.engine.dispose())
        self.temp.cleanup()

    async def _create_schema(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def _get(self, path, headers=None, token=TOKEN):
        from fastapi.testclient import TestClient
        from web.app import app

        with patch.object(config, "ADMIN_WEB_TOKEN", token):
            client = TestClient(app, raise_server_exceptions=False)
            return client.get(path, headers=headers or {})

    def test_without_token_401(self):
        self.assertEqual(self._get("/api/admin/overview").status_code, 401)

    def test_wrong_token_401(self):
        r = self._get("/api/admin/overview", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["detail"], "BAD_TOKEN")

    def test_non_ascii_header_is_401_not_500(self):
        """compare_digest по строкам кидал TypeError на не-ASCII; токен
        сверяется по байтам, поэтому мусор — честный 401, а не 500."""
        r = self._get(
            "/api/admin/overview", headers={"Authorization": b"Bearer \xff\xfe"}
        )
        self.assertEqual(r.status_code, 401)

    def test_trailing_space_in_token_401(self):
        r = self._get(
            "/api/admin/overview", headers={"Authorization": f"Bearer {TOKEN} "}
        )
        self.assertEqual(r.status_code, 401)

    def test_leading_space_in_query_token_401(self):
        self.assertEqual(
            self._get(f"/api/admin/overview?key=%20{TOKEN}").status_code, 401
        )

    def test_unicode_lookalike_token_401(self):
        # Латинская S + кириллические ЕТ визуально равны токену, байтами — нет.
        self.assertEqual(
            self._get("/api/admin/overview?key=S3CR\u0415\u0422").status_code, 401
        )

    def test_100k_token_is_401_and_does_not_crash(self):
        r = self._get(
            "/api/admin/overview", headers={"Authorization": "Bearer " + "a" * 100000}
        )
        self.assertEqual(r.status_code, 401)

    def test_duplicate_authorization_headers_first_wins(self):
        """Клиент с двумя заголовками не может протащить верный второй:
        берётся первый, неверный, и запрос умирает в 401."""
        r = self._get(
            "/api/admin/overview",
            headers=[
                ("Authorization", "Bearer wrong"),
                ("Authorization", f"Bearer {TOKEN}"),
            ],
        )
        self.assertEqual(r.status_code, 401)

    def test_lowercase_bearer_scheme_accepted(self):
        r = self._get(
            "/api/admin/overview", headers={"authorization": f"bearer {TOKEN}"}
        )
        self.assertEqual(r.status_code, 200)

    def test_query_key_wrong_401(self):
        self.assertEqual(self._get("/api/admin/overview?key=wrong").status_code, 401)

    def test_query_key_is_refused_even_when_correct(self):
        """Защита: _guard принимает токен только из Authorization: Bearer.

        Раньше ?key= тоже принимался (W1/L1) -- и полный токен попадал в
        access-лог nginx ($request), историю браузера и Referer. Ссылку для
        входа держат на #key=: фрагмент на сервер не уходит вовсе.
        """
        self.assertEqual(self._get(f"/api/admin/overview?key={TOKEN}").status_code, 401)

    def test_path_traversal_in_user_id_is_404(self):
        r = self._get("/api/admin/users/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(r.status_code, 404)

    def test_empty_configured_token_disables_api_but_page_still_serves(self):
        """Пустой ADMIN_WEB_TOKEN выключает API (404), но HTML-оболочка
        /admin/ отдаётся: статика без данных, токена в ней нет."""
        r_page = self._get("/admin/", token="")
        self.assertEqual(r_page.status_code, 200)
        self.assertNotIn(TOKEN, r_page.text)
        r_api = self._get("/api/admin/overview", token="")
        self.assertEqual(r_api.status_code, 404)


# --------------------------------------------------------------------------- #
# 2. Тайминг-канал на _guard: меряем саму функцию, без HTTP-шума
# --------------------------------------------------------------------------- #
class _FakeRequest:
    def __init__(self, auth=""):
        self.headers = {"authorization": auth} if auth else {}
        self.query_params = {}


class TokenTimingTests(unittest.IsolatedAsyncioTestCase):
    async def _min_batch(self, request, batch=200, rounds=15):
        """Минимум по раундам: срезает шум планировщика и GC."""
        from web.admin import _guard

        best = None
        for _ in range(rounds):
            start = time.perf_counter()
            for _ in range(batch):
                try:
                    await _guard(request)
                except Exception:  # noqa: BLE001 - ожидаемые 401 внутри
                    pass
            per_call = (time.perf_counter() - start) / batch
            best = per_call if best is None else min(best, per_call)
        return best

    async def test_no_per_byte_leak_and_no_usable_length_oracle(self):
        """compare_digest сравнивает байты: попутно проверяем оба канала.

        1. Равная длина, первый байт не совпал ~= равная длина, последний
           не совпал: внутри равной длины время не зависит от позиции
           расхождения — посимвольной утечки нет.
        2. Длина-минус-один ~= равная длина: оракул по длине токена на уровне
           самой функции не читается (разница в наносекундах — это O(1)
           проверка длины, а не сравнение содержимого).
        """
        same = _FakeRequest("Bearer " + "x" * len(TOKEN))
        short = _FakeRequest("Bearer " + "x" * (len(TOKEN) - 1))
        first = _FakeRequest("Bearer Q" + "x" * (len(TOKEN) - 1))
        huge = _FakeRequest("Bearer " + "x" * 100000)
        with patch.object(config, "ADMIN_WEB_TOKEN", TOKEN):
            t_same = await self._min_batch(same)
            t_short = await self._min_batch(short)
            t_first = await self._min_batch(first)
            t_huge = await self._min_batch(huge)
        # Всё умирает в 401, внутри функции — проверено циклом выше.
        self.assertLess(
            t_first,
            t_same * 2.0,
            f"per-byte leak: first-diff {t_first:.3e} vs {t_same:.3e}",
        )
        self.assertLess(
            t_short, t_same * 2.0, f"length oracle: len-1 {t_short:.3e} vs {t_same:.3e}"
        )
        self.assertLess(
            t_huge, 0.01, "100k-token input must stay bounded (no superlinear work)"
        )


# --------------------------------------------------------------------------- #
# Общий каркас web-тестов: SQLite + оба TestClient
# --------------------------------------------------------------------------- #
class WebDbBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "admin.db")
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in WEB_PATCH_MODULES:
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        # Redis в этом окружении недоступен; пути бана/резолва зовут
        # MatchingService.cancel / ChatService.end_chat — глушим явно,
        # чтобы падение теста не притворялось найденной уязвимостью.
        for target, value in (
            ("services.matching.MatchingService.cancel", AsyncMock()),
            (
                "services.chats.ChatService.end_chat",
                AsyncMock(return_value=ServiceResult(True, "ENDED")),
            ),
        ):
            p = patch(target, value)
            p.start()
            self.addCleanup(p.stop)
        for cm in (
            patch.object(config, "ADMIN_WEB_TOKEN", TOKEN),
            patch.object(config, "ADMIN_IDS", {OWNER}),
            patch.object(config, "OWNER_ID", OWNER),
            patch.object(config, "ADMIN_WEB_ID", 0),
        ):
            cm.start()
            self.addCleanup(cm.stop)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        async with self.factory.begin() as s:
            s.add_all(
                [
                    User(
                        telegram_id=101,
                        anon_id="#AAA",
                        first_name="Alice",
                        onboarding_completed=True,
                        last_online=utcnow(),
                    ),
                    User(
                        telegram_id=102,
                        anon_id="#BBB",
                        first_name="Biba",
                        is_banned=True,
                    ),
                    Report(
                        id=1,
                        reporter_id=102,
                        reported_id=101,
                        reason="spam",
                        status="pending",
                        message_content="bad",
                    ),
                    Broadcast(
                        id=1,
                        admin_id=FOREIGN_ADMIN,
                        segment="all",
                        content="чужая рассылка",
                        status="draft",
                    ),
                ]
            )

        from fastapi.testclient import TestClient
        from web.app import app

        self.strict = TestClient(app, raise_server_exceptions=True)
        self.soft = TestClient(app, raise_server_exceptions=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    def _call(self, client, method, path, payload=None):
        if method == "GET":
            return client.get(path, headers=H)
        return client.post(path, json=payload, headers=H)

    def _assert_status(self, method, path, payload, expected):
        """Защита: мусорный параметр при валидном токене отбивается 4xx."""
        response = self._call(self.soft, method, path, payload)
        self.assertEqual(
            response.status_code,
            expected,
            f"{method} {path} -> {response.status_code}, ждали {expected}",
        )


# --------------------------------------------------------------------------- #
# 3. Аутентифицированные 500: необработанные исключения на инъекции
# --------------------------------------------------------------------------- #
class Authenticated500Tests(WebDbBase):
    """Защита: валидный токен + мусорный параметр -- ни один кейс не роняет
    сервис в 500: разряд ограничен на входе, конверсии погашены 4xx."""

    def test_users_page_overflow_is_capped_by_query_bound(self):
        """Защита: page ограничен le=10**6 -- offset() не получает число
        шире INTEGER и SQLite не получает OverflowError."""
        self._assert_status(
            "GET",
            "/api/admin/users?page=" + "9" * 18 + "&size=200",
            None,
            422,
        )

    def test_users_search_q_hundred_digits_stays_a_text_search(self):
        """Защита: 100 цифр не конвертируются в id (лимит 18 знаков) --
        уходят в LIKE как текст, запрос отвечает 200."""
        self._assert_status("GET", "/api/admin/users?q=" + "1" * 100, None, 200)

    def test_users_search_q_5000_digits_never_reaches_int_conversion(self):
        """Защита: длинная цифровая строка отсекается по длине ДО int() --
        лимит CPython на конверсию (ValueError) недостижим."""
        self._assert_status("GET", "/api/admin/users?q=" + "1" * 5000, None, 200)

    def test_user_detail_overflow_is_404(self):
        """Защита: telegram_id вне int64 не доходит до WHERE -- чистый 404."""
        self._assert_status("GET", "/api/admin/users/" + "9" * 21, None, 404)

    def test_reports_offset_overflow_is_capped_by_query_bound(self):
        """Защита: offset ограничен le=10**9 -- .offset() не получает
        число шире INTEGER."""
        self._assert_status(
            "GET", "/api/admin/reports?offset=" + "9" * 21, None, 422
        )

    def test_report_resolve_overflow_is_a_clean_400(self):
        """Защита: report_id вне int64 отсекается до s.get() -- сервис
        отвечает NOT_FOUND, а OverflowError не рвёт транзакцию."""
        self._assert_status(
            "POST",
            "/api/admin/reports/" + "9" * 21 + "/resolve",
            {"action": "dismiss"},
            400,
        )

    def test_settings_value_abc_is_a_clean_400(self):
        """Защита: не-число не конвертируется int() -- сервис отвечает INVALID."""
        self._assert_status(
            "POST",
            "/api/admin/settings",
            {"key": "search_timeout", "value": "abc"},
            400,
        )

    def test_settings_value_object_is_a_clean_400(self):
        """Защита: объект вместо числа не конвертируется -- INVALID, не TypeError."""
        self._assert_status(
            "POST",
            "/api/admin/settings",
            {"key": "search_timeout", "value": {"a": 1}},
            400,
        )

    def test_settings_value_5000_digits_is_a_clean_400(self):
        """Защита: длиннее 18 цифр не доходит до int() -- INVALID."""
        self._assert_status(
            "POST",
            "/api/admin/settings",
            {"key": "search_timeout", "value": "9" * 5000},
            400,
        )

    def test_settings_key_list_is_a_clean_400(self):
        """Защита: не-str ключ гасится isinstance-проверкой до `key not in limits`."""
        self._assert_status(
            "POST", "/api/admin/settings", {"key": ["a"], "value": 60}, 400
        )

    def test_ban_hours_abc_is_a_clean_400(self):
        """Защита: часы блокировки конвертируются через _as_int с границей
        87600 -- мусор получает 400, а не ValueError в 500."""
        self._assert_status(
            "POST",
            "/api/admin/users/101/ban",
            {"hours": "abc", "reason": "x"},
            400,
        )

    def test_premium_days_abc_is_a_clean_400(self):
        """Защита: дни премиума конвертируются через _as_int с границей
        3650 -- мусор получает 400, а не ValueError в 500."""
        self._assert_status(
            "POST", "/api/admin/users/101/premium", {"days": "abc"}, 400
        )

    def test_broadcast_content_int_is_a_clean_400(self):
        """Защита: не-str content гасится до len() -- INVALID, не TypeError."""
        self._assert_status(
            "POST",
            "/api/admin/broadcasts",
            {"content": 123, "segment": "all"},
            400,
        )

    def test_benign_bad_input_gets_4xx_not_500(self):
        """Контроль: заведомо кривые, но «обычные» значения отбиваются 4xx,
        500 живут только в перечисленных выше узлах."""
        cases = [
            ("GET", "/api/admin/users?size=0", None, 422),
            ("GET", "/api/admin/users?size=-5", None, 422),
            ("GET", "/api/admin/overview?days=0", None, 422),
            ("POST", "/api/admin/settings", {"key": "no_such_key", "value": 1}, 400),
            (
                "POST",
                "/api/admin/broadcasts",
                {"content": "hi", "segment": "../all"},
                400,
            ),
            ("POST", "/api/admin/users/101/premium", {"days": 10**9}, 400),
            ("POST", "/api/admin/users/101/ban", {"hours": 100000, "reason": "x"}, 400),
            ("POST", "/api/admin/users/101/message", {"text": "a" * 4001}, 400),
        ]
        for method, path, payload, expected in cases:
            r = self._call(self.soft, method, path, payload)
            self.assertEqual(
                r.status_code,
                expected,
                f"{method} {path} -> {r.status_code}, ждали {expected}",
            )


# --------------------------------------------------------------------------- #
# 4. Рассылки: черновик без confirm и чужой job_id
# --------------------------------------------------------------------------- #
class BroadcastOwnershipTests(WebDbBase):
    async def _status(self, job_id):
        async with self.factory() as s:
            row = await s.get(Broadcast, job_id)
        return row.status

    async def test_draft_is_not_sent_without_confirm(self):
        """POST /broadcasts только создаёт черновик: воркер берёт exclusively
        status=="queued" (services/notifications.py:438), поэтому без
        отдельного confirm волна не уходит."""
        r = self._call(
            self.soft,
            "POST",
            "/api/admin/broadcasts",
            {"content": "привет", "segment": "all"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(await self._status(r.json()["id"]), "draft")

    async def test_foreign_draft_confirm_is_rejected(self):
        """Чужой черновик (admin_id=FOREIGN_ADMIN) не подтверждается от имени
        веб-админа: services/admin.py:474."""
        r = self._call(self.soft, "POST", "/api/admin/broadcasts/1/confirm")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "ALREADY_STARTED")
        self.assertEqual(await self._status(1), "draft")

    async def test_foreign_draft_cancel_is_rejected(self):
        """services/admin.py:537 — та же проверка владельца на отмене."""
        r = self._call(self.soft, "POST", "/api/admin/broadcasts/1/cancel")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"], "NOT_FOUND")
        self.assertEqual(await self._status(1), "draft")

    async def test_own_draft_confirm_then_cancel(self):
        created = self._call(
            self.soft,
            "POST",
            "/api/admin/broadcasts",
            {"content": "своя", "segment": "all"},
        )
        job_id = created.json()["id"]
        ok = self._call(self.soft, "POST", f"/api/admin/broadcasts/{job_id}/confirm")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(await self._status(job_id), "queued")
        cancelled = self._call(
            self.soft, "POST", f"/api/admin/broadcasts/{job_id}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(await self._status(job_id), "cancelled")

    async def test_list_returns_foreign_broadcast_content_deliberate(self):
        """Сознательное поведение (комментарий web/admin.py:402): GET
        /broadcasts отдаёт чужие рассылки целиком, включая текст, — owned=false
        только гасит кнопки."""
        r = self._call(self.soft, "GET", "/api/admin/broadcasts")
        self.assertEqual(r.status_code, 200)
        items = {item["id"]: item for item in r.json()["items"]}
        self.assertEqual(items[1]["content"], "чужая рассылка")
        self.assertEqual(items[1]["admin_id"], FOREIGN_ADMIN)
        self.assertFalse(items[1]["owned"])


# --------------------------------------------------------------------------- #
# 5. Телеграм-панель: спуф админ-команд и callback'ов
# --------------------------------------------------------------------------- #
class TelegramSpoofTests(AttackCase):
    """Не-админ шлёт админ-команды: каждая должна упереться в AdminOnly
    (handlers/admin.py:41-52) — authorize() поднимает PermissionError,
    и обработчик не вызывается."""

    def _patch_admin_ids(self, ids):
        cm = patch.object(config, "ADMIN_IDS", set(ids))
        cm.start()
        self.addCleanup(cm.stop)

    def _record(self, *args, **kwargs):
        """Каркасный _Sent не несёт chat/message_id, а cb_send читает их у
        карточки прогресса ещё до вызова attach_progress."""
        payload = args[1]
        self.sent.append(payload)
        stub = _SentWithMeta(BOT, payload)
        return stub

    def _assert_denied(self):
        self.assertTrue(
            any("Нет доступа." in (text or "") for text in self.texts()),
            f"отказ AdminOnly не пришёл: {self.texts()!r}",
        )

    async def test_non_admin_callback_banf_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.resolve",
            AsyncMock(return_value=ServiceResult(True, "RESOLVED")),
        ) as resolve:
            await self.tap("admin:banf:1", uid)
        resolve.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_non_admin_callback_mute_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.resolve",
            AsyncMock(return_value=ServiceResult(True, "RESOLVED")),
        ) as resolve:
            await self.tap("admin:mute1:1", uid)
        resolve.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_non_admin_callback_send_job_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch(
            "services.admin.AdminService.confirm_broadcast", AsyncMock()
        ) as confirm:
            await self.tap("admin:send:1", uid)
        confirm.assert_not_awaited()
        self._assert_denied()

    async def test_non_admin_callback_grant_is_denied(self):
        uid = next_uid()
        await self.register(uid)
        with patch("services.admin.AdminService.grant", AsyncMock()) as grant:
            await self.tap("admin:dogrant:101:30", uid)
        grant.assert_not_awaited()
        self._assert_denied()

    async def test_non_admin_commands_are_denied(self):
        """/ban_user, /grant, /admin — и от зарегистрированного (AdminOnly),
        и от незарегистрированного (гейт онбординга раньше)."""
        registered = next_uid()
        raw = next_uid()
        await self.register(registered)
        with (
            patch("services.admin.AdminService.find_user", AsyncMock()) as find,
            patch("services.admin.AdminService.ban", AsyncMock()) as ban,
        ):
            await self.send("/ban_user 101 24 тест", raw)
            await self.send("/ban_user 101 24 тест", registered)
            await self.send("/grant 101 30 тест", registered)
            await self.send("/admin", registered)
        find.assert_not_awaited()
        ban.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_forced_admin_fsm_state_is_denied(self):
        """Состояние админа навязано напрямую в FSM-хранилище — AdminOnly
        всё равно перехватывает: middleware роутера стоит перед любым
        StateFilter-обработчиком."""
        uid = next_uid()
        await self.register(uid)
        await self.set_state(uid, "AdminMessage:CONTENT", target_id=101)
        with patch(
            "services.admin.AdminService.send_message_to_user", AsyncMock()
        ) as send:
            await self.send("принудительно записанное сообщение", uid)
        send.assert_not_awaited()
        self._assert_denied()
        self.assertEqual(self.bot_errors, [])

    async def test_admin_send_job_control_passes(self):
        """Контроль: реальный админ подтверждает свою черновую рассылку
        полным прод-путём (AdminOnly -> confirm_broadcast -> карточка)."""
        self._patch_admin_ids({TELEGRAM_ADMIN})
        async with self.factory.begin() as s:
            job = Broadcast(
                admin_id=TELEGRAM_ADMIN,
                segment="all",
                content="контрольная рассылка",
                status="draft",
            )
            s.add(job)
            await s.flush()
            job_id = job.id
        # attach_progress глушим: каркасный _Sent не несёт message_id.
        # Сам confirm_broadcast идёт по-настоящему, через SQLite.
        with patch(
            "services.admin.AdminService.attach_progress",
            AsyncMock(return_value=ServiceResult(True, "ATTACHED")),
        ):
            await self.tap(f"admin:send:{job_id}", TELEGRAM_ADMIN)
        async with self.factory() as s:
            row = await s.get(Broadcast, job_id)
        self.assertEqual(row.status, "queued")
        self.assertTrue(
            any(f"Рассылка #{job_id}" in (text or "") for text in self.texts())
        )
        self.assertEqual(self.bot_errors, [])

    async def test_foreign_poll_update_is_harmless(self):
        """@admin_router.poll() (handlers/admin.py:686) не имеет AdminOnly
        по замыслу: любой получатель опроса шлёт свои голоса. Чужой poll_id —
        no-op в update_poll (services/notifications.py:163-169)."""
        await self.dp.feed_update(
            BOT,
            Update(
                update_id=next_update_id(),
                poll=Poll(
                    id="foreign-poll-id",
                    question="чужой опрос",
                    options=[PollOption(text="да", voter_count=7, persistent_id="p1")],
                    total_voter_count=7,
                    is_closed=True,
                    is_anonymous=True,
                    allows_revoting=False,
                    members_only=False,
                    type="regular",
                    allows_multiple_answers=False,
                ),
            ),
        )
        self.assertEqual(self.bot_errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

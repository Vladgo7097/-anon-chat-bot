"""Red-team battery against the payment contour.

Everything is local: a file-backed SQLite database in a temp dir and a Bot
whose session is replaced by a recorder, so no packet ever reaches the
Telegram API or the production host. Updates are fed through the real
Dispatcher (production middleware chain + FSM + routers) so every probe goes
through exactly the path a user with a phone goes through.
"""

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User as TgUser,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from handlers import load_routers
from models.base import Base
from models.payment import Entitlement, Payment, PremiumSubscription, Product
from models.user import User as UserModel
from services.payments import PaymentService, seed_products
from utils.middleware import AccessMiddleware, AntiFloodMiddleware
from utils.time import utcnow

import models.ban  # noqa: F401
import models.chat  # noqa: F401
import models.operations  # noqa: F401
import models.report  # noqa: F401

ATTACKER = 1001
VICTIM = 1002

_BOT = Bot(token="123456:TEST_TOKEN_FOR_OFFLINE_DISPATCH")
_BOT._me = TgUser(
    id=1, is_bot=True, first_name="AttackBot", username="offline_attack_bot"
)

_next_update = [900000]


def _api_method(obj):
    for value in obj:
        if hasattr(value, "__api_method__"):
            return value
    return None


class PaymentAttackCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(Path(self.temp.name) / "attack.db")
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        for module in (
            "models.base",
            "repositories.chats",
            "repositories.users",
            "services.notifications",
            "services.growth",
            "services.payments",
            "services.profile",
            "services.matching",
            "services.settings",
            "services.reveal",
            "services.reports",
            "services.admin",
            "services.ads",
            "services.funnel_alert",
            "services.relay",
        ):
            module_obj = __import__(module, fromlist=["async_session"])
            if not hasattr(module_obj, "async_session"):
                continue
            p = patch(module + ".async_session", self.factory)
            p.start()
            self.addCleanup(p.stop)
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.drop_all)
            await c.run_sync(Base.metadata.create_all)
        async with self.factory.begin() as s:
            for uid in (ATTACKER, VICTIM):
                s.add(
                    UserModel(
                        telegram_id=uid,
                        anon_id=f"#atk{uid}",
                        gender="male",
                        age=23,
                        language="ru",
                        onboarding_completed=True,
                        onboarding_step="MENU",
                        rules_version=1,
                        rules_accepted_at=utcnow(),
                    )
                )
        await seed_products()

        routers = load_routers()
        for router in routers:
            router._parent_router = None
        self.dp = Dispatcher()
        self.dp.include_routers(*routers)
        self.dp.message.outer_middleware(AccessMiddleware())
        self.dp.callback_query.outer_middleware(AccessMiddleware())
        self.dp.message.middleware(AntiFloodMiddleware())

        self.api = []
        self.request = AsyncMock(side_effect=self._record)
        request_patcher = patch.object(_BOT.session, "make_request", self.request)
        request_patcher.start()
        self.addCleanup(request_patcher.stop)
        for target in (
            patch("services.chat_manager.set_online", AsyncMock()),
            patch("services.chat_manager.get_partner", AsyncMock(return_value=None)),
            patch("services.chat_manager.set_instant_search", AsyncMock()),
            patch("utils.middleware.check_flood", AsyncMock(return_value=False)),
            patch("services.growth.StreakService.checkin", AsyncMock()),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.service = PaymentService()

    async def asyncTearDown(self):
        await self.dp.storage.close()
        for router in load_routers():
            router._parent_router = None
        await self.engine.dispose()
        self.temp.cleanup()

    # -- raw Bot API recorder -------------------------------------------------
    def _record(self, *args, **kwargs):
        # make_request(bot, method, timeout=...) -- args[1] is the method.
        method = _api_method(args) or _api_method(kwargs.values())
        if method is None:
            return True
        self.api.append(method)
        name = method.__api_method__
        if name in {"sendMessage", "sendInvoice", "editMessageText"}:
            chat_id = getattr(method, "chat_id", None)
            if chat_id is None:
                chat_id = getattr(method, "message", None)
                chat_id = getattr(chat_id, "chat_id", None)
            return Message(
                message_id=len(self.api),
                date=datetime.now(timezone.utc),
                chat=Chat(id=chat_id or ATTACKER, type="private"),
                from_user=_BOT._me,
                text=getattr(method, "text", "") or "stub",
            )
        return True

    def _sent(self, name):
        return [m for m in self.api if m.__api_method__ == name]

    def _answers(self):
        return self._sent("answerPreCheckoutQuery")

    def _texts(self):
        return [m.text for m in self._sent("sendMessage")]

    # -- update builders ------------------------------------------------------
    def _uid(self):
        _next_update[0] += 1
        return _next_update[0]

    async def _feed(self, data):
        update = Update.model_validate(data)
        await self.dp.feed_update(_BOT, update)
        return update

    async def _tap(self, uid, callback_data):
        upd = self._uid()
        return await self._feed(
            {
                "update_id": upd,
                "callback_query": {
                    "id": str(upd),
                    "chat_instance": "offline",
                    "data": callback_data,
                    "from_user": {"id": uid, "is_bot": False, "first_name": "Attacker"},
                    "message": {
                        "message_id": upd,
                        "date": datetime.now(timezone.utc),
                        "chat": {"id": uid, "type": "private"},
                        "from_user": {
                            "id": 1,
                            "is_bot": True,
                            "first_name": "AttackBot",
                        },
                        "text": "stub",
                    },
                },
            }
        )

    async def _precheckout(self, uid, payload, amount, currency="XTR", query_id="q-1"):
        before = len(self._answers())
        await self._feed(
            {
                "update_id": self._uid(),
                "pre_checkout_query": {
                    "id": query_id,
                    "currency": currency,
                    "total_amount": amount,
                    "invoice_payload": payload,
                    "from_user": {"id": uid, "is_bot": False, "first_name": "Attacker"},
                },
            }
        )
        answered = self._answers()[before:]
        return answered[-1] if answered else None

    async def _pay(self, uid, payload, amount, currency="XTR", charge="charge-1"):
        before = len(self._texts())
        await self._feed(
            {
                "update_id": self._uid(),
                "message": {
                    "message_id": self._uid(),
                    "date": datetime.now(timezone.utc),
                    "chat": {"id": uid, "type": "private"},
                    "from_user": {"id": uid, "is_bot": False, "first_name": "Attacker"},
                    "successful_payment": {
                        "currency": currency,
                        "total_amount": amount,
                        "invoice_payload": payload,
                        "telegram_payment_charge_id": charge,
                        "provider_payment_charge_id": "prov-1",
                    },
                },
            }
        )
        texts = self._texts()
        return texts[-1] if len(texts) > before else None

    # -- setup helpers --------------------------------------------------------
    async def _buy(self, uid, code, session=None):
        """Tap the inline buy button, exactly like a user, and return the
        invoice Telegram would have offered (payload + price from the DB row)."""
        data = f"v1:buy:{code}" + (f":{session}" if session else "")
        await self._tap(uid, data)
        async with self.factory() as s:
            row = await s.scalar(
                select(Payment)
                .where(Payment.user_id == uid, Payment.product == code)
                .order_by(Payment.id.desc())
            )
        return SimpleNamespace(
            payment=row, payload=row.payload, amount=row.stars_amount, payment_id=row.id
        )

    async def _count(self, model):
        async with self.factory() as s:
            return await s.scalar(select(func.count(model.id)))

    async def _user(self, uid):
        async with self.factory() as s:
            return await s.scalar(select(UserModel).where(UserModel.telegram_id == uid))

    async def _payment(self, payment_id):
        async with self.factory() as s:
            return await s.get(Payment, payment_id)

    async def _product(self, code, **fields):
        async with self.factory.begin() as s:
            product = await s.get(Product, code)
            for key, value in fields.items():
                setattr(product, key, value)

    def _freeze(self, at):
        """Move the payment clock forward, the way waiting does."""
        return patch("services.payments.utcnow", return_value=at)


class ReplayTests(PaymentAttackCase):
    async def test_replay_of_one_successful_payment_credits_exactly_once(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )

        first = await self._pay(ATTACKER, inv.payload, inv.amount, charge="charge-A")
        self.assertIn("Оплата получена", first)

        # Exact replay of the same update (Telegram redelivery / user replay).
        second = await self._pay(ATTACKER, inv.payload, inv.amount, charge="charge-A")
        self.assertIn("уже учтён", second)

        # Replay with a fresh charge id -- a forged second credit attempt.
        third = await self._pay(ATTACKER, inv.payload, inv.amount, charge="charge-B")
        self.assertIn("уже учтён", third)

        user = await self._user(ATTACKER)
        self.assertEqual(await self._count(PremiumSubscription), 1)
        self.assertEqual(user.total_stars_spent, inv.amount)
        # 30 days, not 60: the replays must not stack a second month.
        self.assertLessEqual(
            user.premium_expires - utcnow(), timedelta(days=30, minutes=1)
        )

    async def test_pre_checkout_query_after_payment_is_refused(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )
        await self._pay(ATTACKER, inv.payload, inv.amount, charge="charge-A")
        replay = await self._precheckout(
            ATTACKER, inv.payload, inv.amount, query_id="q2"
        )
        self.assertIsNotNone(replay)
        self.assertFalse(replay.ok)

    async def test_refund_replay_cannot_double_revoke(self):
        inv = await self._buy(ATTACKER, "instant_search")
        await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        await self._pay(ATTACKER, inv.payload, inv.amount, charge="refund-1")
        ok = await self.service.refunded(
            ATTACKER, inv.payload, inv.amount, "XTR", "refund-1"
        )
        self.assertTrue(ok.ok)
        again = await self.service.refunded(
            ATTACKER, inv.payload, inv.amount, "XTR", "refund-1"
        )
        self.assertEqual(again.code, "ALREADY_REFUNDED")
        user = await self._user(ATTACKER)
        self.assertEqual(user.total_stars_spent, 0)


class ForgeryTests(PaymentAttackCase):
    async def test_paying_with_a_foreign_payload_credits_nobody(self):
        victim_inv = await self._buy(VICTIM, "premium_1m")
        await self._precheckout(
            VICTIM, victim_inv.payload, victim_inv.amount, query_id="q1"
        )

        reply = await self._pay(
            ATTACKER, victim_inv.payload, victim_inv.amount, charge="atk-1"
        )
        self.assertIn("требует проверки", reply)

        self.assertEqual(await self._count(PremiumSubscription), 0)
        self.assertEqual((await self._user(ATTACKER)).total_stars_spent or 0, 0)
        # Victim's invoice is left untouched, not consumed or credited.
        self.assertEqual(
            (await self._payment(victim_inv.payment_id)).status, "precheckout_ok"
        )

        # Same for the pre-checkout gate.
        answer = await self._precheckout(
            ATTACKER, victim_inv.payload, victim_inv.amount, query_id="q9"
        )
        self.assertIsNotNone(answer)
        self.assertFalse(answer.ok)
        self.assertEqual(await self._count(PremiumSubscription), 0)

    async def test_forged_amount_currency_and_payload_are_rejected(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )

        cases = [
            ("zero amount", dict(amount=0, charge="f-zero")),
            ("negative amount", dict(amount=-inv.amount, charge="f-neg")),
            ("underpay", dict(amount=inv.amount - 1, charge="f-low")),
            ("overpay", dict(amount=inv.amount + 1, charge="f-high")),
            ("huge amount", dict(amount=10**9, charge="f-huge")),
            ("foreign currency", dict(currency="USD", charge="f-usd")),
            ("empty currency", dict(currency="", charge="f-empty-cur")),
            (
                "missing charge id",
                dict(
                    amount=inv.amount,
                    charge="",
                ),
            ),
        ]
        for label, kwargs in cases:
            payload = kwargs.pop("payload", inv.payload)
            amount = kwargs.get("amount", inv.amount)
            currency = kwargs.get("currency", "XTR")
            charge = kwargs.get("charge")
            with self.subTest(label):
                reply = await self._pay(
                    ATTACKER, payload, amount, currency=currency, charge=charge
                )
                self.assertIsNotNone(reply, f"{label}: handler did not answer")
                self.assertNotIn("Оплата получена", reply)

        # A completely invented payload never maps onto a payment row.
        reply = await self._pay(
            ATTACKER, "deadbeef" * 4, inv.amount, charge="f-invented"
        )
        self.assertIn("требует проверки", reply)

        self.assertEqual(await self._count(Entitlement), 0)
        self.assertEqual(await self._count(PremiumSubscription), 0)
        user = await self._user(ATTACKER)
        self.assertEqual(user.total_stars_spent or 0, 0)
        self.assertFalse(user.is_premium)
        row = await self._payment(inv.payment_id)
        self.assertEqual(row.status, "precheckout_ok")
        self.assertIsNone(row.telegram_payment_charge_id)

    async def test_empty_payload_and_empty_charge_at_service_level(self):
        inv = await self._buy(ATTACKER, "instant_search")
        await self.service.precheckout(ATTACKER, inv.payload, inv.amount, "XTR", "q1")
        self.assertFalse(
            (await self.service.complete(ATTACKER, "", inv.amount, "XTR", "c1")).ok
        )
        self.assertFalse(
            (
                await self.service.complete(
                    ATTACKER, inv.payload, inv.amount, "XTR", ""
                )
            ).ok
        )
        self.assertFalse(
            (
                await self.service.complete(
                    ATTACKER, inv.payload, inv.amount, "XTR", None
                )
            ).ok
        )
        self.assertFalse(
            (
                await self.service.complete(
                    ATTACKER, inv.payload, inv.amount, "USD", "c1"
                )
            ).ok
        )
        self.assertEqual(await self._count(Entitlement), 0)


class StaleInvoiceTests(PaymentAttackCase):
    async def test_expired_invoice_is_refused_at_precheckout(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        async with self.factory() as s:
            row = await s.get(Payment, inv.payment_id)
            expires = row.expires_at
        # Admin raises the price while the old invoice sits in the chat.
        await self._product("premium_1m", price_stars=inv.amount * 3)
        with self._freeze(expires + timedelta(seconds=1)):
            answer = await self._precheckout(
                ATTACKER, inv.payload, inv.amount, query_id="q1"
            )
        self.assertIsNotNone(answer)
        self.assertFalse(answer.ok)
        self.assertFalse(answer.error_message is None)
        self.assertIn("устарел", answer.error_message)

        with self._freeze(expires + timedelta(seconds=1)):
            reply = await self._pay(ATTACKER, inv.payload, inv.amount, charge="stale-1")
        self.assertNotIn("Оплата получена", reply)
        self.assertEqual(await self._count(PremiumSubscription), 0)

    async def test_old_invoice_still_charges_the_quoted_price_within_the_window(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        await self._product("premium_1m", price_stars=inv.amount * 3)
        answer = await self._precheckout(
            ATTACKER, inv.payload, inv.amount, query_id="q1"
        )
        self.assertTrue(answer.ok)
        reply = await self._pay(ATTACKER, inv.payload, inv.amount, charge="quoted-1")
        self.assertIn("Оплата получена", reply)
        user = await self._user(ATTACKER)
        self.assertTrue(user.is_premium)
        self.assertEqual(user.total_stars_spent, inv.amount)


class PendingStateTests(PaymentAttackCase):
    async def test_complete_credits_retries_within_a_day_and_refuses_stale(self):
        """Защита: complete() сверяет expires_at. Ретрай доставки в пределах
        суток после срока -- кредит проходит (Telegram могла принести апдейт
        позже, деньги списались сразу); старше суток счёт мёртв и кредит
        не проходит."""
        inv = await self._buy(ATTACKER, "premium_1m")
        async with self.factory() as s:
            row = await s.get(Payment, inv.payment_id)
            expires = row.expires_at
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )
        later = expires + timedelta(hours=2)
        with self._freeze(later):
            reply = await self._pay(ATTACKER, inv.payload, inv.amount, charge="late-1")
        self.assertIn("Оплата получена", reply)
        row = await self._payment(inv.payment_id)
        self.assertLess(row.expires_at, later)
        self.assertGreaterEqual(row.paid_at, later)
        self.assertEqual(row.telegram_payment_charge_id, "late-1")

        # Тот же сценарий за сутки+ после срока: счёт мёртв, кредит отвергнут.
        stale = await self._buy(ATTACKER, "premium_1m")
        async with self.factory() as s:
            row2 = await s.get(Payment, stale.payment_id)
            expires2 = row2.expires_at
        self.assertTrue(
            await self._precheckout(ATTACKER, stale.payload, stale.amount, query_id="q2")
        )
        with self._freeze(expires2 + timedelta(hours=25)):
            reply2 = await self._pay(ATTACKER, stale.payload, stale.amount, charge="late-2")
        self.assertNotIn("Оплата получена", reply2)
        row2 = await self._payment(stale.payment_id)
        self.assertEqual(row2.status, "precheckout_ok")
        self.assertIsNone(row2.paid_at)
        self.assertEqual(await self._count(PremiumSubscription), 1)

    async def test_second_pre_checkout_query_for_the_same_invoice_is_refused(self):
        """The classic retry: first attempt fails on the Telegram side (not enough
        Stars, user cancels), the user taps Pay again with a new query id."""
        inv = await self._buy(ATTACKER, "premium_1m")
        first = await self._precheckout(
            ATTACKER, inv.payload, inv.amount, query_id="q-1"
        )
        self.assertTrue(first.ok)
        # A redelivery of the very same query is idempotent and still accepted.
        self.assertTrue(
            (
                await self._precheckout(
                    ATTACKER, inv.payload, inv.amount, query_id="q-1"
                )
            ).ok
        )

        second = await self._precheckout(
            ATTACKER, inv.payload, inv.amount, query_id="q-2"
        )
        self.assertIsNotNone(second)
        self.assertFalse(second.ok)
        self.assertIn("устарел", second.error_message)

        # The invoice is fresh, unpaid and worth exactly what was asked -- only
        # the previous approval id makes it look stale.
        row = await self._payment(inv.payment_id)
        self.assertEqual(row.status, "precheckout_ok")
        self.assertGreater(row.expires_at, utcnow())
        self.assertIsNone(row.telegram_payment_charge_id)
        user = await self._user(ATTACKER)
        self.assertFalse(user.deleted_at)
        self.assertEqual(user.onboarding_step, "MENU")
        async with self.factory() as s:
            self.assertTrue((await s.get(Product, "premium_1m")).is_active)
        # Same thing pinned at the service layer, straight at payments.py.
        self.assertFalse(
            await self.service.precheckout(
                ATTACKER, inv.payload, inv.amount, "XTR", "q-3"
            )
        )
        self.assertTrue(
            await self.service.precheckout(
                ATTACKER, inv.payload, inv.amount, "XTR", "q-1"
            )
        )

    async def test_stuck_precheckout_ok_blocks_buying_again(self):
        inv = await self._buy(ATTACKER, "instant_search")
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )
        # Charge never lands (failed/cancelled payment). User taps Buy again.
        before = len(self._sent("sendInvoice"))
        await self._tap(ATTACKER, "v1:buy:instant_search")
        texts = self._texts()
        self.assertTrue(any("Счёт уже отправлен выше" in t for t in texts))
        self.assertEqual(len(self._sent("sendInvoice")), before)

        # ...and stays locked for the rest of the invoice's 15-minute life.
        with self._freeze(inv.payment.expires_at + timedelta(seconds=1)):
            await self._tap(ATTACKER, "v1:buy:instant_search")
        self.assertGreater(len(self._sent("sendInvoice")), before)

    async def test_rejected_precheckout_leaves_no_credit(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        with self._freeze(utcnow() + timedelta(hours=2)):
            answer = await self._precheckout(
                ATTACKER, inv.payload, inv.amount, query_id="q1"
            )
        self.assertFalse(answer.ok)
        reply = await self._pay(ATTACKER, inv.payload, inv.amount, charge="rej-1")
        self.assertNotIn("Оплата получена", reply)
        user = await self._user(ATTACKER)
        self.assertFalse(user.is_premium)
        self.assertEqual(user.total_stars_spent or 0, 0)
        self.assertEqual(await self._count(PremiumSubscription), 0)
        row = await self._payment(inv.payment_id)
        self.assertEqual(row.status, "created")
        # Nothing is recorded about the refused money: no charge id, no ledger
        # entry -- reconciliation can only be done by hand in support.
        self.assertIsNone(row.telegram_payment_charge_id)
        self.assertEqual(user.total_stars_spent or 0, 0)


class CrossUserTests(PaymentAttackCase):
    async def test_buy_button_always_creates_the_tappers_payment(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        self.assertEqual(inv.payment.user_id, ATTACKER)
        async with self.factory() as s:
            self.assertEqual(
                await s.scalar(
                    select(func.count(Payment.id)).where(Payment.user_id == VICTIM)
                ),
                0,
            )

    async def test_payment_bound_to_a_foreign_session_can_never_settle(self):
        """Crafted `v1:buy:<code>:<session>` binds the payment to a chat that is
        not an active one, so the pre-checkout gate refuses it forever."""
        inv = await self._buy(ATTACKER, "gender_filter_1m", session="VICTIMSESSION")
        self.assertEqual(inv.payment.user_id, ATTACKER)
        answer = await self._precheckout(
            ATTACKER, inv.payload, inv.amount, query_id="q1"
        )
        self.assertIsNotNone(answer)
        self.assertFalse(answer.ok)
        reply = await self._pay(ATTACKER, inv.payload, inv.amount, charge="bind-1")
        self.assertNotIn("Оплата получена", reply)
        self.assertEqual(await self._count(Entitlement), 0)
        self.assertEqual(await self._count(PremiumSubscription), 0)
        victim = await self._user(VICTIM)
        self.assertFalse(victim.is_premium)
        self.assertEqual(victim.total_stars_spent or 0, 0)

    async def test_reveal_purchase_for_someone_elses_chat_is_refused(self):
        from models.chat import Chat

        async with self.factory.begin() as s:
            s.add(
                Chat(
                    session_id="FOREIGNCHAT",
                    user1_id=VICTIM,
                    user2_id=9999,
                    status="active",
                )
            )
        await self._tap(ATTACKER, "v1:buy:reveal_priority_request:FOREIGNCHAT")
        async with self.factory() as s:
            rows = (await s.scalars(select(Payment))).all()
        self.assertEqual(len(rows), 0)
        self.assertTrue(
            any("Покупка уже оплачена" in t for t in self._texts()), self._texts()
        )

    async def test_victim_cannot_be_charged_by_an_attacker_buy_tap(self):
        await self._tap(ATTACKER, "v1:buy:premium_1m")
        async with self.factory() as s:
            rows = (await s.scalars(select(Payment))).all()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row.user_id, ATTACKER)


class DiscountTests(PaymentAttackCase):
    """The 50% first-purchase offer is reserved by create() and released by the
    maintenance sweep -- but only from one payment status."""

    async def _reserved_offer(self):
        from services.growth import first_purchase_discount

        self.assertEqual(await first_purchase_discount(ATTACKER), 50)
        inv = await self._buy(ATTACKER, "premium_1m")
        from models.operations import Offer

        async with self.factory() as s:
            offer = await s.scalar(select(Offer).where(Offer.user_id == ATTACKER))
        self.assertEqual(offer.status, "reserved")
        return inv, offer

    async def test_offer_is_released_when_the_invoice_simply_expires(self):
        """Control case: the sweep does release an untouched invoice."""
        from services.notifications import NotificationService

        inv, offer = await self._reserved_offer()
        async with self.factory() as s:
            row = await s.get(Payment, inv.payment_id)
            expires = row.expires_at
        with (
            self._freeze(expires + timedelta(minutes=1)),
            patch(
                "services.notifications.utcnow",
                return_value=expires + timedelta(minutes=1),
            ),
        ):
            await NotificationService().schedule()
        from models.operations import Offer

        async with self.factory() as s:
            offer = await s.get(Offer, offer.id)
        self.assertEqual(offer.status, "active")

    async def test_a_failed_payment_attempt_keeps_the_discount(self):
        """Защита: неудачный тап по Pay не уничтожает скидку первого отзыва."""
        from models.operations import Offer
        from services.growth import first_purchase_discount
        from services.notifications import NotificationService

        inv, offer = await self._reserved_offer()
        self.assertEqual(inv.amount, 49)  # 99 with the 50% off

        # pre_checkout_query approved, charge never lands (not enough Stars).
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )
        async with self.factory() as s:
            row = await s.get(Payment, inv.payment_id)
            expires = row.expires_at
        with (
            self._freeze(expires + timedelta(minutes=1)),
            patch(
                "services.notifications.utcnow",
                return_value=expires + timedelta(minutes=1),
            ),
        ):
            await NotificationService().schedule()

        async with self.factory() as s:
            offer = await s.get(Offer, offer.id)
            row = await s.get(Payment, inv.payment_id)
        # Свип релизит оффер из любого незавершённого статуса: сам платёж
        # остаётся в precheckout_ok (complete() -- единственное место, где он
        # снимается), но резерв скидки больше не вечен.
        self.assertEqual(row.status, "precheckout_ok")
        self.assertEqual(offer.status, "active")

        # Скидка пережила неудачную попытку: баннер на месте, новый счёт со
        # скидкой, а не за полную цену.
        with self._freeze(expires + timedelta(minutes=2)):
            self.assertEqual(await first_purchase_discount(ATTACKER), 50)
            fresh = await self._buy(ATTACKER, "premium_1m")
        self.assertEqual(fresh.amount, 49)
        self.assertIsNotNone(fresh.payment.offer_id)


class RaceTests(PaymentAttackCase):
    async def test_parallel_successful_payments_do_not_stack_a_second_month(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        self.assertTrue(
            await self._precheckout(ATTACKER, inv.payload, inv.amount, query_id="q1")
        )
        escaped = []
        replies = await asyncio.gather(
            *(
                self._pay(ATTACKER, inv.payload, inv.amount, charge=f"race-{i}")
                for i in range(5)
            ),
            return_exceptions=True,
        )
        for item in replies:
            if isinstance(item, BaseException):
                escaped.append(item)
        credited = sum(
            1 for r in replies if isinstance(r, str) and "Оплата получена" in r
        )
        self.assertEqual(await self._count(PremiumSubscription), 1)
        user = await self._user(ATTACKER)
        self.assertEqual(user.total_stars_spent, inv.amount)
        self.assertLessEqual(
            user.premium_expires - utcnow(), timedelta(days=30, minutes=1)
        )
        # Кредит останавливает статус-гейт под мьютексом, а не только
        # unique-индекс: проигравшие получают ответ, а не IntegrityError.
        self.assertEqual(escaped, [], escaped)

    async def test_parallel_service_complete_credits_once(self):
        inv = await self._buy(ATTACKER, "premium_1m")
        await self.service.precheckout(ATTACKER, inv.payload, inv.amount, "XTR", "q1")
        escaped, results = [], []
        outcome = await asyncio.gather(
            *(
                self.service.complete(
                    ATTACKER, inv.payload, inv.amount, "XTR", f"race-{i}"
                )
                for i in range(5)
            ),
            return_exceptions=True,
        )
        for item in outcome:
            if isinstance(item, BaseException):
                escaped.append(item)
            else:
                results.append(item)
        self.assertEqual(
            sum(1 for r in results if r.ok),
            1,
            [r.code for r in results] + [str(e) for e in escaped],
        )
        self.assertEqual(await self._count(PremiumSubscription), 1)
        # Мьютекс: статус-гейт срабатывает раньше unique-индекса, проигравшие
        # получают чистый ALREADY_PAID, а не падают необработанным
        # IntegrityError из unique-индекса.
        self.assertEqual(escaped, [], escaped)

    async def test_two_concurrent_purchases_for_one_user_keep_both_months(self):
        """Защита: две параллельные покупки одного юзера складываются в 120 дней,
        а не перетирают друг друга через stale-чтение premium_expires."""
        one = await self._buy(ATTACKER, "premium_1m")
        three = await self._buy(ATTACKER, "premium_3m")
        self.assertTrue(
            await self._precheckout(ATTACKER, one.payload, one.amount, query_id="q1")
        )
        self.assertTrue(
            await self._precheckout(
                ATTACKER, three.payload, three.amount, query_id="q2"
            )
        )
        await asyncio.gather(
            self._pay(ATTACKER, one.payload, one.amount, charge="c-one"),
            self._pay(ATTACKER, three.payload, three.amount, charge="c-three"),
            return_exceptions=True,
        )
        user = await self._user(ATTACKER)
        paid = await self._count(Payment)
        credited = await self._count(PremiumSubscription)
        self.assertEqual(paid, 2)
        self.assertEqual(credited, 2, "both purchases were recorded")
        # Мьютекс на complete() не даёт второму прочитать stale-значение:
        # в журнале 30 + 90, и на счёте те же 120 дней, а не 90.
        self.assertGreaterEqual(
            user.premium_expires - utcnow(),
            timedelta(days=119),
            "the lost update reproduced -- a paid month vanished",
        )

    async def test_parallel_buy_taps_share_one_live_invoice(self):
        """Защита: create() сериализован мьютексом -- параллельные тапы
        «Купить» переиспользуют живой счёт: одна строка, одна отправка."""
        await asyncio.gather(
            self._tap(ATTACKER, "v1:buy:premium_1m"),
            self._tap(ATTACKER, "v1:buy:premium_1m"),
        )
        async with self.factory() as s:
            rows = (
                await s.scalars(
                    select(Payment).where(
                        Payment.user_id == ATTACKER, Payment.product == "premium_1m"
                    )
                )
            ).all()
        self.assertEqual(len(rows), 1, [r.id for r in rows])
        self.assertEqual(len(self._sent("sendInvoice")), 1)

    async def test_parallel_buy_taps_spend_one_discount_once(self):
        """Защита: живой счёт один (мьютекс create()), резерв скидки -- CAS
        по active, поэтому одна 50%-скидка выкупает ровно один счёт."""
        from services.growth import first_purchase_discount

        self.assertEqual(await first_purchase_discount(ATTACKER), 50)
        await asyncio.gather(
            self._tap(ATTACKER, "v1:buy:premium_1m"),
            self._tap(ATTACKER, "v1:buy:premium_1m"),
        )
        async with self.factory() as s:
            rows = (
                await s.scalars(
                    select(Payment).where(
                        Payment.user_id == ATTACKER, Payment.product == "premium_1m"
                    )
                )
            ).all()
        discounted = [r for r in rows if r.offer_id is not None]
        self.assertEqual(len(rows), 1, [r.stars_amount for r in rows])
        self.assertEqual(len(discounted), 1)
        self.assertEqual({r.stars_amount for r in discounted}, {49})


if __name__ == "__main__":
    unittest.main(verbosity=2)

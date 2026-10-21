"""The Premium screen: one compact offer, state-aware copy, honest prices."""

import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handlers.premium import _days_phrase, premium_keyboard, show_premium
from utils.time import utcnow


def product(code, price):
    return SimpleNamespace(code=code, title="Premium", price_stars=price)


PRODUCTS = [
    product("premium_1m", 99),
    product("premium_3m", 249),
    product("premium_12m", 799),
]


class PremiumKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def render(self, discounts, lang="ru"):
        service = SimpleNamespace(products=AsyncMock(return_value=PRODUCTS))
        with patch("handlers.premium.PaymentService", return_value=service):
            return await premium_keyboard(discounts, lang)

    async def test_discount_prices_match_the_payment_formula(self):
        keyboard = await self.render(
            {"premium_1m": 50, "premium_3m": 50, "premium_12m": 50}
        )
        labels = [row[0].text for row in keyboard.inline_keyboard]
        self.assertEqual(
            labels,
            [
                "1 месяц · 49 ⭐ вместо 99",
                "3 месяца · 124 ⭐ вместо 249",
                "12 месяцев · 399 ⭐ вместо 799",
            ],
        )
        self.assertEqual(
            [row[0].callback_data for row in keyboard.inline_keyboard],
            ["v1:buy:premium_1m", "v1:buy:premium_3m", "v1:buy:premium_12m"],
        )
        self.assertEqual(keyboard.inline_keyboard[1][0].style, "success")
        self.assertEqual(keyboard.inline_keyboard[0][0].style, "primary")

    async def test_tariffs_are_localized_and_show_the_monthly_price(self):
        keyboard = await self.render({}, "en")
        labels = [row[0].text for row in keyboard.inline_keyboard]
        self.assertEqual(
            labels,
            [
                "1 month · 99 ⭐",
                "3 months · 249 ⭐ · 83 ⭐/mo",
                "12 months · 799 ⭐ · 67 ⭐/mo",
            ],
        )

    async def test_ukrainian_discount_wording_is_not_russian(self):
        keyboard = await self.render({"premium_3m": 20}, "uk")
        self.assertEqual(
            keyboard.inline_keyboard[1][0].text,
            "3 місяці · 199 ⭐ замість 249",
        )


class PremiumScreenTests(unittest.IsolatedAsyncioTestCase):
    def message(self):
        return SimpleNamespace(answer=AsyncMock())

    async def render(
        self, user, lang="ru", first_purchase=50, discounts=None, active=False
    ):
        message = self.message()
        first_purchase_mock = AsyncMock(return_value=first_purchase)
        with (
            patch(
                "repositories.users.UserRepository.get",
                AsyncMock(return_value=user),
            ),
            patch("services.growth.first_purchase_discount", first_purchase_mock),
            patch(
                "handlers.premium._active_discounts",
                AsyncMock(return_value=discounts or {}),
            ),
            patch(
                "handlers.premium.premium_keyboard",
                AsyncMock(return_value=None),
            ) as keyboard,
        ):
            await show_premium(message, 1, lang)
        return message, first_purchase_mock, keyboard

    async def test_new_user_sees_one_time_payment_and_the_discount(self):
        user = SimpleNamespace(premium_expires=None, is_premium=False)
        message, _, keyboard = await self.render(
            user,
            discounts={
                "premium_1m": 50,
                "premium_3m": 50,
                "premium_12m": 50,
            },
        )
        text = message.answer.await_args.args[0]
        self.assertIn("Автопродления нет", text)
        self.assertIn("Без рекламы", text)
        self.assertIn("3 фото вместо 1", text)
        self.assertIn("Скидка до 50%", text)
        self.assertIn("Выбери срок", text)
        self.assertEqual(
            keyboard.await_args.args[0],
            {"premium_1m": 50, "premium_3m": 50, "premium_12m": 50},
        )

    async def test_active_user_gets_status_and_renewal_not_a_resale(self):
        user = SimpleNamespace(
            premium_expires=utcnow() + timedelta(days=10), is_premium=True
        )
        message, first_purchase, keyboard = await self.render(
            user, first_purchase=50, active=True
        )
        text = message.answer.await_args.args[0]
        self.assertIn("<b>Premium</b> активен", text)
        self.assertIn("10 дней", text)
        self.assertIn("Продление добавит время", text)
        self.assertIn("Выбери продление", text)
        self.assertNotIn("Скидка", text)
        first_purchase.assert_not_awaited()
        self.assertEqual(keyboard.await_args.args[0], {})

    async def test_expired_user_sees_the_end_date_and_plans(self):
        expired = utcnow() - timedelta(days=3)
        user = SimpleNamespace(premium_expires=expired, is_premium=False)
        message, _, _ = await self.render(user, first_purchase=None)
        text = message.answer.await_args.args[0]
        self.assertIn("<b>Premium</b> закончился", text)
        self.assertIn(f"{expired:%d.%m.%Y}", text)
        self.assertIn("Выбери срок", text)

    async def test_new_user_without_an_offer_gets_no_fake_discount(self):
        user = SimpleNamespace(premium_expires=None, is_premium=False)
        message, _, _ = await self.render(user, first_purchase=None, discounts={})
        text = message.answer.await_args.args[0]
        self.assertNotIn("Скидка", text)
        self.assertIn("Выбери срок", text)


class DaysPhraseTests(unittest.TestCase):
    def test_russian_plural_forms(self):
        self.assertEqual(_days_phrase(1, "ru"), "1 день")
        self.assertEqual(_days_phrase(2, "ru"), "2 дня")
        self.assertEqual(_days_phrase(5, "ru"), "5 дней")
        self.assertEqual(_days_phrase(11, "ru"), "11 дней")
        self.assertEqual(_days_phrase(21, "ru"), "21 день")

    def test_english_and_ukrainian_forms(self):
        self.assertEqual(_days_phrase(1, "en"), "1 day")
        self.assertEqual(_days_phrase(4, "en"), "4 days")
        self.assertEqual(_days_phrase(2, "uk"), "2 дні")
        self.assertEqual(_days_phrase(5, "uk"), "5 днів")
        self.assertEqual(_days_phrase(21, "uk"), "21 день")

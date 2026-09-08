"""The first-purchase Premium discount: granted once, never to someone who
has already paid, and actually reflected in the invoice amount."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select
from test_chat_duration import DatabaseCase
from models.operations import Offer
from models.payment import Payment
from services.growth import first_purchase_discount
from services.payments import PaymentService, seed_products


class FirstPurchaseDiscountTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await seed_products()

    async def test_granted_once_and_repeat_calls_are_idempotent(self):
        self.assertEqual(await first_purchase_discount(1), 50)
        self.assertEqual(await first_purchase_discount(1), 50)
        async with self.factory() as s:
            offers = list((await s.scalars(select(Offer).where(Offer.user_id == 1))).all())
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].discount_percent, 50)

    async def test_not_offered_to_someone_who_already_paid(self):
        async with self.factory.begin() as s:
            s.add(Payment(user_id=2, product="premium_1m", stars_amount=99, payload="paid-one",
                          currency="XTR", status="paid"))
        self.assertIsNone(await first_purchase_discount(2))
        async with self.factory() as s:
            self.assertEqual(list((await s.scalars(select(Offer).where(Offer.user_id == 2))).all()), [])

    async def test_invoice_amount_uses_the_discount(self):
        await first_purchase_discount(1)
        result = await PaymentService().create(1, "premium_1m")
        self.assertTrue(result.ok)
        payment = result.data["payment"]
        # 99 stars at -50% -> 49, and the offer is recorded on the payment.
        self.assertEqual(payment.stars_amount, 49)
        self.assertIsNotNone(payment.offer_id)

    async def test_full_price_without_the_discount(self):
        result = await PaymentService().create(3, "premium_1m")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["payment"].stars_amount, 99)


if __name__ == "__main__":
    unittest.main()

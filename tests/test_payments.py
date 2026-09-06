import sys
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_chat_duration import DatabaseCase
from services.payments import PaymentService, seed_products
from models.payment import Payment, Entitlement, PremiumSubscription, Product
from models.operations import Offer
from models.user import User
from sqlalchemy import select, func
from utils.time import utcnow


class PaymentTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p=patch('services.payments.async_session',self.factory)
        p.start()
        self.addCleanup(p.stop)
        await seed_products()

    async def test_refund_revokes_entitlement_once(self):
        service = PaymentService()
        p = (await service.create(1, "instant_search")).data["payment"]
        await service.precheckout(1, p.payload, p.stars_amount, "XTR")
        await service.complete(1, p.payload, p.stars_amount, "XTR", "refund-test")
        self.assertFalse((await service.refunded(2, p.payload, p.stars_amount, "XTR", "refund-test")).ok)
        self.assertTrue((await service.refunded(1, p.payload, p.stars_amount, "XTR", "refund-test")).ok)
        self.assertEqual((await service.refunded(1, p.payload, p.stars_amount, "XTR", "refund-test")).code, "ALREADY_REFUNDED")
        async with self.factory() as s:
            self.assertEqual((await s.scalar(select(Entitlement))).remaining_uses, 0)
            self.assertEqual((await s.scalar(select(User).where(User.telegram_id == 1))).total_stars_spent, 0)

    async def test_refunding_stacked_premium_preserves_other_purchase(self):
        service = PaymentService()
        now = utcnow()
        purchases = []
        with patch("services.payments.utcnow", return_value=now):
            for charge in ("one", "two"):
                p = (await service.create(1, "premium_1m")).data["payment"]
                await service.precheckout(1, p.payload, p.stars_amount, "XTR")
                await service.complete(1, p.payload, p.stars_amount, "XTR", charge)
                purchases.append((p, charge))
        with patch("services.payments.utcnow", return_value=now+timedelta(days=5)):
            p, charge = purchases[0]
            await service.refunded(1, p.payload, p.stars_amount, "XTR", charge)
            async with self.factory() as s:
                self.assertEqual((await s.scalar(select(User).where(User.telegram_id == 1))).premium_expires, now+timedelta(days=35))
            p, charge = purchases[1]
            await service.refunded(1, p.payload, p.stars_amount, "XTR", charge)
            async with self.factory() as s:
                self.assertFalse((await s.scalar(select(User).where(User.telegram_id == 1))).is_premium)

    async def test_one_invoice_and_one_checkout_per_payment(self):
        service=PaymentService()
        p=(await service.create(1,'instant_search')).data['payment']
        again=(await service.create(1,'instant_search')).data['payment']
        self.assertEqual(p.id,again.id)
        self.assertTrue(await service.claim_invoice(1,p.id))
        self.assertFalse(await service.claim_invoice(1,p.id))
        await service.invoice_sent(p.id,100)
        self.assertFalse(await service.claim_invoice(1,p.id))
        self.assertTrue(await service.precheckout(1,p.payload,p.stars_amount,'XTR','checkout-one'))
        self.assertTrue(await service.precheckout(1,p.payload,p.stars_amount,'XTR','checkout-one'))
        self.assertFalse(await service.precheckout(1,p.payload,p.stars_amount,'XTR','checkout-two'))

    async def test_payment_validation_and_exactly_one_entitlement(self):
        service=PaymentService()
        result=await service.create(1,'gender_filter_one_match')
        p=result.data['payment']
        self.assertEqual(p.currency,'XTR')
        self.assertFalse(await service.precheckout(2,p.payload,p.stars_amount,'XTR'))
        self.assertFalse(await service.precheckout(1,p.payload,p.stars_amount+1,'XTR'))
        self.assertFalse(await service.precheckout(1,'forged',10,'XTR'))
        self.assertFalse(await service.precheckout(1,p.payload,p.stars_amount,'USD'))
        self.assertTrue(await service.precheckout(1,p.payload,p.stars_amount,'XTR'))
        self.assertTrue((await service.complete(1,p.payload,p.stars_amount,'XTR','test-charge')).ok)
        self.assertFalse((await service.complete(1,p.payload,p.stars_amount,'XTR','test-charge')).ok)
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count(Entitlement.id))),1)

    async def test_premium_extends_and_price_is_snapshot(self):
        old=utcnow()+timedelta(days=10)
        async with self.factory.begin() as s:
            u=await s.scalar(select(User).where(User.telegram_id==1))
            u.premium_expires=old
        service=PaymentService()
        p=(await service.create(1,'premium_1m')).data['payment']
        async with self.factory.begin() as s:
            product=await s.get(Product,'premium_1m')
            product.price_stars+=50
        self.assertTrue(await service.precheckout(1,p.payload,p.stars_amount,'XTR'))
        await service.complete(1,p.payload,p.stars_amount,'XTR','premium-charge')
        async with self.factory() as s:
            u=await s.scalar(select(User).where(User.telegram_id==1))
            self.assertEqual(u.premium_expires,old+timedelta(days=30))
            self.assertEqual(await s.scalar(select(func.count(PremiumSubscription.id))),1)

    async def test_offer_expires_and_cannot_be_used_for_two_invoices(self):
        async with self.factory.begin() as s:
            s.add(Offer(user_id=1,code='test-offer',discount_percent=20,product_scope='premium',ends_at=utcnow()+timedelta(hours=1)))
        service=PaymentService()
        first=(await service.create(1,'premium_1m')).data['payment']
        second=(await service.create(1,'premium_3m')).data['payment']
        self.assertIsNotNone(first.offer_id)
        self.assertIsNone(second.offer_id)
        async with self.factory.begin() as s:
            offer=await s.get(Offer,first.offer_id)
            offer.ends_at=utcnow()-timedelta(seconds=1)
        self.assertFalse(await service.precheckout(1,first.payload,first.stars_amount,'XTR'))

    async def test_monthly_filter_preserves_older_single_use(self):
        service = PaymentService()
        for code in ("gender_filter_one_match", "gender_filter_1m"):
            p = (await service.create(1, code)).data["payment"]
            self.assertTrue(await service.precheckout(1, p.payload, p.stars_amount, "XTR"))
            self.assertTrue((await service.complete(1, p.payload, p.stars_amount, "XTR", code)).ok)
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 1))
            user.settings_gender_filter = "f"
        chat = await self.repo.match(1, 2, self.redis)
        self.assertIsNotNone(chat)
        async with self.factory.begin() as s:
            single = await s.scalar(select(Entitlement).where(Entitlement.kind == "gender_filter_one_match"))
            self.assertEqual(single.remaining_uses, 1)
            monthly = await s.scalar(select(Entitlement).where(Entitlement.kind == "gender_filter_1m"))
            monthly.expires_at = utcnow() - timedelta(seconds=1)
        await self.repo.end(1, chat.session_id)
        self.assertIsNotNone(await self.repo.match(1, 2, self.redis))
        async with self.factory() as s:
            single = await s.scalar(select(Entitlement).where(Entitlement.kind == "gender_filter_one_match"))
            self.assertEqual(single.remaining_uses, 0)


if __name__=='__main__':
    unittest.main(verbosity=2)

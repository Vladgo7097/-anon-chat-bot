import uuid
import logging
from datetime import timedelta
from sqlalchemy import select, or_
from models.base import async_session
from models.payment import Product, Payment, Entitlement, PremiumSubscription
from models.user import User
from repositories.chats import premium_active
from services.onboarding import banned, step_for
from services.result import ServiceResult
from utils.time import utcnow
import config


async def seed_products():
    catalog = [
        ("premium_1m", "Premium 1 месяц", config.STARS_PREMIUM_1M, "premium", 30*86400),
        ("premium_3m", "Premium 3 месяца", config.STARS_PREMIUM_3M, "premium", 90*86400),
        ("premium_12m", "Premium 12 месяцев", config.STARS_PREMIUM_12M, "premium", 365*86400),
        ("instant_search", "Приоритет поиска", config.STARS_INSTANT_SEARCH, "instant_search", 0),
        ("gender_filter_one_match", "Фильтр на один диалог", config.STARS_GENDER_FILTER, "gender_filter_one_match", 0),
        ("gender_filter_1m", "Фильтр по полу на месяц", config.STARS_GENDER_FILTER_MONTH, "gender_filter_1m", 30*86400),
        ("reveal_priority_request", "Приоритетный запрос", config.STARS_REVEAL_PRIORITY, "reveal_priority_request", 0),
        ("reveal_status", "Статус взаимного запроса", config.STARS_REVEAL_STATUS, "reveal_status", 0),
        ("skip_next_delay", "Следующий без задержки", config.STARS_SKIP_NEXT_DELAY, "skip_next_delay", 0),
        ("extra_profile_photo", "Дополнительное фото", config.STARS_EXTRA_PHOTO, "extra_profile_photo", 0),
    ]
    async with async_session.begin() as s:
        for code, title, price, kind, duration in catalog:
            if not await s.get(Product, code):
                s.add(Product(code=code, title=title, price_stars=price, product_type=kind, duration_seconds=duration))


async def available_entitlement(s, user_id, kinds, target_session=None):
    query = select(Entitlement).where(Entitlement.user_id == user_id, Entitlement.kind.in_(kinds),
        Entitlement.remaining_uses > 0, or_(Entitlement.expires_at.is_(None), Entitlement.expires_at > utcnow()))
    if target_session is not None:
        query = query.where(Entitlement.target_session == target_session)
    return await s.scalar(query.order_by(Entitlement.id).with_for_update())


async def consume_delay_skip(user_id):
    async with async_session.begin() as s:
        entitlement=await available_entitlement(s,user_id,["skip_next_delay"])
        if entitlement:
            entitlement.remaining_uses-=1
            return True
        return False


class PaymentService:
    async def refunded(self, user_id, payload, amount, currency, charge_id):
        """Reconcile an actual Telegram refund event; does not initiate refunds."""
        async with async_session.begin() as s:
            p = await s.scalar(select(Payment).where(Payment.payload == payload).with_for_update())
            if (not p or p.user_id != user_id or p.stars_amount != amount or currency != "XTR"
                    or p.telegram_payment_charge_id != charge_id):
                return ServiceResult(False, "INVALID_PAYMENT")
            if p.status == "refunded":
                return ServiceResult(False, "ALREADY_REFUNDED")
            if p.status != "paid":
                return ServiceResult(False, "INVALID_PAYMENT")
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            now = utcnow()
            entitlement = await s.scalar(select(Entitlement).where(Entitlement.source_payment_id == p.id).with_for_update())
            if entitlement:
                entitlement.remaining_uses = 0
                entitlement.expires_at = now
            subscription = await s.scalar(select(PremiumSubscription).where(
                PremiumSubscription.source_payment_id == p.id).with_for_update())
            if subscription and subscription.is_active:
                remaining = max(timedelta(0), subscription.expires_at - max(now, subscription.started_at))
                later = (await s.scalars(select(PremiumSubscription).where(
                    PremiumSubscription.user_id == user_id, PremiumSubscription.id != subscription.id,
                    PremiumSubscription.is_active.is_(True), PremiumSubscription.started_at >= subscription.expires_at)
                    .with_for_update())).all()
                for item in later:
                    item.started_at -= remaining
                    item.expires_at -= remaining
                subscription.is_active = False
                if user and user.premium_expires:
                    user.premium_expires = max(now, user.premium_expires-remaining)
                    user.is_premium = user.premium_expires > now
            p.status = "refunded"
            if user:
                user.total_stars_spent = max(0, (user.total_stars_spent or 0)-amount)
            from services.growth import event
            await event(s, user_id, "payment_refunded", f"refund:{p.id}")
            logging.getLogger(__name__).info("payment_refunded", extra={"payment_id": p.id, "user_id": user_id})
            return ServiceResult(True, "REFUNDED")

    async def products(self, kinds=None):
        async with async_session() as s:
            q = select(Product).where(Product.is_active.is_(True)).order_by(Product.price_stars)
            if kinds:
                q = q.where(Product.code.in_(kinds))
            return list((await s.scalars(q)).all())

    async def create(self, user_id, code, target_session=None):
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            product = await s.get(Product, code)
            if not user or banned(user) or step_for(user) != "MENU" or not product or not product.is_active:
                return ServiceResult(False, "NOT_AVAILABLE")
            if code.startswith("reveal_"):
                from models.chat import Chat
                chat = await s.scalar(select(Chat).where(Chat.session_id == target_session, Chat.status == "active",
                    or_(Chat.user1_id == user_id, Chat.user2_id == user_id)))
                if not chat:
                    return ServiceResult(False, "SESSION_EXPIRED")
            now = utcnow()
            q = select(Payment).where(Payment.user_id == user_id, Payment.product == code,
                Payment.target_session == target_session).order_by(Payment.id.desc())
            old = await s.scalar(q.limit(1))
            if old and target_session and old.status == "paid":
                return ServiceResult(False, "ALREADY_PAID")
            if old and old.status in {"created", "precheckout_ok"} and old.expires_at > now:
                payment = old
            else:
                from models.operations import Offer
                scopes = ["any", code, "premium" if code.startswith("premium_") else "one_time"]
                discount = await s.scalar(select(Offer).where(Offer.user_id==user_id, Offer.status=="active",
                    Offer.starts_at<=now, Offer.ends_at>now, Offer.product_scope.in_(scopes))
                    .order_by(Offer.discount_percent.desc()).with_for_update().limit(1))
                price = max(1, product.price_stars*(100-discount.discount_percent)//100) if discount else product.price_stars
                payment = Payment(user_id=user_id, product=code, stars_amount=product.price_stars,
                    payload=uuid.uuid4().hex, currency="XTR", duration_seconds=product.duration_seconds,
                    expires_at=now+timedelta(minutes=15), target_session=target_session)
                if discount:
                    payment.stars_amount, payment.offer_id = price, discount.id
                    payment.expires_at = min(payment.expires_at, discount.ends_at)
                s.add(payment)
                await s.flush()
                if discount:
                    discount.status, discount.reserved_payment_id = "reserved", payment.id
            return ServiceResult(True, "INVOICE", {"payment": payment, "title": product.title})

    async def claim_invoice(self,user_id,payment_id):
        async with async_session.begin() as s:
            p=await s.get(Payment,payment_id,with_for_update=True)
            if not p or p.user_id!=user_id or p.status!="created" or p.invoice_message_id:
                return False
            if p.invoice_claimed_at and p.invoice_claimed_at>utcnow()-timedelta(seconds=30):
                return False
            p.invoice_claimed_at=utcnow()
            return True

    async def invoice_sent(self,payment_id,message_id):
        async with async_session.begin() as s:
            p=await s.get(Payment,payment_id,with_for_update=True)
            p.invoice_message_id=message_id

    async def precheckout(self, user_id, payload, amount, currency, query_id=None):
        async with async_session.begin() as s:
            p = await s.scalar(select(Payment).where(Payment.payload == payload).with_for_update())
            if not p or p.user_id != user_id or p.stars_amount != amount or currency != "XTR" or p.status not in {"created", "precheckout_ok"} or p.expires_at <= utcnow():
                return False
            if p.status=="precheckout_ok" and p.precheckout_query_id != query_id:
                p.precheckout_query_id = query_id
            user = await s.scalar(select(User).where(User.telegram_id == user_id))
            product = await s.get(Product, p.product)
            if not user or banned(user) or user.deleted_at or step_for(user) != "MENU" or not product or not product.is_active:
                return False
            if p.offer_id:
                from models.operations import Offer
                discount = await s.get(Offer, p.offer_id)
                if not discount or discount.status != "reserved" or discount.reserved_payment_id != p.id or discount.ends_at <= utcnow():
                    return False
            if p.target_session:
                from models.chat import Chat
                if not await s.scalar(select(Chat).where(Chat.session_id==p.target_session, Chat.status=="active")):
                    return False
            p.status = "precheckout_ok"
            p.precheckout_query_id=query_id
            return True

    async def complete(self, user_id, payload, amount, currency, charge_id):
        async with async_session.begin() as s:
            p = await s.scalar(select(Payment).where(Payment.payload == payload).with_for_update())
            if not p or p.user_id != user_id or p.stars_amount != amount or currency != "XTR" or not charge_id:
                return ServiceResult(False, "INVALID_PAYMENT")
            if p.status == "paid":
                return ServiceResult(False, "ALREADY_PAID")
            if p.status != "precheckout_ok":
                return ServiceResult(False, "INVALID_PAYMENT")
            if await s.scalar(select(Payment).where(Payment.telegram_payment_charge_id == charge_id)):
                return ServiceResult(False, "ALREADY_PAID")
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            now = utcnow()
            p.status, p.paid_at, p.telegram_payment_charge_id = "paid", now, charge_id
            p.telegram_payment_id = payload
            user.total_stars_spent = (user.total_stars_spent or 0) + amount
            from models.operations import Offer
            from services.growth import event
            if p.offer_id:
                discount = await s.scalar(select(Offer).where(Offer.id==p.offer_id).with_for_update())
                if discount:
                    discount.status, discount.redeemed_at = "redeemed", now
            await event(s, user_id, "payment_success", f"payment:{p.id}")
            if p.product.startswith("premium_"):
                base = max(now, user.premium_expires or now)
                user.premium_expires = base + timedelta(seconds=p.duration_seconds)
                user.is_premium = True
                await event(s, user_id, "premium_started", f"premium:{p.id}")
                s.add(PremiumSubscription(user_id=user_id, plan=p.product.removeprefix("premium_"), stars_paid=amount,
                    started_at=base, expires_at=user.premium_expires, source_payment_id=p.id))
            else:
                s.add(Entitlement(user_id=user_id, kind=p.product, source_payment_id=p.id, target_session=p.target_session,
                    expires_at=now+timedelta(seconds=p.duration_seconds) if p.duration_seconds else None))
            logging.getLogger(__name__).info("payment_completed", extra={"payment_id": p.id, "user_id": user_id})
            return ServiceResult(True, "PAID", {"product": p.product, "session_id": p.target_session})

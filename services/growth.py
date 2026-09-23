from datetime import timedelta
from sqlalchemy import select, func
from models.base import async_session
from models.user import User
from models.operations import Referral, Streak, Offer, Achievement, AnalyticsEvent
from utils.time import utcnow


async def event(s, user_id, name, key):
    if not await s.scalar(select(AnalyticsEvent).where(AnalyticsEvent.dedupe_key == key)):
        s.add(AnalyticsEvent(user_id=user_id, event=name, dedupe_key=key))


async def award(s, user_id, code):
    if not await s.get(Achievement, (user_id, code)):
        s.add(Achievement(user_id=user_id, achievement_code=code))


async def offer(s, user_id, code, percent, scope="premium", days=30):
    if not await s.scalar(select(Offer).where(Offer.code == code)):
        s.add(Offer(user_id=user_id, code=code, discount_percent=percent, product_scope=scope, ends_at=utcnow()+timedelta(days=days)))
        from services.notifications import enqueue
        await enqueue(s,user_id,"reward",f"reward:{code}",f"<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Получена скидка {percent}%. Срок: {days} дней. Подробности в профиле.","profile:rewards")


async def first_purchase_discount(user_id, percent=50, days=30):
    """Half off the first Premium ever bought, granted the moment the person
    first looks at the tariffs.

    Almost every abandoned invoice belongs to someone who has never paid with
    Stars at all: the price is not the wall, buying Stars for the first time
    is. This lowers that first step once, per account, and never again --
    PaymentService.create() picks the discount up on its own at invoice time.
    Returns the active percent, or None when the person has already paid for
    something (they are past this barrier and do not need it).
    """
    from models.payment import Payment
    now = utcnow()
    code = f"first_purchase:{user_id}"
    async with async_session.begin() as s:
        if await s.scalar(select(Payment.id).where(Payment.user_id == user_id, Payment.status == "paid").limit(1)):
            return None
        existing = await s.scalar(select(Offer).where(Offer.code == code))
        if existing:
            active = existing.status == "active" and existing.starts_at <= now < existing.ends_at
            return existing.discount_percent if active else None
        s.add(Offer(user_id=user_id, code=code, discount_percent=percent,
                    product_scope="premium", ends_at=now+timedelta(days=days)))
    return percent


async def completed_chat(s, user):
    await SeasonalService.reward(s,user.telegram_id)
    for count, code in ((1,"first_chat"),(50,"50_chats"),(100,"100_chats")):
        if user.chats_count >= count:
            await award(s, user.telegram_id, code)
    referral = await s.scalar(select(Referral).where(Referral.referred_id == user.telegram_id).with_for_update())
    if referral and referral.status == "pending":
        referral.status, referral.activated_at = "activated", utcnow()
        referrer = await s.scalar(select(User).where(User.telegram_id == referral.referrer_id).with_for_update())
        if referrer:
            referrer.referrals_activated += 1
            # The inviter's reward is 7 days of Premium, stacking on any
            # active subscription -- the old 20% coupon invited nobody: a
            # discount on a product you have never bought is not a carrot.
            from models.payment import PremiumSubscription
            from services.notifications import enqueue
            referrer.premium_expires = max(utcnow(), referrer.premium_expires or utcnow()) + timedelta(days=7)
            referrer.is_premium = True
            s.add(PremiumSubscription(user_id=referrer.telegram_id, plan="referral",
                                      stars_paid=0, expires_at=referrer.premium_expires))
            await enqueue(s, referrer.telegram_id, "reward", f"reward:ref_premium:{referral.id}",
                          "<tg-emoji emoji-id=\"5312160339335347417\">🎁</tg-emoji> Друг завершил первый диалог — тебе +7 дней Premium! Подробности в профиле.",
                          "profile:rewards")
        await offer(s, user.telegram_id, f"ref:{referral.id}:invited", 20)


class StreakService:
    async def checkin(self, user_id):
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id).with_for_update())
            if not user or user.deleted_at:
                return 0
            record = await s.get(Streak, user_id)
            if not record:
                record = Streak(user_id=user_id, current_streak=0, best_streak=0)
                s.add(record)
            today = utcnow().date()
            if record.last_checkin_date == today:
                return record.current_streak
            record.current_streak = record.current_streak+1 if record.last_checkin_date == today-timedelta(days=1) else 1
            record.best_streak = max(record.best_streak, record.current_streak)
            record.last_checkin_date = today
            user.streak_days, user.last_active_date = record.current_streak, today
            await event(s,user_id,"daily_active",f"active:{user_id}:{today}")
            for day, discount, scope in ((3,10,"one_time"),(7,20,"premium"),(30,30,"premium_12m")):
                if record.current_streak >= day:
                    await award(s, user_id, f"streak_{day}")
                    await offer(s, user_id, f"streak:{user_id}:{day}", discount, scope)
            return record.current_streak


async def track(user_id, name, key):
    async with async_session.begin() as s:
        user = await s.scalar(select(User).where(User.telegram_id==user_id).with_for_update())
        if user:
            await event(s,user_id,name,key)


class GrowthService:
    async def profile(self, user_id):
        async with async_session() as s:
            user=await s.scalar(select(User).where(User.telegram_id==user_id))
            achievements=list((await s.scalars(select(Achievement.achievement_code).where(Achievement.user_id==user_id))).all())
            offers=list((await s.scalars(select(Offer).where(Offer.user_id==user_id,Offer.status.in_(["active","reserved"]),Offer.ends_at>utcnow()))).all())
            return user,achievements,offers

    async def upsell(self, user_id):
        from services.settings import bot_value
        threshold = await bot_value("upsell_chat_count", 3)
        from models.payment import Payment,Product
        from repositories.chats import premium_active
        from models.chat import Chat
        from sqlalchemy import or_
        async with async_session() as s:
            user=await s.scalar(select(User).where(User.telegram_id==user_id))
            if not user or premium_active(user):
                return None
            last=await s.scalar(select(func.max(AnalyticsEvent.created_at)).where(AnalyticsEvent.user_id==user_id,AnalyticsEvent.event=="premium_offer_shown"))
            if last and last>utcnow()-timedelta(days=1):
                return None
            ended=select(func.count(Chat.id)).where(Chat.status=="ended",or_(Chat.user1_id==user_id,Chat.user2_id==user_id))
            if last:
                ended=ended.where(Chat.ended_at>last)
            if await s.scalar(ended)<threshold:
                return None
            product=await s.get(Product,"premium_1m")
            if not product or not product.is_active:
                return None
            spent=await s.scalar(select(func.coalesce(func.sum(Payment.stars_amount),0)).where(Payment.user_id==user_id,Payment.status=="paid",Payment.paid_at>utcnow()-timedelta(days=30),Payment.product.in_(["instant_search","gender_filter_one_match","skip_next_delay"])))
            text="<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> С Premium доступны фильтры, приоритет поиска и «Следующий» без задержки."
            if spent>product.price_stars:
                text+=f"\nЗа последние 30 дней на эти улучшения потрачено {spent}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji>. Premium на месяц стоит {product.price_stars}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> — на {spent-product.price_stars}<tg-emoji emoji-id=\"5920281855378068765\">⭐</tg-emoji> меньше."
            return text


class SeasonalService:
    @staticmethod
    async def active(s):
        from models.operations import BotSetting
        from datetime import datetime
        flag=await s.get(BotSetting,"seasonal_enabled")
        item=await s.get(BotSetting,"seasonal_event")
        if not flag or not flag.value_json.get("value") or not item:
            return None
        data=item.value_json
        start,end=datetime.fromisoformat(data["starts_at"]),datetime.fromisoformat(data["ends_at"])
        return data if start<=utcnow()<end else None

    @staticmethod
    async def reward(s,user_id):
        from datetime import datetime
        data=await SeasonalService.active(s)
        if not data:
            return
        code=f"season:{data['code']}:{user_id}"
        if await s.scalar(select(Offer.id).where(Offer.code==code)):
            return
        await award(s,user_id,f"season:{data['code']}")
        s.add(Offer(user_id=user_id,code=code,discount_percent=data["discount"],product_scope="premium",ends_at=datetime.fromisoformat(data["ends_at"])))
        from services.notifications import enqueue
        await enqueue(s,user_id,"reward",f"reward:{code}",f"<tg-emoji emoji-id=\"5994502837327892086\">🎉</tg-emoji> Событие «{data['code']}»: получен бейдж и скидка {data['discount']}% на Premium до {data['ends_at']}.","profile:rewards")

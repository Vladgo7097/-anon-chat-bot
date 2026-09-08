"""Cross-promo banner slots sold to other Telegram channels.

Revenue here is external — the advertiser pays outside Telegram Stars — so
this module only meters impressions and joins against the agreed budget; it
never touches services/payments.py or the Stars product catalog. Telegram
gives a bot no server-side event for a plain url= button tap, so clicks are
never recorded: a fabricated click count would be exactly the kind of
fictitious monetization signal the product rules forbid. Joins are real and
attributed only when the advertiser granted the bot admin rights, which is
what lets a per-campaign invite link exist in the first place (see
handlers/admin.py's launch flow and handlers/ads.py's join listener).
"""
import re
from sqlalchemy import select, func
from redis.exceptions import RedisError
from models.base import async_session
from models.operations import AdCampaign, AdImpression, AdJoin
from models.user import User
from repositories.chats import premium_active
from services.admin import authorize, audit
from services.chat_manager import get_redis
from services.result import ServiceResult
from utils.time import utcnow

STATUSES = {"draft", "active", "paused", "completed"}
COOLDOWN_KEY = "ad:cooldown:{}"
# ASCII only (Telegram usernames can't be anything else), 5-32 chars, and not
# all underscores -- shared with handlers/admin.py's ad_channel input step so
# the UI-level and service-level checks can't drift apart again.
CHANNEL_USERNAME_RE = re.compile(r"(?=.*[A-Za-z0-9])[A-Za-z0-9_]{5,32}$")


async def _eligible_campaign(s, now):
    """Least-shown active campaign that still has budget; skip_locked so two
    concurrent picks never fight over the same row."""
    candidates = (await s.scalars(select(AdCampaign).where(AdCampaign.status == "active")
        .order_by(AdCampaign.impressions_total).with_for_update(skip_locked=True))).all()
    for campaign in candidates:
        if campaign.total_limit and campaign.impressions_total >= campaign.total_limit:
            continue
        if campaign.daily_limit:
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            shown_today = await s.scalar(select(func.count(AdImpression.id)).where(
                AdImpression.campaign_id == campaign.id, AdImpression.shown_at >= day_start))
            if shown_today >= campaign.daily_limit:
                continue
        return campaign
    return None


class AdService:
    """User-facing: which ad (if any) to show between two dialogues."""

    async def pick(self, user_id):
        from services.settings import bot_value
        try:
            if await (await get_redis()).exists(COOLDOWN_KEY.format(user_id)):
                return ServiceResult(False, "COOLDOWN")
        except RedisError:
            pass  # Degrade to DB-only limits rather than block the whole flow.
        async with async_session.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == user_id))
            if not user or premium_active(user):
                return ServiceResult(False, "NOT_ELIGIBLE")
            campaign = await _eligible_campaign(s, utcnow())
            if not campaign:
                return ServiceResult(False, "NO_CAMPAIGN")
            impression = AdImpression(campaign_id=campaign.id, user_id=user_id)
            s.add(impression)
            campaign.impressions_total += 1
            await s.flush()
            data = {"impression_id": impression.id, "title": campaign.title,
                    "subtitle": campaign.subtitle, "body": campaign.body,
                    "channel_username": campaign.channel_username, "invite_link": campaign.invite_link}
        try:
            cooldown = await bot_value("ad_cooldown_seconds", 1800)
            await (await get_redis()).set(COOLDOWN_KEY.format(user_id), "1", ex=cooldown)
        except RedisError:
            pass
        return ServiceResult(True, "AD", data)


class AdCampaignService:
    """Admin-facing: create and manage campaigns. Every change is audited."""

    async def create(self, admin, channel_username, title, subtitle, body, daily_limit, total_limit):
        authorize(admin)
        if not CHANNEL_USERNAME_RE.fullmatch(channel_username):
            return ServiceResult(False, "INVALID_CHANNEL")
        if not title or len(title) > 64 or len(subtitle) > 64:
            return ServiceResult(False, "INVALID_TITLE")
        if not body or len(body) > 400:
            return ServiceResult(False, "INVALID_BODY")
        if daily_limit < 0 or total_limit < 0:
            return ServiceResult(False, "INVALID_LIMIT")
        async with async_session.begin() as s:
            campaign = AdCampaign(admin_id=admin, channel_username=channel_username, title=title,
                subtitle=subtitle, body=body, daily_limit=daily_limit, total_limit=total_limit)
            s.add(campaign)
            await s.flush()
            audit(s, admin, "ad_create", "ad_campaign", campaign.id, after={
                "channel_username": channel_username, "title": title, "daily_limit": daily_limit,
                "total_limit": total_limit})
            campaign_id = campaign.id
        return ServiceResult(True, "DRAFT", {"campaign_id": campaign_id})

    async def get(self, admin, campaign_id):
        authorize(admin)
        async with async_session() as s:
            return await s.get(AdCampaign, campaign_id)

    async def list(self, admin, limit=10):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(AdCampaign).where(AdCampaign.status != "completed")
                .order_by(AdCampaign.id.desc()).limit(limit))).all())

    async def set_status(self, admin, campaign_id, status, invite_link=None):
        authorize(admin)
        if status not in STATUSES:
            return ServiceResult(False, "INVALID")
        async with async_session.begin() as s:
            campaign = await s.get(AdCampaign, campaign_id, with_for_update=True)
            if not campaign:
                return ServiceResult(False, "NOT_FOUND")
            if campaign.status == "completed":
                return ServiceResult(False, "ALREADY_COMPLETED")
            before = campaign.status
            campaign.status = status
            if status == "active" and not campaign.started_at:
                campaign.started_at = utcnow()
                if invite_link:
                    campaign.invite_link = invite_link
            if status == "completed":
                campaign.ended_at = utcnow()
            audit(s, admin, "ad_status", "ad_campaign", campaign_id, {"status": before}, {"status": status})
        return ServiceResult(True, "SAVED")

    async def delete_draft(self, admin, campaign_id):
        authorize(admin)
        async with async_session.begin() as s:
            campaign = await s.get(AdCampaign, campaign_id, with_for_update=True)
            if not campaign or campaign.status != "draft":
                return ServiceResult(False, "NOT_FOUND")
            await s.delete(campaign)
            audit(s, admin, "ad_delete", "ad_campaign", campaign_id)
        return ServiceResult(True, "DELETED")


async def record_join(invite_link, telegram_user_id, joined_at):
    """Called from handlers/ads.py's chat_member listener with a real Telegram
    join event. Ignored silently if invite_link matches no active campaign —
    that just means the channel is using its link for something else too."""
    async with async_session.begin() as s:
        campaign = await s.scalar(select(AdCampaign).where(
            AdCampaign.invite_link == invite_link, AdCampaign.status == "active").with_for_update())
        if not campaign:
            return
        key = f"adjoin:{campaign.id}:{telegram_user_id}:{joined_at.isoformat()}"
        if await s.scalar(select(AdJoin.id).where(AdJoin.dedupe_key == key)):
            return
        s.add(AdJoin(campaign_id=campaign.id, user_id=telegram_user_id, joined_at=joined_at, dedupe_key=key))
        campaign.joins_total += 1

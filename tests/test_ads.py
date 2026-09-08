"""Cross-promo ad campaigns: eligibility, budget limits, cooldown, admin CRUD."""
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from test_chat_duration import DatabaseCase
from models.user import User
from models.operations import AdCampaign, AdImpression, AdJoin, AdminAudit
from services.ads import AdService, AdCampaignService
from services.result import ServiceResult
from utils.time import utcnow


class FakeRedis:
    """Real exists/set for the ad cooldown, plus a stub for ChatRepository.match's
    search-queue membership check (unrelated to ads, just a setup dependency)."""
    def __init__(self):
        self.store = {}

    async def exists(self, key):
        return key in self.store

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def zscore(self, key, member):
        return 1

    async def eval(self, script, numkeys, *args):
        # ChatService.end_chat's cleanup script; a real delete is enough here.
        for key in args[:numkeys]:
            self.store.pop(key, None)
        return 0


class AdCampaignServiceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.ADMIN_IDS", {99})
        p.start()
        self.addCleanup(p.stop)

    async def make(self, **overrides):
        args = dict(channel_username="news_channel", title="🌍 Мир вокруг",
                    subtitle="Путешествия · 89K подписчиков", body="Маршруты и лайфхаки.",
                    daily_limit=0, total_limit=0)
        args.update(overrides)
        return await AdCampaignService().create(99, **args)

    async def test_nonadmin_denied(self):
        with self.assertRaises(PermissionError):
            await AdCampaignService().create(1, "x"*6, "t", "", "b", 0, 0)

    async def test_validation_rejects_bad_input(self):
        self.assertFalse((await self.make(channel_username="ab")).ok)  # too short
        self.assertFalse((await self.make(channel_username="bad channel!")).ok)  # bad chars
        # str.isalnum() accepts non-ASCII letters; Telegram usernames don't.
        self.assertFalse((await self.make(channel_username="приветик")).ok)
        self.assertFalse((await self.make(channel_username="_____")).ok)  # no actual letters/digits
        self.assertFalse((await self.make(title="")).ok)
        self.assertFalse((await self.make(title="x"*65)).ok)
        self.assertFalse((await self.make(body="")).ok)
        self.assertFalse((await self.make(daily_limit=-1)).ok)

    async def test_create_starts_as_draft_and_is_audited(self):
        result = await self.make()
        self.assertTrue(result.ok)
        async with self.factory() as s:
            campaign = await s.get(AdCampaign, result.data["campaign_id"])
            self.assertEqual(campaign.status, "draft")
            self.assertIsNone(campaign.started_at)
            self.assertEqual(await s.scalar(select(AdminAudit.id).where(AdminAudit.action == "ad_create")), 1)

    async def test_lifecycle_transitions_and_audit(self):
        campaign_id = (await self.make()).data["campaign_id"]
        service = AdCampaignService()
        launched = await service.set_status(99, campaign_id, "active")
        self.assertTrue(launched.ok)
        async with self.factory() as s:
            campaign = await s.get(AdCampaign, campaign_id)
            self.assertEqual(campaign.status, "active")
            self.assertIsNotNone(campaign.started_at)
        self.assertTrue((await service.set_status(99, campaign_id, "paused")).ok)
        self.assertTrue((await service.set_status(99, campaign_id, "active")).ok)
        self.assertTrue((await service.set_status(99, campaign_id, "completed")).ok)
        async with self.factory() as s:
            campaign = await s.get(AdCampaign, campaign_id)
            self.assertIsNotNone(campaign.ended_at)
        # A completed campaign cannot be reopened.
        self.assertFalse((await service.set_status(99, campaign_id, "active")).ok)

    async def test_delete_only_removes_a_draft(self):
        service = AdCampaignService()
        campaign_id = (await self.make()).data["campaign_id"]
        await service.set_status(99, campaign_id, "active")
        self.assertFalse((await service.delete_draft(99, campaign_id)).ok)
        other_id = (await self.make(channel_username="second_channel")).data["campaign_id"]
        self.assertTrue((await service.delete_draft(99, other_id)).ok)
        async with self.factory() as s:
            self.assertIsNone(await s.get(AdCampaign, other_id))

    async def test_list_excludes_completed(self):
        service = AdCampaignService()
        keep_id = (await self.make()).data["campaign_id"]
        done_id = (await self.make(channel_username="second_channel")).data["campaign_id"]
        await service.set_status(99, done_id, "active")
        await service.set_status(99, done_id, "completed")
        ids = {c.id for c in await service.list(99)}
        self.assertIn(keep_id, ids)
        self.assertNotIn(done_id, ids)


class AdServiceTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = FakeRedis()
        p = patch("services.ads.get_redis", AsyncMock(return_value=self.redis))
        p.start()
        self.addCleanup(p.stop)

    async def active_campaign(self, **overrides):
        fields = dict(admin_id=99, channel_username="news_channel", title="🌍 Мир вокруг",
            subtitle="", body="Текст.", status="active", started_at=utcnow())
        fields.update(overrides)
        async with self.factory.begin() as s:
            campaign = AdCampaign(**fields)
            s.add(campaign)
            await s.flush()
            return campaign.id

    async def test_no_active_campaign_yields_no_ad(self):
        result = await AdService().pick(1)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NO_CAMPAIGN")

    async def test_premium_user_never_sees_an_ad_and_consumes_no_budget(self):
        campaign_id = await self.active_campaign()
        async with self.factory.begin() as s:
            user = await s.scalar(select(User).where(User.telegram_id == 1))
            user.premium_expires = utcnow()+timedelta(days=30)
        result = await AdService().pick(1)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NOT_ELIGIBLE")
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).impressions_total, 0)

    async def test_free_user_gets_the_ad_and_it_is_billed_as_one_impression(self):
        campaign_id = await self.active_campaign()
        result = await AdService().pick(1)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["channel_username"], "news_channel")
        self.assertIn("impression_id", result.data)
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).impressions_total, 1)
            self.assertEqual(await s.scalar(select(AdImpression.user_id).where(
                AdImpression.campaign_id == campaign_id)), 1)

    async def test_cooldown_blocks_a_second_pick_and_does_not_double_bill(self):
        campaign_id = await self.active_campaign()
        first = await AdService().pick(1)
        self.assertTrue(first.ok)
        second = await AdService().pick(1)
        self.assertFalse(second.ok)
        self.assertEqual(second.code, "COOLDOWN")
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).impressions_total, 1)

    async def test_total_limit_stops_the_campaign(self):
        campaign_id = await self.active_campaign(total_limit=1)
        self.assertTrue((await AdService().pick(1)).ok)
        self.redis.store.clear()  # bypass per-user cooldown to isolate the budget check
        result = await AdService().pick(2)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NO_CAMPAIGN")
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).impressions_total, 1)

    async def test_daily_limit_stops_the_campaign_even_under_total_budget(self):
        await self.active_campaign(daily_limit=1, total_limit=100)
        self.assertTrue((await AdService().pick(1)).ok)
        self.redis.store.clear()
        result = await AdService().pick(2)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "NO_CAMPAIGN")

    async def test_paused_campaign_is_never_picked(self):
        await self.active_campaign(status="paused", started_at=None)
        result = await AdService().pick(1)
        self.assertFalse(result.ok)

    async def test_least_shown_campaign_is_picked_first(self):
        quiet_id = await self.active_campaign(channel_username="quiet_channel")
        busy_id = await self.active_campaign(channel_username="busy_channel", impressions_total=50)
        result = await AdService().pick(1)
        self.assertTrue(result.ok)
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, quiet_id)).impressions_total, 1)
            self.assertEqual((await s.get(AdCampaign, busy_id)).impressions_total, 50)


class ChatFinishAdIntegrationTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = FakeRedis()
        for target in ("services.ads.get_redis", "services.chat_manager.get_redis"):
            p = patch(target, AsyncMock(return_value=self.redis))
            p.start()
            self.addCleanup(p.stop)
        async with self.factory.begin() as s:
            s.add(AdCampaign(admin_id=99, channel_username="news_channel", title="🌍 Мир вокруг",
                subtitle="", body="Текст.", status="active", started_at=utcnow()))

    async def match(self, a_id, b_id):
        # ChatRepository.match() requires both sides to still hold their
        # search:user:{id} lifetime marker; a real search enqueue sets it.
        self.redis.store[f"search:user:{a_id}"] = "1"
        self.redis.store[f"search:user:{b_id}"] = "1"
        return await self.repo.match(a_id, b_id, self.redis)

    async def test_stop_shows_the_ad_card_with_a_url_button_to_the_channel(self):
        from handlers.chat import finish
        chat = await self.match(1, 2)
        self.assertIsNotNone(chat)
        message = AsyncMock()
        message.answer = AsyncMock(side_effect=lambda *a, **k: AsyncMock())
        await finish(message, 1, "stop")
        texts = [call.args[0] for call in message.answer.await_args_list]
        self.assertTrue(any("Реклама" in t for t in texts))
        ad_call = next(c for c in message.answer.await_args_list if "Реклама" in c.args[0])
        markup = ad_call.kwargs["reply_markup"]
        go_button = markup.inline_keyboard[0][0]
        self.assertEqual(go_button.url, "https://t.me/news_channel")
        self.assertIsNone(go_button.callback_data)

    async def test_unreachable_partner_is_also_a_real_dialogue_end(self):
        # finish() is also called with reason="unreachable" when copy_message
        # hits TelegramForbiddenError mid-relay; for the user still looking at
        # this chat, their dialogue just ended the same as a "stop" would.
        from handlers.chat import finish
        await self.match(1, 2)
        message = AsyncMock()
        message.answer = AsyncMock(side_effect=lambda *a, **k: AsyncMock())
        await finish(message, 1, "unreachable")
        texts = [call.args[0] for call in message.answer.await_args_list]
        self.assertTrue(any("Реклама" in t for t in texts))

    async def test_cooldown_prevents_an_ad_on_the_immediately_following_next(self):
        from handlers.chat import finish
        await self.match(1, 2)
        first = AsyncMock()
        first.answer = AsyncMock(side_effect=lambda *a, **k: AsyncMock())
        await finish(first, 1, "stop")
        self.assertTrue(any("Реклама" in c.args[0] for c in first.answer.await_args_list))
        await self.match(1, 3)
        second = AsyncMock()
        second.answer = AsyncMock(side_effect=lambda *a, **k: AsyncMock())
        await finish(second, 1, "next")
        self.assertFalse(any("Реклама" in c.args[0] for c in second.answer.await_args_list))


class AdJoinTests(DatabaseCase):
    async def campaign_with_link(self, invite_link="https://t.me/+abc123", **overrides):
        fields = dict(admin_id=99, channel_username="news_channel", title="🌍 Мир вокруг",
            subtitle="", body="Текст.", status="active", started_at=utcnow(), invite_link=invite_link)
        fields.update(overrides)
        async with self.factory.begin() as s:
            campaign = AdCampaign(**fields)
            s.add(campaign)
            await s.flush()
            return campaign.id

    async def test_join_via_tracked_link_is_recorded_and_counted(self):
        from services.ads import record_join
        campaign_id = await self.campaign_with_link()
        when = utcnow()
        await record_join("https://t.me/+abc123", 555, when)
        async with self.factory() as s:
            campaign = await s.get(AdCampaign, campaign_id)
            self.assertEqual(campaign.joins_total, 1)
            row = await s.scalar(select(AdJoin).where(AdJoin.campaign_id == campaign_id))
            self.assertEqual(row.user_id, 555)

    async def test_duplicate_event_is_not_double_counted(self):
        from services.ads import record_join
        campaign_id = await self.campaign_with_link()
        when = utcnow()
        await record_join("https://t.me/+abc123", 555, when)
        await record_join("https://t.me/+abc123", 555, when)
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).joins_total, 1)
            rows = await s.scalar(select(func.count(AdJoin.id)).where(AdJoin.campaign_id == campaign_id))
            self.assertEqual(rows, 1)

    async def test_a_second_join_by_the_same_user_at_a_different_time_counts_again(self):
        from services.ads import record_join
        campaign_id = await self.campaign_with_link()
        await record_join("https://t.me/+abc123", 555, utcnow())
        await record_join("https://t.me/+abc123", 555, utcnow()+timedelta(minutes=1))
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).joins_total, 2)

    async def test_unrelated_invite_link_is_ignored_without_error(self):
        from services.ads import record_join
        campaign_id = await self.campaign_with_link()
        await record_join("https://t.me/+someone_elses_link", 555, utcnow())
        async with self.factory() as s:
            self.assertEqual((await s.get(AdCampaign, campaign_id)).joins_total, 0)

    async def test_join_after_the_campaign_ended_is_not_counted(self):
        # Telegram keeps delivering chat_member events for the same invite
        # link after a campaign is paused/completed (the bot is still admin
        # there); those joins must not keep inflating a stopped campaign.
        from services.ads import record_join
        for status in ("paused", "completed"):
            campaign_id = await self.campaign_with_link(
                invite_link=f"https://t.me/+{status}", status=status)
            await record_join(f"https://t.me/+{status}", 555, utcnow())
            async with self.factory() as s:
                self.assertEqual((await s.get(AdCampaign, campaign_id)).joins_total, 0)


class ChatMemberHandlerTests(unittest.IsolatedAsyncioTestCase):
    def event(self, invite_link, old_status, new_status, user_id=555):
        link = SimpleNamespace(invite_link=invite_link) if invite_link else None
        return SimpleNamespace(invite_link=link, date=utcnow(),
            old_chat_member=SimpleNamespace(status=old_status),
            new_chat_member=SimpleNamespace(status=new_status, user=SimpleNamespace(id=user_id)))

    async def test_fresh_join_via_tracked_link_is_recorded(self):
        from handlers.ads import on_channel_membership_change
        event = self.event("https://t.me/+abc", "left", "member")
        with patch("services.ads.record_join", AsyncMock()) as record:
            await on_channel_membership_change(event)
        record.assert_awaited_once_with("https://t.me/+abc", 555, event.date)

    async def test_no_invite_link_is_ignored(self):
        from handlers.ads import on_channel_membership_change
        event = self.event(None, "left", "member")
        with patch("services.ads.record_join", AsyncMock()) as record:
            await on_channel_membership_change(event)
        record.assert_not_awaited()

    async def test_promotion_of_an_existing_member_is_not_a_join(self):
        from handlers.ads import on_channel_membership_change
        event = self.event("https://t.me/+abc", "member", "administrator")
        with patch("services.ads.record_join", AsyncMock()) as record:
            await on_channel_membership_change(event)
        record.assert_not_awaited()

    async def test_leaving_the_channel_is_not_a_join(self):
        from handlers.ads import on_channel_membership_change
        event = self.event("https://t.me/+abc", "member", "left")
        with patch("services.ads.record_join", AsyncMock()) as record:
            await on_channel_membership_change(event)
        record.assert_not_awaited()


class AdLaunchHandlerTests(unittest.IsolatedAsyncioTestCase):
    def campaign(self, **overrides):
        fields = dict(id=1, channel_username="news_channel", title="t", subtitle="", body="b",
            daily_limit=0, total_limit=0, impressions_total=0, joins_total=0, invite_link=None,
            status="active", started_at=utcnow(), ended_at=None)
        fields.update(overrides)
        return SimpleNamespace(**fields)

    async def test_launch_creates_and_stores_a_tracked_invite_link(self):
        from handlers.admin import cb_ad_launch
        campaign = self.campaign()
        bot = SimpleNamespace(create_chat_invite_link=AsyncMock(
            return_value=SimpleNamespace(invite_link="https://t.me/+abc123")))
        callback = SimpleNamespace(data="admin:ad:launch:1", from_user=SimpleNamespace(id=99),
            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()), bot=bot)
        with patch("services.ads.AdCampaignService.get", AsyncMock(return_value=campaign)), \
             patch("services.ads.AdCampaignService.set_status",
                   AsyncMock(return_value=ServiceResult(True, "SAVED"))) as set_status:
            await cb_ad_launch(callback)
        self.assertEqual(bot.create_chat_invite_link.await_args.kwargs["chat_id"], "@news_channel")
        set_status.assert_awaited_once_with(99, 1, "active", invite_link="https://t.me/+abc123")
        self.assertNotIn("⚠️", callback.message.answer.await_args.args[0])

    async def test_launch_without_admin_rights_falls_back_to_the_plain_link(self):
        from handlers.admin import cb_ad_launch
        from aiogram.exceptions import TelegramBadRequest
        campaign = self.campaign(id=2)
        bot = SimpleNamespace(create_chat_invite_link=AsyncMock(
            side_effect=TelegramBadRequest(method=SimpleNamespace(), message="not enough rights")))
        callback = SimpleNamespace(data="admin:ad:launch:2", from_user=SimpleNamespace(id=99),
            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()), bot=bot)
        with patch("services.ads.AdCampaignService.get", AsyncMock(return_value=campaign)), \
             patch("services.ads.AdCampaignService.set_status",
                   AsyncMock(return_value=ServiceResult(True, "SAVED"))) as set_status:
            await cb_ad_launch(callback)
        set_status.assert_awaited_once_with(99, 2, "active", invite_link=None)
        self.assertIn("⚠️", callback.message.answer.await_args.args[0])


class AdsSettingsHandlerTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        p = patch("config.ADMIN_IDS", {99})
        p.start()
        self.addCleanup(p.stop)
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        self.state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=99, user_id=99))

    def callback(self, data):
        return SimpleNamespace(data=data, from_user=SimpleNamespace(id=99), answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()))

    async def test_settings_screen_shows_the_current_value(self):
        from handlers.admin import cb_ads_settings
        callback = self.callback("admin:ads:settings")
        await cb_ads_settings(callback, self.state)
        self.assertIn("30 мин", callback.message.answer.await_args.args[0])

    async def test_preset_button_saves_the_value(self):
        from handlers.admin import cb_ads_settings_set
        from services.settings import bot_value
        callback = self.callback("admin:ads:settings:set:600")
        await cb_ads_settings_set(callback)
        self.assertEqual(await bot_value("ad_cooldown_seconds", None), 600)
        self.assertIn("10 мин", callback.message.edit_text.await_args.args[0])

    async def test_custom_value_is_saved_and_state_is_cleared(self):
        from handlers.admin import cb_ads_settings_custom, ads_settings_cooldown
        from services.settings import bot_value
        await cb_ads_settings_custom(self.callback("admin:ads:settings:custom"), self.state)
        self.assertIsNotNone(await self.state.get_state())
        message = SimpleNamespace(text="900", from_user=SimpleNamespace(id=99), answer=AsyncMock())
        await ads_settings_cooldown(message, self.state)
        self.assertEqual(await bot_value("ad_cooldown_seconds", None), 900)
        self.assertIsNone(await self.state.get_state())

    async def test_invalid_custom_value_keeps_the_state_and_does_not_save(self):
        from handlers.admin import cb_ads_settings_custom, ads_settings_cooldown
        from services.settings import bot_value
        await cb_ads_settings_custom(self.callback("admin:ads:settings:custom"), self.state)
        before = await bot_value("ad_cooldown_seconds", 1800)
        message = SimpleNamespace(text="not a number", from_user=SimpleNamespace(id=99), answer=AsyncMock())
        await ads_settings_cooldown(message, self.state)
        self.assertEqual(await bot_value("ad_cooldown_seconds", 1800), before)
        self.assertIsNotNone(await self.state.get_state())

    async def test_cancel_clears_state_without_saving(self):
        from handlers.admin import cb_ads_settings_custom, cb_ads_settings_cancel
        from services.settings import bot_value
        await cb_ads_settings_custom(self.callback("admin:ads:settings:custom"), self.state)
        before = await bot_value("ad_cooldown_seconds", 1800)
        await cb_ads_settings_cancel(self.callback("admin:ads:settings:cancel"), self.state)
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(await bot_value("ad_cooldown_seconds", 1800), before)


class AdChannelInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.storage.base import StorageKey
        self.state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=99, user_id=99))

    def message(self, text):
        return SimpleNamespace(text=text, from_user=SimpleNamespace(id=99), answer=AsyncMock())

    async def test_bare_username_without_at_is_rejected(self):
        from handlers.admin import ad_channel
        from handlers.admin import AdminAdCampaign
        await self.state.set_state(AdminAdCampaign.CHANNEL)
        message = self.message("anon_chat_news")
        await ad_channel(message, self.state)
        self.assertIn("с @", message.answer.await_args.args[0])
        self.assertEqual(await self.state.get_state(), AdminAdCampaign.CHANNEL.state)
        self.assertEqual(await self.state.get_data(), {})

    async def test_at_prefixed_username_is_accepted_and_stored_without_at(self):
        from handlers.admin import ad_channel, AdminAdCampaign
        await self.state.set_state(AdminAdCampaign.CHANNEL)
        message = self.message("@anon_chat_news")
        await ad_channel(message, self.state)
        self.assertEqual((await self.state.get_data())["channel_username"], "anon_chat_news")
        self.assertEqual(await self.state.get_state(), AdminAdCampaign.TITLE.state)

    async def test_too_short_username_after_at_is_rejected(self):
        from handlers.admin import ad_channel, AdminAdCampaign
        await self.state.set_state(AdminAdCampaign.CHANNEL)
        message = self.message("@abc")
        await ad_channel(message, self.state)
        self.assertIn("5–32 символа", message.answer.await_args.args[0])
        self.assertEqual(await self.state.get_state(), AdminAdCampaign.CHANNEL.state)

    async def test_non_ascii_username_is_rejected(self):
        # str.isalnum() accepts non-ASCII letters; Telegram usernames don't.
        from handlers.admin import ad_channel, AdminAdCampaign
        await self.state.set_state(AdminAdCampaign.CHANNEL)
        message = self.message("@приветик")
        await ad_channel(message, self.state)
        self.assertIn("5–32 символа", message.answer.await_args.args[0])
        self.assertEqual(await self.state.get_state(), AdminAdCampaign.CHANNEL.state)

    async def test_all_underscore_username_is_rejected(self):
        from handlers.admin import ad_channel, AdminAdCampaign
        await self.state.set_state(AdminAdCampaign.CHANNEL)
        message = self.message("@_____")
        await ad_channel(message, self.state)
        self.assertIn("5–32 символа", message.answer.await_args.args[0])
        self.assertEqual(await self.state.get_state(), AdminAdCampaign.CHANNEL.state)


if __name__ == "__main__":
    unittest.main(verbosity=2)

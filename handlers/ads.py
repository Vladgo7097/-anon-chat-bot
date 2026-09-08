"""Listens for real channel-join events attributed to a tracked ad invite link.

Telegram sends `chat_member` updates to a bot only for chats where the bot is
an admin. So this handler only ever fires for campaigns whose advertiser
granted admin rights and got a tracked invite link (see handlers/admin.py's
launch flow) — there is no path here that infers or estimates a join.
"""
from aiogram.types import ChatMemberUpdated
from handlers import ads_router

JOINED_STATUSES = {"member", "administrator", "creator"}


@ads_router.chat_member()
async def on_channel_membership_change(event: ChatMemberUpdated):
    if not event.invite_link:
        return
    if event.new_chat_member.status not in JOINED_STATUSES:
        return
    if event.old_chat_member.status in JOINED_STATUSES:
        return  # a status change between existing members, not a fresh join
    from services.ads import record_join
    await record_join(event.invite_link.invite_link, event.new_chat_member.user.id, event.date)

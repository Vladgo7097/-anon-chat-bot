import secrets
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from models.base import async_session
from models.user import User
from utils.time import utcnow


class UserRepository:
    async def get(self, telegram_id: int):
        async with async_session() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_or_create(self, telegram_id: int, username=None, first_name=None):
        for _ in range(10):
            try:
                async with async_session.begin() as session:
                    query = select(User).where(User.telegram_id == telegram_id)
                    user = await session.scalar(query)
                    now = utcnow()
                    if (user is not None and user.username == username
                            and (first_name is None or user.first_name == first_name)
                            and user.bot_blocked_at is None and user.last_online
                            and user.last_online >= now-timedelta(seconds=60)):
                        return user
                    if user is not None:
                        user = await session.scalar(query.with_for_update().execution_options(populate_existing=True))
                    if user is None:
                        user = User(telegram_id=telegram_id, anon_id="#" + secrets.token_hex(4).upper())
                        session.add(user)
                    user.username = username
                    if first_name is not None:
                        user.first_name = first_name
                    user.last_online = now
                    user.bot_blocked_at = None
                    await session.flush()
                return user
            except IntegrityError:
                # Concurrent /start or anon_id collision: retry in a new transaction.
                continue
        raise RuntimeError("Could not allocate anonymous profile")

    async def transition(self, telegram_id, operation):
        async with async_session.begin() as session:
            from repositories.chats import lock_matching
            await lock_matching(session)
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            return await operation(user, session)

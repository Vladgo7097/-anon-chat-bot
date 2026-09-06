"""Shared Premium access policy."""
from utils.time import utcnow


def premium_active(user, now=None):
    return bool(user and user.premium_expires and user.premium_expires > (now or utcnow()))


async def is_active(user_id, now=None):
    from repositories.users import UserRepository
    return premium_active(await UserRepository().get(user_id), now)

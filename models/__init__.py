# Every model module must be imported here so Base.metadata is complete no
# matter which entry point touches it first. bot.py's on_startup() calls
# Base.metadata.create_all() before `handlers` (and the services/repositories
# it pulls in) get imported -- and those imports are what used to register
# models/report.py and models/ban.py. Without this, create_all() silently
# skipped the `reports` and `bans` tables on a genuinely fresh database, and
# the very next step (running migrations/004_reports.sql, which assumes
# `reports` already exists) crashed on startup.
from models.base import Base
from models import user, chat, payment, operations, report, ban, mute, start, op, community

# The model modules are part of the public surface on purpose -- listing them
# here says the imports above are side-effect registration, not leftovers.
__all__ = ["Base", "user", "chat", "payment", "operations", "report", "ban", "mute", "start", "op", "community"]

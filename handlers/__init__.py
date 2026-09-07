from aiogram import Router
from importlib import import_module

start_router = Router(name="start")
menu_router = Router(name="menu")
search_router = Router(name="search")
chat_router = Router(name="chat")
profile_router = Router(name="profile")
settings_router = Router(name="settings")
report_router = Router(name="report")
premium_router = Router(name="premium")
admin_router = Router(name="admin")
stats_router = Router(name="stats")
help_router = Router(name="help")
reveal_router = Router(name="reveal")
rating_router = Router(name="rating")
ads_router = Router(name="ads")


def load_routers() -> tuple[Router, ...]:
    """Import handler modules so decorators populate routers before polling."""
    names = (
        "premium", "help", "start", "menu", "search", "profile", "settings", "report",
        "admin", "stats", "reveal", "rating", "chat", "ads",
    )
    for name in names:
        import_module(f"{__name__}.{name}")
    return tuple(globals()[f"{name}_router"] for name in names)

from aiogram import Router
from importlib import import_module

start_router = Router(name="start")
menu_router = Router(name="menu")
ideas_router = Router(name="ideas")
search_router = Router(name="search")
chat_router = Router(name="chat")
profile_router = Router(name="profile")
community_router = Router(name="community")
settings_router = Router(name="settings")
report_router = Router(name="report")
premium_router = Router(name="premium")
admin_router = Router(name="admin")
stats_router = Router(name="stats")
help_router = Router(name="help")
preview_router = Router(name="preview")
reveal_router = Router(name="reveal")
rating_router = Router(name="rating")
ads_router = Router(name="ads")
op_router = Router(name="op")
op_admin_router = Router(name="op_admin")


def load_routers() -> tuple[Router, ...]:
    """Import handler modules so decorators populate routers before polling."""
    names = (
        "premium",
        "help",
        "start",
        "menu",
        "ideas",
        "search",
        "profile",
        "community",
        "settings",
        "report",
        "admin",
        "op_admin",
        "stats",
        "reveal",
        "preview",
        "rating",
        "chat",
        "ads",
        "op",
    )
    for name in names:
        import_module(f"{__name__}.{name}")
    routers = tuple(globals()[f"{name}_router"] for name in names)
    return routers

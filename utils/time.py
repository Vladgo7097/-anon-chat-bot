from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Киев -- часовой пояс продукта: сутки в статистике заканчиваются не в
# UTC-полночь, а по календарю Europe/Kyiv.
KYIV = ZoneInfo("Europe/Kyiv")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def kyiv_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[начало, конец) текущих суток по Киеву, как UTC-моменты.

    Границы строятся в киевской зоне, а не сдвигом UTC-полуночи: в ночь
    перевода часов сутки длятся 25 часов, и календарный день обязан
    оставаться одним днём, каким его видит Киев."""
    local = (now or utcnow()).astimezone(KYIV)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)

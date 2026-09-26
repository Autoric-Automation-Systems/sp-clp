from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = ZoneInfo("UTC")

# On Windows the system time zone database is absent, so zoneinfo depends on the
# tzdata package being installed and bundled with the executable.
UNKNOWN_ZONE_WARNING = "Fuso horario desconhecido; usando UTC"


def resolve_zone(name: str | None) -> ZoneInfo:
    """Return the configured zone, falling back to UTC when it is missing or unknown."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return UTC


def is_known_zone(name: str | None) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def local_hour(hour_start: str, name: str | None) -> str:
    """Convert a stored UTC hour marker into ISO 8601 in the machine time zone."""
    moment = datetime.fromisoformat(hour_start)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(resolve_zone(name)).isoformat()

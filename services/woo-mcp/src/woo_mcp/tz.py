"""`period` / `date_from` / `date_to` → store-local date range → Woo query params (DESIGN §9.1)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver.exceptions import ToolError

Period = Literal["today", "yesterday", "last_7_days", "last_30_days"]

_PERIOD_DAYS: dict[str, tuple[int, int]] = {
    # (days back for "from", days back for "to"), both inclusive
    "today": (0, 0),
    "yesterday": (1, 1),
    "last_7_days": (6, 0),
    "last_30_days": (29, 0),
}


@dataclass(frozen=True)
class DateRange:
    start: date | None
    end: date | None


def store_tz(timezone_string: str, gmt_offset: float) -> tuple[tzinfo, str]:
    """WordPress stores either a named zone (`Asia/Kolkata`) or only a numeric UTC offset."""
    if timezone_string:
        try:
            return ZoneInfo(timezone_string), timezone_string
        except ZoneInfoNotFoundError, ValueError:
            pass
    offset = timedelta(hours=gmt_offset)
    sign = "-" if offset < timedelta(0) else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    name = f"UTC{sign}{minutes // 60:02d}:{minutes % 60:02d}"
    return timezone(offset, name), name


def resolve_range(
    period: Period | None,
    date_from: date | None,
    date_to: date | None,
    tz: tzinfo,
    now: datetime | None = None,
) -> DateRange | None:
    """`period` and explicit dates are mutually exclusive; no filter at all → None (all dates)."""
    if period and (date_from or date_to):
        raise ToolError("Use either period or date_from/date_to, not both")
    if period:
        today = (now or datetime.now(tz)).astimezone(tz).date()
        back_from, back_to = _PERIOD_DAYS[period]
        return DateRange(today - timedelta(days=back_from), today - timedelta(days=back_to))
    if date_from and date_to and date_from > date_to:
        raise ToolError("date_from must be on or before date_to")
    if date_from or date_to:
        return DateRange(date_from, date_to)
    return None


def woo_date_query(rng: DateRange | None) -> dict[str, str]:
    """Woo `after`/`before` are exclusive bounds in store-local time (`dates_are_gmt=false`)."""
    if rng is None:
        return {}
    query = {"dates_are_gmt": "false"}
    if rng.start:
        query["after"] = (datetime.combine(rng.start, time()) - timedelta(seconds=1)).isoformat()
    if rng.end:
        query["before"] = datetime.combine(rng.end + timedelta(days=1), time()).isoformat()
    return query

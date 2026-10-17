"""Fixed-window counters in `rate_counters`: login throttling and tool-call rate limits (SO-7)."""

from datetime import UTC, datetime, timedelta
from typing import Any


def window_start(window: timedelta, now: datetime | None = None) -> datetime:
    ts = (now or datetime.now(UTC)).timestamp()
    size = window.total_seconds()
    return datetime.fromtimestamp(ts - ts % size, UTC)


async def current(conn: Any, key: str, window: timedelta) -> int:
    value = await conn.fetchval(
        "SELECT count FROM rate_counters WHERE key = $1 AND window_start = $2", key, window_start(window)
    )
    return int(value or 0)


async def increment(conn: Any, key: str, window: timedelta, now: datetime | None = None) -> int:
    value = await conn.fetchval(
        "INSERT INTO rate_counters (key, window_start, count) VALUES ($1, $2, 1) "
        "ON CONFLICT (key, window_start) DO UPDATE SET count = rate_counters.count + 1 RETURNING count",
        key,
        window_start(window, now),
    )
    return int(value)

"""Per-credential `tools/call` rate limit (SO-7, DESIGN §5.11, D95).

A fixed 1-minute window in `rate_counters`, keyed `cred:<credential_id>`, shared by every replica through
Postgres. The limit is the key's own `rate_limit_per_min` when set, else the gateway default; a default of 0
turns rate limiting off for everyone.
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from wmg_gateway.db.repos import counters
from wmg_gateway.mcp.caller import Caller

WINDOW = timedelta(minutes=1)


def limit_for(caller: Caller, default: int) -> int:
    if default == 0:
        return 0
    return caller.rate_limit or default


async def retry_after(pool: Any, caller: Caller, default: int, now: datetime | None = None) -> int | None:
    """Counts this call; returns None if it is allowed, else the seconds until the window resets."""
    limit = limit_for(caller, default)
    if limit == 0:
        return None
    now = now or datetime.now(UTC)
    async with pool.acquire() as conn:
        count = await counters.increment(conn, f"cred:{caller.credential_id}", WINDOW, now)
    if count <= limit:
        return None
    resets = counters.window_start(WINDOW, now) + WINDOW
    return max(1, math.ceil((resets - now).total_seconds()))

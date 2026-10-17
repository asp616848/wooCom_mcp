"""Leader housekeeping (DESIGN §5.12, D60, D95): every minute, delete `rate_counters` windows older than
1 hour; once a day in the 03:00 UTC hour, delete audit rows older than 30 days.

Counters keep 1 hour, not the 10 minutes DESIGN first said: login lockouts share the table (15-minute window).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import anyio

from wmg_common.logging import get_logger

log = get_logger(__name__)

INTERVAL_S = 60.0
COUNTER_TTL = timedelta(hours=1)
AUDIT_RETENTION = timedelta(days=30)
AUDIT_HOUR_UTC = 3


class Housekeeping:
    def __init__(self, pool: Any, interval_s: float = INTERVAL_S) -> None:
        self._pool = pool
        self._interval_s = interval_s
        self._audit_pruned_on: date | None = None

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # one bad tick must not stop the loop
                log.error("housekeeping tick failed", error=type(exc).__name__, exc_info=exc)
            await anyio.sleep(self._interval_s)

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM rate_counters WHERE window_start < $1", now - COUNTER_TTL
            )
            if (counters := _rows(status)) > 0:
                log.info("rate counters pruned", rows=counters)
            if now.hour == AUDIT_HOUR_UTC and self._audit_pruned_on != now.date():
                status = await conn.execute("DELETE FROM audit_log WHERE ts < $1", now - AUDIT_RETENTION)
                self._audit_pruned_on = now.date()
                log.info("audit retention applied", rows=_rows(status), days=AUDIT_RETENTION.days)


def _rows(status: str) -> int:
    """asyncpg's command tag, e.g. 'DELETE 12'."""
    return int(status.rsplit(" ", 1)[-1])

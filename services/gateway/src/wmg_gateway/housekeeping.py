"""Leader housekeeping (DESIGN §5.12, D60, D95): every minute, delete `rate_counters` windows older than
1 hour; once a day in the 03:00 UTC hour, delete audit rows older than 30 days. OAuth leftovers go every
tick too: expired pending requests and codes, tokens a week past expiry or revocation, and registered apps
nobody ever approved after 7 days (D98).

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
OAUTH_KEEP = timedelta(days=7)


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
            await self._oauth(conn, now)
            if now.hour == AUDIT_HOUR_UTC and self._audit_pruned_on != now.date():
                status = await conn.execute("DELETE FROM audit_log WHERE ts < $1", now - AUDIT_RETENTION)
                self._audit_pruned_on = now.date()
                log.info("audit retention applied", rows=_rows(status), days=AUDIT_RETENTION.days)

    @staticmethod
    async def _oauth(conn: Any, now: datetime) -> None:
        stale = now - OAUTH_KEEP
        await conn.execute("DELETE FROM oauth_requests WHERE expires_at < $1", now)
        await conn.execute("DELETE FROM oauth_codes WHERE expires_at < $1", stale)
        await conn.execute(
            "DELETE FROM oauth_tokens WHERE expires_at < $1 OR revoked_at < $1",
            stale,
        )
        await conn.execute(
            "DELETE FROM oauth_clients c WHERE c.created_at < $1 AND NOT EXISTS "
            "(SELECT 1 FROM oauth_consents k WHERE k.client_id = c.client_id)",
            stale,
        )


def _rows(status: str) -> int:
    """asyncpg's command tag, e.g. 'DELETE 12'."""
    return int(status.rsplit(" ", 1)[-1])

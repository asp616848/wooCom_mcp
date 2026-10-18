"""SO-7 rate limit (D95) and leader housekeeping (D60, D95), with a fake connection."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from wmg_gateway.housekeeping import Housekeeping
from wmg_gateway.mcp.caller import Caller
from wmg_gateway.ratelimit.ratelimit import limit_for, retry_after

pytestmark = pytest.mark.anyio


def caller(rate_limit: int | None = None) -> Caller:
    return Caller("u1", "rohan@example.com", "admin", "api_key", "k1", rate_limit)


class FakeConn:
    def __init__(self) -> None:
        self.counts: dict[tuple[str, datetime], int] = {}
        self.executed: list[tuple[str, datetime]] = []

    async def fetchval(self, sql: str, key: str, window_start: datetime) -> int:
        self.counts[(key, window_start)] = self.counts.get((key, window_start), 0) + 1
        return self.counts[(key, window_start)]

    async def execute(self, sql: str, cutoff: datetime) -> str:
        self.executed.append((sql, cutoff))
        return "DELETE 3"


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeConn]:
        yield self.conn


@pytest.mark.parametrize(
    ("default", "own", "expected"),
    [(120, None, 120), (120, 5, 5), (0, 5, 0), (0, None, 0), (10, 500, 500)],
)
def test_limit_for(default: int, own: int | None, expected: int) -> None:
    assert limit_for(caller(own), default) == expected


async def test_retry_after_counts_per_credential_per_minute() -> None:
    pool = FakePool()
    now = datetime(2026, 10, 18, 9, 30, 12, 500000, tzinfo=UTC)
    assert [await retry_after(pool, caller(2), 120, now) for _ in range(3)] == [None, None, 48]
    assert list(pool.conn.counts) == [("cred:k1", datetime(2026, 10, 18, 9, 30, tzinfo=UTC))]
    # the next minute is a fresh window
    assert await retry_after(pool, caller(2), 120, now + timedelta(seconds=48)) is None
    # just before the reset the answer is never 0 seconds
    late = datetime(2026, 10, 18, 9, 31, 59, 900000, tzinfo=UTC)
    for _ in range(2):
        await retry_after(pool, caller(2), 120, late)
    assert await retry_after(pool, caller(2), 120, late) == 1


async def test_retry_after_off_touches_nothing() -> None:
    pool = FakePool()
    assert await retry_after(pool, caller(1), 0) is None
    assert pool.conn.counts == {}


async def test_housekeeping_counters_every_tick_audit_once_a_day_at_3() -> None:
    pool = FakePool()
    hk = Housekeeping(pool)
    two = datetime(2026, 10, 18, 2, 59, tzinfo=UTC)
    await hk.tick(two)
    rest = [e for e in pool.conn.executed if "oauth_" not in e[0]]
    assert rest == [("DELETE FROM rate_counters WHERE window_start < $1", two - timedelta(hours=1))]
    oauth = [sql.split()[2] for sql, _ in pool.conn.executed if "oauth_" in sql]  # DELETE FROM <table>
    assert oauth == ["oauth_requests", "oauth_codes", "oauth_tokens", "oauth_clients"]  # D98: every tick
    three = datetime(2026, 10, 18, 3, 0, 30, tzinfo=UTC)
    await hk.tick(three)
    await hk.tick(three + timedelta(minutes=1))  # same day: audit pruned once
    audit = [cutoff for sql, cutoff in pool.conn.executed if "audit_log" in sql]
    assert audit == [three - timedelta(days=30)]
    await hk.tick(three + timedelta(days=1))
    assert len([sql for sql, _ in pool.conn.executed if "audit_log" in sql]) == 2


def test_caller_from_claims_reads_the_key_limit() -> None:
    from mcp.server.auth.provider import AccessToken

    from wmg_gateway.mcp import caller as caller_module

    claims: dict[str, Any] = {
        "email": "ritika@example.com",
        "role": "member",
        "credential_type": "api_key",
        "credential_id": "k2",
        "rate_limit": 7,
    }
    token = AccessToken(token="t", client_id="k2", scopes=["mcp"], subject="u2", claims=claims)
    original = caller_module.get_access_token
    caller_module.get_access_token = lambda: token  # type: ignore[assignment]
    try:
        assert caller_module.caller_from(None).rate_limit == 7
    finally:
        caller_module.get_access_token = original

"""GET /api/insights (DESIGN §7.3, §16, SO-8): tool-call health per server and per tool over a window.

Computed from `audit_log` (`method = 'tools/call'`), so the numbers match an audit SQL count exactly.
Admins only.
"""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter

from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import AdminUser, Pool

router = APIRouter(prefix="/insights", tags=["insights"])

Window = Literal["1h", "24h", "7d"]
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
TOP_TOOLS = 10
TOP_FAILURES = 10


class ServerStats(ApiModel):
    server_slug: str
    calls: int
    errors: int
    error_rate: float
    p50_ms: int
    p95_ms: int
    outcomes: dict[str, int]


class ToolStats(ApiModel):
    exposed_name: str
    calls: int
    error_rate: float
    p95_ms: int


class FailureReason(ApiModel):
    server_slug: str
    outcome: str
    message: str
    count: int
    last_seen: str


class Insights(ApiModel):
    window: Window
    calls: int
    error_rate: float
    servers: list[ServerStats]
    tools: list[ToolStats]
    failures: list[FailureReason]


_STATS = (
    "count(*) AS calls, count(*) FILTER (WHERE outcome <> 'success') AS errors, "
    "COALESCE(avg((outcome <> 'success')::int), 0)::float AS error_rate, "
    "COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms), 0)::int AS p50_ms, "
    "COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)::int AS p95_ms"
)
_WHERE = "WHERE method = 'tools/call' AND ts >= now() - $1::interval"


@router.get("")
async def insights(_admin: AdminUser, pool: Pool, window: Window = "24h") -> Insights:
    since = WINDOWS[window]
    async with pool.acquire() as conn:
        total = await conn.fetchrow(f"SELECT {_STATS} FROM audit_log {_WHERE}", since)  # noqa: S608
        servers = await conn.fetch(
            f"SELECT COALESCE(server_slug, '(unknown)') AS server_slug, {_STATS} FROM audit_log {_WHERE} "  # noqa: S608
            "GROUP BY 1 ORDER BY calls DESC, 1",
            since,
        )
        outcomes = await conn.fetch(
            f"SELECT COALESCE(server_slug, '(unknown)') AS server_slug, outcome::text, count(*) AS n "  # noqa: S608
            f"FROM audit_log {_WHERE} GROUP BY 1, 2",
            since,
        )
        tools = await conn.fetch(
            f"SELECT COALESCE(exposed_name, tool_name, '(unknown)') AS exposed_name, {_STATS} "  # noqa: S608
            f"FROM audit_log {_WHERE} GROUP BY 1 ORDER BY calls DESC, 1 LIMIT {TOP_TOOLS}",
            since,
        )
        failures = await conn.fetch(
            "SELECT COALESCE(server_slug, '(unknown)') AS server_slug, outcome::text AS outcome, "  # noqa: S608
            "COALESCE(error_message, outcome::text) AS message, count(*) AS count, "
            "to_char(max(ts) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS last_seen "
            f"FROM audit_log {_WHERE} AND outcome <> 'success' "
            f"GROUP BY 1, 2, 3 ORDER BY count DESC, last_seen DESC LIMIT {TOP_FAILURES}",
            since,
        )
    by_server: dict[str, dict[str, int]] = {}
    for row in outcomes:
        by_server.setdefault(row["server_slug"], {})[row["outcome"]] = row["n"]
    return Insights(
        window=window,
        calls=total["calls"],
        error_rate=total["error_rate"],
        servers=[ServerStats(**dict(r), outcomes=by_server.get(r["server_slug"], {})) for r in servers],
        tools=[ToolStats(**{k: r[k] for k in ToolStats.model_fields}) for r in tools],
        failures=[FailureReason(**dict(r)) for r in failures],
    )

"""Upstream health (DESIGN §3.4, §5.6, §5.8, D36).

The leader probes every enabled server each `HEALTH_INTERVAL_MS` (timeout `HEALTH_TIMEOUT_MS`): era detection
+ `tools/list`. Results are written to `mcp_servers` (status, era, errors) and `server_tools` (only when the
tool list's SHA-256 changes). A NOTIFY on `wmg_events` makes every replica reload its catalog.
"""

import hashlib
import json
import uuid
from typing import Any

import anyio

from wmg_common.logging import get_logger
from wmg_gateway.catalog.catalog import CatalogHolder, ServerEntry
from wmg_gateway.catalog.naming import exposed_names
from wmg_gateway.events.bus import notify
from wmg_gateway.upstream.manager import ProbeResult, UpstreamError, UpstreamManager, categorize

log = get_logger(__name__)


def tools_hash(probe: ProbeResult) -> str:
    canonical = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in probe.tools]
    canonical.sort(key=lambda t: t["name"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def record_success(conn: Any, server_id: str, slug: str, probe: ProbeResult) -> bool:
    """Persist a successful probe. Returns True if anything the catalog depends on changed (NOTIFY sent)."""
    sid = uuid.UUID(server_id)
    async with conn.transaction():
        before = await conn.fetchrow(
            "SELECT status, protocol_era, discover_result FROM mcp_servers WHERE id = $1 FOR UPDATE", sid
        )
        if before is None:  # deleted meanwhile
            return False
        await conn.execute(
            "UPDATE mcp_servers SET status = 'connected', last_checked_at = now(), last_ok_at = now(), "
            "last_error_code = NULL, last_error = NULL, protocol_era = $2, discover_result = $3, "
            "era_verified_at = now(), server_info = $4 WHERE id = $1",
            sid,
            probe.era,
            probe.discover_result,
            probe.server_info,
        )
        digest = tools_hash(probe)
        stored = await conn.fetchval("SELECT tools_hash FROM server_tools_meta WHERE server_id = $1", sid)
        tools_changed = stored != digest
        if tools_changed:
            names = exposed_names(slug, [t.name for t in probe.tools])
            await conn.execute("DELETE FROM server_tools WHERE server_id = $1", sid)
            await conn.executemany(
                "INSERT INTO server_tools (server_id, name, exposed_name, title, description, input_schema, "
                "output_schema, annotations) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                [
                    (
                        sid,
                        t.name,
                        names[t.name],
                        t.title,
                        t.description,
                        t.input_schema,
                        t.output_schema,
                        t.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                        if t.annotations
                        else None,
                    )
                    for t in probe.tools
                ],
            )
            await conn.execute(
                "INSERT INTO server_tools_meta (server_id, tools_hash, updated_at) VALUES ($1, $2, now()) "
                "ON CONFLICT (server_id) DO UPDATE SET tools_hash = EXCLUDED.tools_hash, updated_at = now()",
                sid,
                digest,
            )
        changed = (
            tools_changed
            or before["status"] != "connected"
            or before["protocol_era"] != probe.era
            or before["discover_result"] != probe.discover_result
        )
        if changed:
            await notify(conn, "catalog_changed" if tools_changed else "server_status", server_id)
    return bool(changed)


async def record_failure(conn: Any, server_id: str, error: UpstreamError) -> bool:
    """Mark a server unreachable. Returns True if its status flipped (NOTIFY sent)."""
    async with conn.transaction():
        sid = uuid.UUID(server_id)
        previous = await conn.fetchval("SELECT status FROM mcp_servers WHERE id = $1 FOR UPDATE", sid)
        if previous is None:
            return False
        await conn.execute(
            "UPDATE mcp_servers SET status = 'unreachable', last_checked_at = now(), last_error_code = $2, "
            "last_error = $3 WHERE id = $1",
            sid,
            error.code,
            error.message[:500],
        )
        flipped = previous != "unreachable"
        if flipped:
            await notify(conn, "server_status", server_id)
    return bool(flipped)


async def mark_unreachable(pool: Any, server: ServerEntry, error: UpstreamError) -> None:
    """On a transport-level call failure (DESIGN §5.7 step 9): hide the server until the next good probe."""
    try:
        async with pool.acquire() as conn:
            if await record_failure(conn, server.id, error):
                log.warning("upstream marked unreachable", slug=server.slug, code=error.code)
    except Exception as exc:
        log.error("could not mark upstream unreachable", slug=server.slug, error=type(exc).__name__)


class HealthChecker:
    def __init__(
        self,
        pool: Any,
        manager: UpstreamManager,
        catalog: CatalogHolder,
        interval_s: float,
        timeout_s: float,
    ) -> None:
        self._pool = pool
        self._manager = manager
        self._catalog = catalog
        self._interval_s = interval_s
        self._timeout_s = timeout_s

    async def run_forever(self) -> None:
        log.info("health loop started", interval_s=self._interval_s)
        while True:
            started = anyio.current_time()
            try:
                await self.tick()
            except Exception as exc:  # one bad tick must not stop the loop
                log.error("health tick failed", error=type(exc).__name__, exc_info=exc)
            await anyio.sleep(max(0.0, self._interval_s - (anyio.current_time() - started)))

    async def tick(self) -> None:
        servers = [s for s in self._catalog.current.servers.values() if s.enabled]
        async with anyio.create_task_group() as tg:
            for server in servers:
                tg.start_soon(self.check, server)

    async def check(self, server: ServerEntry) -> bool:
        """Probe one server and persist the outcome. Returns True if it is healthy."""
        try:
            with anyio.fail_after(self._timeout_s):
                probe = await self._manager.probe(server)
        except TimeoutError:
            outcome: ProbeResult | UpstreamError = UpstreamError(
                "TIMEOUT", f"No answer within {int(self._timeout_s * 1000)} ms"
            )
        except UpstreamError as exc:
            outcome = exc
        except Exception as exc:
            outcome = categorize(exc)
        else:
            outcome = probe
        async with self._pool.acquire() as conn:
            if isinstance(outcome, ProbeResult):
                if await record_success(conn, server.id, server.slug, outcome):
                    log.info("upstream healthy", slug=server.slug, era=outcome.era, tools=len(outcome.tools))
                return True
            if await record_failure(conn, server.id, outcome):
                log.warning(
                    "upstream unreachable", slug=server.slug, code=outcome.code, error=outcome.message
                )
            return False

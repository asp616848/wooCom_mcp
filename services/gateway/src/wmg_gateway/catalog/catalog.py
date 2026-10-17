"""In-memory catalog (DESIGN §5.8, D35): servers, tools and policies loaded from Postgres as one immutable
snapshot.

`tools/list` is served from here, never by calling upstreams. `CatalogHolder.reload()` builds a new snapshot
and swaps the reference in one assignment, so readers never see a half-built catalog.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import Any

import mcp_types as types
from prometheus_client import Gauge

from wmg_gateway.catalog.naming import exposed_description, exposed_title


@dataclass(frozen=True)
class ServerEntry:
    id: str
    slug: str
    name: str
    url: str
    auth_type: str
    auth_header_name: str | None
    credential_enc: str | None
    enabled: bool
    timeout_ms: int
    source: str
    status: str
    last_error_code: str | None
    last_error: str | None
    protocol_era: str | None
    discover_result: dict[str, Any] | None
    era_verified_at: datetime | None
    updated_at: datetime

    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000

    def config_key(self) -> tuple[Any, ...]:
        """Changes whenever the pooled HTTP client for this server must be rebuilt."""
        return (self.url, self.auth_type, self.auth_header_name, self.credential_enc, self.timeout_ms)


@dataclass(frozen=True)
class ToolEntry:
    server: ServerEntry
    original: str
    exposed: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    annotations: dict[str, Any] | None
    admin_only: bool

    @cached_property
    def upstream_tool(self) -> types.Tool:
        """The tool as the upstream listed it (used to prime per-call client sessions)."""
        return types.Tool.model_validate(
            {
                "name": self.original,
                "title": self.title,
                "description": self.description,
                "inputSchema": self.input_schema,
                "outputSchema": self.output_schema,
                "annotations": self.annotations,
            }
        )

    @cached_property
    def exposed_tool(self) -> types.Tool:
        return types.Tool.model_validate(
            {
                "name": self.exposed,
                "title": exposed_title(self.server.name, self.title, self.original),
                "description": exposed_description(self.server.slug, self.description),
                "inputSchema": self.input_schema,
                "outputSchema": self.output_schema,
                "annotations": self.annotations,
            }
        )

    @property
    def read_only_or_idempotent(self) -> bool:
        hints = self.annotations or {}
        return bool(hints.get("readOnlyHint") or hints.get("idempotentHint"))


def is_live(server: ServerEntry) -> bool:
    return server.enabled and server.status == "connected"


@dataclass(frozen=True)
class Catalog:
    servers: dict[str, ServerEntry] = field(default_factory=dict)
    tools_by_exposed: dict[str, ToolEntry] = field(default_factory=dict)
    tools_by_server: dict[str, list[ToolEntry]] = field(default_factory=dict)

    def by_exposed(self, name: str) -> ToolEntry | None:
        return self.tools_by_exposed.get(name)

    def server_by_slug(self, slug: str) -> ServerEntry | None:
        return next((s for s in self.servers.values() if s.slug == slug), None)

    def visible_tools(self, role: str) -> list[ToolEntry]:
        """Sorted by exposed name: live servers only; admin-only tools for admins only."""
        return [
            t
            for name, t in sorted(self.tools_by_exposed.items())
            if is_live(t.server) and (not t.admin_only or role == "admin")
        ]

    def fingerprint(self) -> str:
        """Changes when any caller's visible tool list could change (drives tools/list_changed)."""
        rows = sorted(
            f"{t.exposed}|{t.admin_only}" for t in self.tools_by_exposed.values() if is_live(t.server)
        )
        return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def server_entry(row: Any) -> ServerEntry:
    return ServerEntry(
        id=str(row["id"]),
        slug=row["slug"],
        name=row["name"],
        url=row["url"],
        auth_type=row["auth_type"],
        auth_header_name=row["auth_header_name"],
        credential_enc=row["credential_enc"],
        enabled=row["enabled"],
        timeout_ms=row["timeout_ms"],
        source=row["source"],
        status=row["status"],
        last_error_code=row["last_error_code"],
        last_error=row["last_error"],
        protocol_era=row["protocol_era"],
        discover_result=row["discover_result"],
        era_verified_at=row["era_verified_at"],
        updated_at=row["updated_at"],
    )


async def load(conn: Any) -> Catalog:
    servers = {s.id: s for s in map(server_entry, await conn.fetch("SELECT * FROM mcp_servers"))}
    policies = {
        (str(r["server_id"]), r["tool_name"]): r["admin_only"]
        for r in await conn.fetch("SELECT server_id, tool_name, admin_only FROM tool_policies")
    }
    by_exposed: dict[str, ToolEntry] = {}
    by_server: dict[str, list[ToolEntry]] = {sid: [] for sid in servers}
    for r in await conn.fetch("SELECT * FROM server_tools ORDER BY server_id, name"):
        server = servers[str(r["server_id"])]
        entry = ToolEntry(
            server=server,
            original=r["name"],
            exposed=r["exposed_name"],
            title=r["title"],
            description=r["description"],
            input_schema=r["input_schema"],
            output_schema=r["output_schema"],
            annotations=r["annotations"],
            admin_only=policies.get((server.id, r["name"]), False),
        )
        by_exposed[entry.exposed] = entry
        by_server[server.id].append(entry)
    return Catalog(servers=servers, tools_by_exposed=by_exposed, tools_by_server=by_server)


UPSTREAM_UP = Gauge("wmg_upstream_up", "1 if the upstream is enabled and connected", ["server"])
UPSTREAM_TOOLS = Gauge("wmg_upstream_tools", "Tools the upstream exposes", ["server"])


def export_metrics(catalog: Catalog) -> None:
    """Gauges follow the snapshot on every replica (SO-8); deleted servers drop out of the scrape."""
    UPSTREAM_UP.clear()
    UPSTREAM_TOOLS.clear()
    for server in catalog.servers.values():
        UPSTREAM_UP.labels(server.slug).set(1 if server.enabled and server.status == "connected" else 0)
        UPSTREAM_TOOLS.labels(server.slug).set(len(catalog.tools_by_server.get(server.id, [])))


class CatalogHolder:
    def __init__(self) -> None:
        self.current = Catalog()
        self._fingerprint = self.current.fingerprint()

    async def reload(self, pool: Any) -> bool:
        """Swap in a fresh snapshot; True if the visible-tool fingerprint changed."""
        async with pool.acquire() as conn:
            catalog = await load(conn)
        self.current = catalog
        export_metrics(catalog)
        fingerprint = catalog.fingerprint()
        changed = fingerprint != self._fingerprint
        self._fingerprint = fingerprint
        return changed

"""Upstream MCP servers (DESIGN §7.3, §7.2): list, tools, tool policies (SO-2), test connection, create,
update, delete.

Credentials are write-only: they are encrypted on the way in (AES-GCM, AAD = server id) and never returned.
URLs pass the SSRF policy (§8.4). Every change NOTIFYs `catalog_changed`, so all replicas reload; a create or
config change also probes the server right away so the response shows a real status.
"""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import anyio
from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator

from wmg_common.errors import AppError
from wmg_common.logging import get_logger
from wmg_gateway.catalog.catalog import server_entry
from wmg_gateway.db.repos.users import User
from wmg_gateway.events.bus import notify
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import AdminUser, CurrentUser, Pool
from wmg_gateway.runtime import Runtime
from wmg_gateway.security import ssrf
from wmg_gateway.upstream.health import HealthChecker
from wmg_gateway.upstream.manager import UpstreamConfig, UpstreamError

log = get_logger(__name__)
router = APIRouter(prefix="/servers", tags=["servers"])

AuthType = Literal["none", "bearer", "header"]
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,29}$")]
TEST_TIMEOUT_S = 10.0
PROBE_TIMEOUT_S = 5.0  # first probe right after create/update (a cold legacy server needs 3 round trips)
_HEADER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "accept",
        "cookie",
        "mcp-protocol-version",
        "mcp-session-id",
        "mcp-method",
        "mcp-name",
    }
)


def _check_header_name(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not _HEADER_NAME.match(value)
        or value.lower() in _RESERVED_HEADERS
        or value.lower().startswith("mcp-param-")
    ):
        raise ValueError("not an allowed header name")
    return value


class ServerFields(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    auth_type: AuthType | None = None
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, min_length=1, max_length=4096)
    timeout_ms: int | None = Field(default=None, ge=1000, le=120000)
    enabled: bool | None = None

    @field_validator("auth_header_name")
    @classmethod
    def _header(cls, v: str | None) -> str | None:
        return _check_header_name(v)


class ServerCreate(ServerFields):
    slug: Slug
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    auth_type: AuthType = "none"

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        if "--" in v or v.endswith("-"):
            raise ValueError("no '--' and no trailing '-'")
        return v

    @model_validator(mode="after")
    def _auth_consistent(self) -> ServerCreate:
        if self.auth_type == "header" and not self.auth_header_name:
            raise ValueError("authHeaderName is required for authType 'header'")
        if self.auth_type != "none" and not self.credential:
            raise ValueError("credential is required unless authType is 'none'")
        return self


class ServerPatch(ServerFields):
    pass


class ServerTest(ApiModel):
    url: str = Field(min_length=1, max_length=2048)
    auth_type: AuthType = "none"
    auth_header_name: str | None = None
    credential: str | None = Field(default=None, max_length=4096)
    timeout_ms: int | None = Field(default=None, ge=1000, le=120000)

    @field_validator("auth_header_name")
    @classmethod
    def _header(cls, v: str | None) -> str | None:
        return _check_header_name(v)


class TestResult(ApiModel):
    ok: bool
    era: str | None = None
    tool_count: int | None = None
    error_code: str | None = None
    error: str | None = None


class ServerOut(ApiModel):
    """Member view (DESIGN §7.2): no URL, auth or error details."""

    id: uuid.UUID
    slug: str
    name: str
    status: Literal["connected", "unreachable", "disabled", "unknown"]
    enabled: bool
    tool_count: int
    source: str
    protocol_era: str | None
    last_checked_at: datetime | None


class AdminServerOut(ServerOut):
    url: str
    auth_type: AuthType
    auth_header_name: str | None
    has_credential: bool
    timeout_ms: int
    last_error_code: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ServersEnvelope(ApiModel):
    servers: list[AdminServerOut | ServerOut]


class ServerEnvelope(ApiModel):
    server: AdminServerOut | ServerOut


class ToolOut(ApiModel):
    name: str
    exposed_name: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    admin_only: bool


class ToolsEnvelope(ApiModel):
    tools: list[ToolOut]


class PolicyIn(ApiModel):
    admin_only: bool


class PolicyOut(ApiModel):
    server_id: uuid.UUID
    tool_name: str
    admin_only: bool
    updated_at: datetime


class PolicyEnvelope(ApiModel):
    policy: PolicyOut


_LIST_SQL = (
    "SELECT s.*, "
    "(SELECT count(*) FROM server_tools t LEFT JOIN tool_policies p "
    " ON p.server_id = t.server_id AND p.tool_name = t.name "
    " WHERE t.server_id = s.id AND ($1 OR NOT COALESCE(p.admin_only, false))) AS tool_count "
    "FROM mcp_servers s "
)


def _out(row: Any, admin: bool) -> ServerOut:
    member = {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "status": "disabled" if not row["enabled"] else row["status"],
        "enabled": row["enabled"],
        "tool_count": row["tool_count"],
        "source": row["source"],
        "protocol_era": row["protocol_era"],
        "last_checked_at": row["last_checked_at"],
    }
    if not admin:
        return ServerOut(**member)
    return AdminServerOut(
        **member,
        url=row["url"],
        auth_type=row["auth_type"],
        auth_header_name=row["auth_header_name"],
        has_credential=row["credential_enc"] is not None,
        timeout_ms=row["timeout_ms"],
        last_error_code=row["last_error_code"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _fetch_out(conn: Any, server_id: uuid.UUID, user: User) -> ServerOut:
    admin = user.role == "admin"
    row = await conn.fetchrow(_LIST_SQL + "WHERE s.id = $2", admin, server_id)
    if row is None:
        raise AppError(404, "NOT_FOUND", "Server not found")
    return _out(row, admin)


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


async def _ssrf_check(rt: Runtime, url: str) -> None:
    try:
        await rt.check_url(url)
    except ssrf.SsrfBlockedError as exc:
        raise AppError(422, "SSRF_BLOCKED", str(exc)) from None


async def _probe_now(rt: Runtime, conn: Any, server_id: uuid.UUID) -> None:
    """Probe a just-saved server and record the outcome (bounded by the health timeout)."""
    row = await conn.fetchrow("SELECT * FROM mcp_servers WHERE id = $1", server_id)
    if row is None or not row["enabled"]:
        return
    timeout_s = max(rt.settings.health_timeout_ms / 1000, PROBE_TIMEOUT_S)
    checker = HealthChecker(rt.pool, rt.manager, rt.catalog, rt.settings.health_interval_ms / 1000, timeout_s)
    await checker.check(server_entry(row))


@router.get("")
async def list_servers(user: CurrentUser, pool: Pool) -> ServersEnvelope:
    admin = user.role == "admin"
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_SQL + "ORDER BY s.slug", admin)
    return ServersEnvelope(servers=[_out(r, admin) for r in rows])


@router.get("/{server_id}/tools")
async def list_server_tools(server_id: uuid.UUID, user: CurrentUser, pool: Pool) -> ToolsEnvelope:
    admin = user.role == "admin"
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM mcp_servers WHERE id = $1", server_id):
            raise AppError(404, "NOT_FOUND", "Server not found")
        rows = await conn.fetch(
            "SELECT t.name, t.exposed_name, t.title, t.description, t.input_schema, "
            "COALESCE(p.admin_only, false) AS admin_only FROM server_tools t LEFT JOIN tool_policies p "
            "ON p.server_id = t.server_id AND p.tool_name = t.name "
            "WHERE t.server_id = $1 AND ($2 OR NOT COALESCE(p.admin_only, false)) ORDER BY t.exposed_name",
            server_id,
            admin,
        )
    return ToolsEnvelope(tools=[ToolOut.model_validate(dict(r)) for r in rows])


@router.put("/{server_id}/tools/{tool_name}/policy")
async def set_tool_policy(
    server_id: uuid.UUID, tool_name: str, body: PolicyIn, admin: AdminUser, pool: Pool
) -> PolicyEnvelope:
    """SO-2: mark one tool admin-only (or open it to members). Every replica reloads on the NOTIFY."""
    async with pool.acquire() as conn, conn.transaction():
        slug = await conn.fetchval("SELECT slug FROM mcp_servers WHERE id = $1", server_id)
        if slug is None:
            raise AppError(404, "NOT_FOUND", "Server not found")
        if not await conn.fetchval(
            "SELECT 1 FROM server_tools WHERE server_id = $1 AND name = $2", server_id, tool_name
        ):
            raise AppError(404, "NOT_FOUND", "Tool not found")
        row = await conn.fetchrow(
            "INSERT INTO tool_policies (server_id, tool_name, admin_only, updated_by) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (server_id, tool_name) DO UPDATE SET admin_only = EXCLUDED.admin_only, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() "
            "RETURNING server_id, tool_name, admin_only, updated_at",
            server_id,
            tool_name,
            body.admin_only,
            admin.id,
        )
        await notify(conn, "catalog_changed", str(server_id))
    log.info("tool policy set", slug=slug, tool=tool_name, admin_only=body.admin_only, by=str(admin.id))
    return PolicyEnvelope(policy=PolicyOut.model_validate(dict(row)))


@router.post("/test")
async def test_server(body: ServerTest, _admin: AdminUser, request: Request) -> TestResult:
    rt = _runtime(request)
    await _ssrf_check(rt, body.url)
    config = UpstreamConfig(
        url=body.url,
        auth_type=body.auth_type,
        auth_header_name=body.auth_header_name,
        credential=body.credential,
        timeout_s=(body.timeout_ms or rt.settings.default_tool_timeout_ms) / 1000,
    )
    try:
        with anyio.fail_after(TEST_TIMEOUT_S):
            probe = await rt.manager.probe_config(config)
    except TimeoutError:
        return TestResult(ok=False, error_code="TIMEOUT", error=f"No answer within {int(TEST_TIMEOUT_S)} s")
    except UpstreamError as exc:
        return TestResult(ok=False, error_code=exc.code, error=exc.message)
    return TestResult(ok=True, era=probe.era, tool_count=len(probe.tools))


@router.post("", status_code=201)
async def create_server(body: ServerCreate, admin: AdminUser, request: Request, pool: Pool) -> ServerEnvelope:
    rt = _runtime(request)
    await _ssrf_check(rt, body.url)
    server_id = uuid.uuid4()
    credential_enc = rt.cipher.encrypt(body.credential, str(server_id)) if body.credential else None
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT 1 FROM mcp_servers WHERE slug = $1", body.slug):
                raise AppError(409, "CONFLICT", f"Slug '{body.slug}' is already used")
            await conn.execute(
                "INSERT INTO mcp_servers "
                "(id, slug, name, url, auth_type, auth_header_name, credential_enc, enabled, "
                "timeout_ms, source, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'manual', $10)",
                server_id,
                body.slug,
                body.name.strip(),
                body.url,
                body.auth_type,
                body.auth_header_name if body.auth_type == "header" else None,
                credential_enc if body.auth_type != "none" else None,
                True if body.enabled is None else body.enabled,
                body.timeout_ms or rt.settings.default_tool_timeout_ms,
                admin.id,
            )
            await notify(conn, "catalog_changed", str(server_id))
        log.info("server created", slug=body.slug, by=str(admin.id))
        await _probe_now(rt, conn, server_id)
        return ServerEnvelope(server=await _fetch_out(conn, server_id, admin))


@router.patch("/{server_id}")
async def update_server(
    server_id: uuid.UUID, body: ServerPatch, admin: AdminUser, request: Request, pool: Pool
) -> ServerEnvelope:
    rt = _runtime(request)
    fields = body.model_fields_set
    if body.url is not None:
        await _ssrf_check(rt, body.url)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT * FROM mcp_servers WHERE id = $1 FOR UPDATE", server_id)
            if row is None:
                raise AppError(404, "NOT_FOUND", "Server not found")
            auth_type = body.auth_type or row["auth_type"]
            header = body.auth_header_name if "auth_header_name" in fields else row["auth_header_name"]
            credential_enc = row["credential_enc"]
            if "credential" in fields:  # string = set, null = clear, absent = keep
                credential_enc = (
                    rt.cipher.encrypt(body.credential, str(server_id)) if body.credential else None
                )
            if auth_type == "none":
                header, credential_enc = None, None
            elif auth_type == "header" and not header:
                raise AppError(422, "VALIDATION_FAILED", "authHeaderName is required for authType 'header'")
            elif auth_type == "bearer":
                header = None
            if auth_type != "none" and credential_enc is None:
                raise AppError(422, "VALIDATION_FAILED", "credential is required unless authType is 'none'")
            await conn.execute(
                "UPDATE mcp_servers SET name = $2, url = $3, auth_type = $4, auth_header_name = $5, "
                "credential_enc = $6, timeout_ms = $7, enabled = $8, updated_at = now() WHERE id = $1",
                server_id,
                (body.name or row["name"]).strip(),
                body.url or row["url"],
                auth_type,
                header,
                credential_enc,
                body.timeout_ms or row["timeout_ms"],
                row["enabled"] if body.enabled is None else body.enabled,
            )
            await notify(conn, "catalog_changed", str(server_id))
        reprobe = fields & {"url", "auth_type", "auth_header_name", "credential", "timeout_ms"} or (
            body.enabled is True and not row["enabled"]
        )
        if reprobe:  # the pooled client is rebuilt because the server's config key changed
            await _probe_now(rt, conn, server_id)
        log.info("server updated", slug=row["slug"], fields=sorted(fields - {"credential"}), by=str(admin.id))
        return ServerEnvelope(server=await _fetch_out(conn, server_id, admin))


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: uuid.UUID, admin: AdminUser, request: Request, pool: Pool) -> None:
    rt = _runtime(request)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow("SELECT slug, source FROM mcp_servers WHERE id = $1 FOR UPDATE", server_id)
        if row is None:
            raise AppError(404, "NOT_FOUND", "Server not found")
        if row["source"] in ("provisioned", "hosted"):
            raise AppError(409, "CONFLICT", "Delete the store/hosted server instead")
        await conn.execute("DELETE FROM mcp_servers WHERE id = $1", server_id)
        await notify(conn, "catalog_changed", str(server_id))
    await rt.manager.forget(str(server_id))
    log.info("server deleted", slug=row["slug"], by=str(admin.id))

"""Hosted MCP servers (DESIGN §14, SO-5, D94): GET/POST /api/hosted-servers, POST …/:id/retry, DELETE …/:id.

Admins only. POST records intent (202): an `mcp_servers` row (source 'hosted', disabled until deployed, its
in-cluster URL known upfront, a generated credential) plus a `hosted_deployments` row; the leader's
HostedReconciler deploys it with the `mcp-server` chart into `mcp-hosted`. Secret env values are encrypted
(AAD = server id) and never returned; only their names are.
"""

import json
import re
import secrets
import string
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator

from wmg_common.errors import AppError
from wmg_common.logging import get_logger
from wmg_gateway.events.bus import notify
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.api.servers import AuthType, Slug, _check_header_name
from wmg_gateway.http.deps import AdminUser, Pool
from wmg_gateway.provisioner.hosted import REMOVAL_FAILED, hosted_url
from wmg_gateway.runtime import Runtime

log = get_logger(__name__)
router = APIRouter(prefix="/hosted-servers", tags=["hosted"])

TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_LENGTH = 48
MAX_ENV = 20
# repository[:tag] — lowercase registry/path segments, an explicit tag (no digests, no implicit 'latest')
SEGMENT = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
IMAGE = re.compile(rf"^{SEGMENT}(?::[0-9]+)?(?:/{SEGMENT})*:[A-Za-z0-9_][A-Za-z0-9._-]{{0,127}}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
RESERVED_ENV = frozenset({"PORT"})  # plus every MCP_* name: the chart sets MCP_ALLOWED_HOSTS and MCP_AUTH_*

EnvMap = Annotated[dict[str, Annotated[str, Field(max_length=4096)]], Field(max_length=MAX_ENV)]


def _check_env(env: dict[str, str]) -> dict[str, str]:
    for name in env:
        if not ENV_NAME.match(name):
            raise ValueError(f"'{name}' is not a valid variable name")
        if name.upper() in RESERVED_ENV or name.upper().startswith("MCP_"):
            raise ValueError(f"'{name}' is set by the platform (PORT and MCP_* are reserved)")
    return env


class HostedCreate(ApiModel):
    slug: Slug
    name: str = Field(min_length=1, max_length=80)
    image: str = Field(min_length=3, max_length=255)
    port: int = Field(default=8080, ge=1024, le=65535)
    path: str = Field(default="/mcp", pattern=r"^/[A-Za-z0-9._~/-]{0,100}$")
    env: EnvMap = Field(default_factory=dict)
    secret_env: EnvMap = Field(default_factory=dict)
    size: Literal["small", "medium"] = "small"
    allow_internet: bool = False
    auth_type: AuthType = "bearer"
    auth_header_name: str | None = None

    @field_validator("slug")
    @classmethod
    def _slug_shape(cls, v: str) -> str:
        if "--" in v or v.endswith("-"):
            raise ValueError("no '--' and no trailing '-'")
        return v

    @field_validator("image")
    @classmethod
    def _image_shape(cls, v: str) -> str:
        if not IMAGE.match(v):
            raise ValueError("must be <repository>:<tag> (an explicit tag, no digest)")
        return v

    @field_validator("env", "secret_env")
    @classmethod
    def _env_names(cls, v: dict[str, str]) -> dict[str, str]:
        return _check_env(v)

    @field_validator("auth_header_name")
    @classmethod
    def _header(cls, v: str | None) -> str | None:
        return _check_header_name(v)

    @model_validator(mode="after")
    def _consistent(self) -> HostedCreate:
        if self.auth_type == "header" and not self.auth_header_name:
            raise ValueError("authHeaderName is required for authType 'header'")
        if overlap := sorted(set(self.env) & set(self.secret_env)):
            raise ValueError(f"set {', '.join(overlap)} in env or secretEnv, not both")
        return self


class HostedOut(ApiModel):
    server_id: uuid.UUID
    slug: str
    name: str
    image: str
    port: int
    path: str
    url: str
    env: dict[str, str]
    secret_env_keys: list[str]
    size: str
    allow_internet: bool
    auth_type: str
    auth_header_name: str | None
    deploy_status: Literal["deploying", "deployed", "failed", "removing"]
    deploy_reason: str | None
    attempt: int
    server_status: Literal["connected", "unreachable", "disabled", "unknown"]
    tool_count: int
    created_at: datetime
    deployed_at: datetime | None


class HostedListEnvelope(ApiModel):
    hosted: list[HostedOut]


class HostedEnvelope(ApiModel):
    hosted: HostedOut


_SELECT = (
    "SELECT h.*, s.slug, s.name, s.url, s.auth_type, s.auth_header_name, s.enabled, s.status, "
    "(SELECT count(*) FROM server_tools t WHERE t.server_id = s.id) AS tool_count "
    "FROM hosted_deployments h JOIN mcp_servers s ON s.id = h.server_id "
)


def _out(row: Any) -> HostedOut:
    return HostedOut(
        server_id=row["server_id"],
        slug=row["slug"],
        name=row["name"],
        image=row["image"],
        port=row["port"],
        path=row["path"],
        url=row["url"],
        env=row["env"] or {},
        secret_env_keys=list(row["secret_env_keys"]),
        size=row["size"],
        allow_internet=row["allow_internet"],
        auth_type=row["auth_type"],
        auth_header_name=row["auth_header_name"],
        deploy_status=row["deploy_status"],
        deploy_reason=row["deploy_reason"],
        attempt=row["attempt"],
        server_status="disabled" if not row["enabled"] else row["status"],
        tool_count=row["tool_count"],
        created_at=row["created_at"],
        deployed_at=row["deployed_at"],
    )


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


async def _locked(conn: Any, server_id: uuid.UUID) -> Any:
    row = await conn.fetchrow("SELECT * FROM hosted_deployments WHERE server_id = $1 FOR UPDATE", server_id)
    if row is None:
        raise AppError(404, "NOT_FOUND", "Hosted server not found")
    return row


async def _reload(conn: Any, server_id: uuid.UUID) -> HostedOut:
    return _out(await conn.fetchrow(_SELECT + "WHERE h.server_id = $1", server_id))


@router.get("")
async def list_hosted(_admin: AdminUser, pool: Pool) -> HostedListEnvelope:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT + "ORDER BY h.created_at")
    return HostedListEnvelope(hosted=[_out(r) for r in rows])


@router.post("", status_code=202)
async def create_hosted(body: HostedCreate, admin: AdminUser, request: Request, pool: Pool) -> HostedEnvelope:
    rt = _runtime(request)
    settings = rt.settings
    if not settings.provisioning_enabled():
        raise AppError(503, "PROVISIONING_DISABLED", "Hosted servers are not configured on this gateway")
    prefixes = settings.image_prefixes()
    if not any(body.image.startswith(p) for p in prefixes):
        raise AppError(422, "VALIDATION_FAILED", f"image: must start with one of {', '.join(prefixes)}")
    server_id = uuid.uuid4()
    sid = str(server_id)
    credential_enc = None
    if body.auth_type != "none":
        token = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))
        credential_enc = rt.cipher.encrypt(token, sid)
    secret_env_enc = rt.cipher.encrypt(json.dumps(body.secret_env), sid) if body.secret_env else None
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT 1 FROM mcp_servers WHERE slug = $1", body.slug):
                raise AppError(409, "CONFLICT", f"Slug '{body.slug}' is already used")
            await conn.execute(
                "INSERT INTO mcp_servers (id, slug, name, url, auth_type, auth_header_name, credential_enc, "
                "enabled, timeout_ms, source, created_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, false, $8, 'hosted', $9)",
                server_id,
                body.slug,
                body.name.strip(),
                hosted_url(body.slug, settings.hosted_namespace, body.port, body.path),
                body.auth_type,
                body.auth_header_name if body.auth_type == "header" else None,
                credential_enc,
                settings.default_tool_timeout_ms,
                admin.id,
            )
            await conn.execute(
                "INSERT INTO hosted_deployments (server_id, image, port, path, env, secret_env_enc, "
                "secret_env_keys, size, allow_internet, deploy_status, deploy_reason) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'deploying', 'queued')",
                server_id,
                body.image,
                body.port,
                body.path,
                body.env,
                secret_env_enc,
                sorted(body.secret_env),
                body.size,
                body.allow_internet,
            )
            await notify(conn, "catalog_changed", sid)  # listed as disabled until deployed
        log.info("hosted server requested", slug=body.slug, image=body.image, by=str(admin.id))
        return HostedEnvelope(hosted=await _reload(conn, server_id))


@router.post("/{server_id}/retry", status_code=202)
async def retry_hosted(server_id: uuid.UUID, admin: AdminUser, pool: Pool) -> HostedEnvelope:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _locked(conn, server_id)
            if row["deploy_status"] != "failed":
                raise AppError(409, "CONFLICT", "Only a failed deploy can be retried")
            if (row["deploy_reason"] or "").startswith(REMOVAL_FAILED):
                raise AppError(409, "CONFLICT", "Its removal failed: delete it again instead")
            await conn.execute(
                "UPDATE hosted_deployments SET deploy_status = 'deploying', deploy_reason = 'queued', "
                "attempt = attempt + 1, updated_at = now() WHERE server_id = $1",
                server_id,
            )
        log.info("hosted retry", server_id=str(server_id), attempt=row["attempt"] + 1, by=str(admin.id))
        return HostedEnvelope(hosted=await _reload(conn, server_id))


@router.delete("/{server_id}", status_code=202)
async def delete_hosted(server_id: uuid.UUID, admin: AdminUser, pool: Pool) -> HostedEnvelope:
    """Idempotent: deleting a server that is already being removed returns it unchanged. Its tools vanish
    at once (the server is disabled here); the reconciler then uninstalls it and deletes the rows."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _locked(conn, server_id)
            if row["deploy_status"] != "removing":
                # a new attempt gives the remove Job a fresh name and fences off in-flight reconciler writes
                await conn.execute(
                    "UPDATE hosted_deployments SET deploy_status = 'removing', deploy_reason = NULL, "
                    "attempt = attempt + 1, updated_at = now() WHERE server_id = $1",
                    server_id,
                )
                await conn.execute(
                    "UPDATE mcp_servers SET enabled = false, updated_at = now() WHERE id = $1", server_id
                )
                await notify(conn, "catalog_changed", str(server_id))
                log.info("hosted removal requested", server_id=str(server_id), by=str(admin.id))
        return HostedEnvelope(hosted=await _reload(conn, server_id))

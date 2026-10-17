"""API keys for MCP clients (D52): GET/POST /api/keys, PATCH/DELETE /api/keys/:id (DESIGN §7.3).

Everyone manages their own keys; admins can also list all keys, issue/revoke keys for other users and set a
key's own tool-call rate limit (D95; null = the gateway default). The plaintext key is returned once.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from wmg_common.errors import AppError
from wmg_common.logging import get_logger
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import AdminUser, CurrentUser, Pool
from wmg_gateway.security import api_keys

log = get_logger(__name__)
router = APIRouter(prefix="/keys", tags=["keys"])

MAX_ACTIVE_KEYS = 10


class ApiKey(ApiModel):
    id: UUID
    name: str
    prefix: str
    user_id: UUID
    user_email: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    rate_limit_per_min: int | None


class KeysEnvelope(ApiModel):
    keys: list[ApiKey]
    default_rate_limit_per_min: int  # 0 = rate limiting off


class KeyEnvelope(ApiModel):
    key: ApiKey


class KeyLimit(ApiModel):
    rate_limit_per_min: int | None = Field(ge=1, le=100_000)


class KeyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=50)
    user_id: UUID | None = None


class KeyCreated(ApiModel):
    id: UUID
    name: str
    prefix: str
    key: str


_SELECT = (
    "SELECT k.id, k.name, k.prefix, k.user_id, u.email AS user_email, "
    "k.created_at, k.last_used_at, k.revoked_at, k.rate_limit_per_min "
    "FROM api_keys k JOIN users u ON u.id = k.user_id "
)


@router.get("")
async def list_keys(
    user: CurrentUser,
    request: Request,
    pool: Pool,
    user_id: Annotated[UUID | None, Query(alias="userId")] = None,
    all_users: Annotated[int, Query(alias="all", ge=0, le=1)] = 0,
) -> KeysEnvelope:
    if user.role != "admin" and (all_users or (user_id is not None and user_id != user.id)):
        raise AppError(403, "FORBIDDEN", "Admins only")
    async with pool.acquire() as conn:
        if all_users:
            rows = await conn.fetch(_SELECT + "ORDER BY k.created_at DESC")
        else:
            rows = await conn.fetch(
                _SELECT + "WHERE k.user_id = $1 ORDER BY k.created_at DESC", user_id or user.id
            )
    return KeysEnvelope(
        keys=[ApiKey.model_validate(dict(r)) for r in rows],
        default_rate_limit_per_min=request.app.state.settings.rate_limit_per_min,
    )


@router.patch("/{key_id}")
async def set_key_limit(key_id: UUID, body: KeyLimit, admin: AdminUser, pool: Pool) -> KeyEnvelope:
    """Takes effect on the key's next request: every request re-reads the key (NFR-P5)."""
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE api_keys SET rate_limit_per_min = $2 WHERE id = $1 AND revoked_at IS NULL RETURNING id",
            key_id,
            body.rate_limit_per_min,
        )
        if updated is None:
            exists = await conn.fetchval("SELECT 1 FROM api_keys WHERE id = $1", key_id)
            raise (
                AppError(409, "CONFLICT", "Key is revoked")
                if exists
                else AppError(404, "NOT_FOUND", "Key not found")
            )
        row = await conn.fetchrow(_SELECT + "WHERE k.id = $1", key_id)
    log.info("key rate limit set", key_id=str(key_id), limit=body.rate_limit_per_min, by=str(admin.id))
    return KeyEnvelope(key=ApiKey.model_validate(dict(row)))


@router.post("", status_code=201)
async def create_key(body: KeyCreate, user: CurrentUser, request: Request, pool: Pool) -> KeyCreated:
    owner = body.user_id or user.id
    if owner != user.id and user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Only admins can issue keys for other users")
    pepper = request.app.state.settings.api_key_pepper.get_secret_value()
    key, prefix = api_keys.generate()
    async with pool.acquire() as conn, conn.transaction():
        status = await conn.fetchval("SELECT status FROM users WHERE id = $1 FOR UPDATE", owner)
        if status is None:
            raise AppError(404, "NOT_FOUND", "User not found")
        if status != "active":
            raise AppError(409, "CONFLICT", "User is disabled")
        active = await conn.fetchval(
            "SELECT count(*) FROM api_keys WHERE user_id = $1 AND revoked_at IS NULL", owner
        )
        if active >= MAX_ACTIVE_KEYS:
            raise AppError(
                409, "CONFLICT", f"At most {MAX_ACTIVE_KEYS} active keys per user; revoke one first"
            )
        key_id = await conn.fetchval(
            "INSERT INTO api_keys (user_id, name, prefix, secret_hash, created_by) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            owner,
            body.name.strip(),
            prefix,
            api_keys.hash_key(key, pepper),
            user.id,
        )
    return KeyCreated(id=key_id, name=body.name.strip(), prefix=prefix, key=key)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: UUID, user: CurrentUser, pool: Pool) -> None:
    async with pool.acquire() as conn:
        row: Any = await conn.fetchrow("SELECT user_id, revoked_at FROM api_keys WHERE id = $1", key_id)
        # members get 404 for other people's keys: no hint that the id exists
        if row is None or (row["user_id"] != user.id and user.role != "admin"):
            raise AppError(404, "NOT_FOUND", "Key not found")
        if row["revoked_at"] is None:
            await conn.execute(
                "UPDATE api_keys SET revoked_at = now(), revoked_by = $2 WHERE id = $1", key_id, user.id
            )

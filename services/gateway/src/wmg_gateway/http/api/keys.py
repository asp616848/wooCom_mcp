"""API keys for MCP clients (D52): GET/POST /api/keys, DELETE /api/keys/:id (DESIGN §7.3).

Everyone manages their own keys; admins can also list all keys and issue/revoke keys for other users.
The plaintext key is returned once, at creation.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from wmg_common.errors import AppError
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser, Pool
from wmg_gateway.security import api_keys

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


class KeysEnvelope(ApiModel):
    keys: list[ApiKey]


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
    "k.created_at, k.last_used_at, k.revoked_at "
    "FROM api_keys k JOIN users u ON u.id = k.user_id "
)


@router.get("")
async def list_keys(
    user: CurrentUser,
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
    return KeysEnvelope(keys=[ApiKey.model_validate(dict(r)) for r in rows])


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

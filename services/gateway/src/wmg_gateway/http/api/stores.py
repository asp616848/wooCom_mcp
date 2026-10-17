"""Bonus stores (DESIGN §13.1): GET/POST /api/stores, POST /api/stores/:name/retry, DELETE /api/stores/:name.

Any signed-in user may create stores (≤ MAX_STORES_PER_USER each, ≤ MAX_STORES_TOTAL overall); members see
and manage their own, admins all. The API only records intent (202); the leader's reconciler does the work
(§13.2). Limit checks run under one advisory lock, so two concurrent creates can't both take the last slot.
Another user's store answers 404, like API keys.
"""

import secrets
import string
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request
from pydantic import Field

from wmg_common.errors import AppError
from wmg_common.logging import get_logger
from wmg_gateway.db.repos.users import User
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser, Pool
from wmg_gateway.provisioner.stores import server_slug
from wmg_gateway.runtime import Runtime

log = get_logger(__name__)
router = APIRouter(prefix="/stores", tags=["stores"])

NAME_PATTERN = r"^[a-z][a-z0-9]{2,15}$"
RESERVED = frozenset({"main", "admin", "api", "www", "shop", "dashboard", "gateway", "test"})
TOKEN_ALPHABET = string.ascii_letters + string.digits
TOKEN_LENGTH = 48
TEARDOWN_PREFIXES = ("teardown failed", "could not start teardown")
StoreName = Annotated[str, Path(pattern=NAME_PATTERN)]


class StoreCreate(ApiModel):
    name: str = Field(pattern=NAME_PATTERN)


class StoreOut(ApiModel):
    name: str
    status: Literal["provisioning", "ready", "failed", "deleting"]
    status_reason: str | None
    url: str
    attempt: int
    owner: str
    tools_prefix: str
    created_at: datetime
    ready_at: datetime | None


class StoresEnvelope(ApiModel):
    stores: list[StoreOut]


class StoreEnvelope(ApiModel):
    store: StoreOut


_SELECT = "SELECT s.*, u.email AS owner_email FROM stores s JOIN users u ON u.id = s.owner_id "


def _out(row: Any, scheme: str) -> StoreOut:
    return StoreOut(
        name=row["name"],
        status=row["status"],
        status_reason=row["status_reason"],
        url=f"{scheme}://{row['host']}",
        attempt=row["attempt"],
        owner=row["owner_email"],
        tools_prefix=f"{server_slug(row['name'])}__",
        created_at=row["created_at"],
        ready_at=row["ready_at"],
    )


def _runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


async def _visible(conn: Any, name: str, user: User, *, lock: bool = False) -> Any:
    row = await conn.fetchrow(_SELECT + "WHERE s.name = $1" + (" FOR UPDATE OF s" if lock else ""), name)
    if row is None or (user.role != "admin" and row["owner_id"] != user.id):
        raise AppError(404, "NOT_FOUND", "Store not found")
    return row


async def _reload(conn: Any, store_id: uuid.UUID, scheme: str) -> StoreOut:
    return _out(await conn.fetchrow(_SELECT + "WHERE s.id = $1", store_id), scheme)


@router.get("")
async def list_stores(user: CurrentUser, request: Request, pool: Pool) -> StoresEnvelope:
    scheme = _runtime(request).settings.store_scheme
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SELECT + "WHERE $1 OR s.owner_id = $2 ORDER BY s.created_at", user.role == "admin", user.id
        )
    return StoresEnvelope(stores=[_out(r, scheme) for r in rows])


@router.post("", status_code=202)
async def create_store(body: StoreCreate, user: CurrentUser, request: Request, pool: Pool) -> StoreEnvelope:
    rt = _runtime(request)
    settings = rt.settings
    if not settings.provisioning_enabled():
        raise AppError(503, "PROVISIONING_DISABLED", "Store provisioning is not configured on this gateway")
    name = body.name
    if name in RESERVED:
        raise AppError(422, "VALIDATION_FAILED", f"name: '{name}' is reserved")
    store_id = uuid.uuid4()
    token = "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LENGTH))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('wmg-stores'))")
            if await conn.fetchval("SELECT 1 FROM stores WHERE name = $1", name) or await conn.fetchval(
                "SELECT 1 FROM mcp_servers WHERE slug = $1", server_slug(name)
            ):
                raise AppError(409, "CONFLICT", f"A store named '{name}' already exists")
            own = await conn.fetchval("SELECT count(*) FROM stores WHERE owner_id = $1", user.id)
            if own >= settings.max_stores_per_user:
                raise AppError(
                    409,
                    "CONFLICT",
                    f"Store limit reached: at most {settings.max_stores_per_user} stores per user",
                )
            if await conn.fetchval("SELECT count(*) FROM stores") >= settings.max_stores_total:
                raise AppError(
                    409,
                    "CONFLICT",
                    f"Store limit reached: at most {settings.max_stores_total} stores in total",
                )
            await conn.execute(
                "INSERT INTO stores (id, name, owner_id, status, namespace, host, mcp_token_enc) "
                "VALUES ($1, $2, $3, 'provisioning', $4, $5, $6)",
                store_id,
                name,
                user.id,
                f"store-{name}",
                settings.store_host(name),
                rt.cipher.encrypt(token, str(store_id)),
            )
        log.info("store requested", store=name, by=str(user.id))
        return StoreEnvelope(store=await _reload(conn, store_id, settings.store_scheme))


@router.post("/{name}/retry", status_code=202)
async def retry_store(name: StoreName, user: CurrentUser, request: Request, pool: Pool) -> StoreEnvelope:
    scheme = _runtime(request).settings.store_scheme
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _visible(conn, name, user, lock=True)
            if row["status"] != "failed":
                raise AppError(409, "CONFLICT", "Only a failed store can be retried")
            if (row["status_reason"] or "").startswith(TEARDOWN_PREFIXES):
                raise AppError(409, "CONFLICT", "Its teardown failed: delete the store again instead")
            await conn.execute(
                "UPDATE stores SET status = 'provisioning', status_reason = NULL, attempt = attempt + 1, "
                "updated_at = now() WHERE id = $1",
                row["id"],
            )
        log.info("store retry", store=name, attempt=row["attempt"] + 1, by=str(user.id))
        return StoreEnvelope(store=await _reload(conn, row["id"], scheme))


@router.delete("/{name}", status_code=202)
async def delete_store(name: StoreName, user: CurrentUser, request: Request, pool: Pool) -> StoreEnvelope:
    """Idempotent: deleting a store that is already being deleted returns it unchanged."""
    scheme = _runtime(request).settings.store_scheme
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _visible(conn, name, user, lock=True)
            if row["status"] != "deleting":
                # a new attempt gives the teardown Job a fresh name and fences off in-flight reconciler writes
                await conn.execute(
                    "UPDATE stores SET status = 'deleting', status_reason = NULL, attempt = attempt + 1, "
                    "updated_at = now() WHERE id = $1",
                    row["id"],
                )
                log.info("store deletion requested", store=name, by=str(user.id))
        return StoreEnvelope(store=await _reload(conn, row["id"], scheme))

"""GET /api/audit (DESIGN §7.3, §5.10): newest first, keyset pagination on the row id.

Admins see every row and may filter by user; members always get their own rows only (enforced here, not in
the UI).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from wmg_gateway.audit.audit import Outcome
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser, Pool

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditItem(ApiModel):
    id: int
    ts: datetime
    user_id: UUID | None
    user_email: str | None
    credential_type: str | None
    method: str
    server_slug: str | None
    tool_name: str | None
    exposed_name: str | None
    outcome: Outcome
    duration_ms: int
    error_message: str | None
    args_summary: Any
    client_name: str | None
    client_mode: str
    request_id: str | None


class AuditPage(ApiModel):
    items: list[AuditItem]
    next_cursor: str | None


_COLUMNS = (
    "id, ts, user_id, user_email, credential_type, method, server_slug, tool_name, exposed_name, outcome, "
    "duration_ms, error_message, args_summary, client_name, client_mode, request_id"
)


@router.get("")
async def list_audit(
    user: CurrentUser,
    pool: Pool,
    user_id: Annotated[UUID | None, Query(alias="userId")] = None,
    server_slug: Annotated[str | None, Query(alias="serverSlug", max_length=30)] = None,
    outcome: Outcome | None = None,
    from_ts: Annotated[datetime | None, Query(alias="from")] = None,
    to_ts: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    if user.role != "admin":
        user_id = user.id
    conditions: list[str] = []
    args: list[Any] = []
    for clause, value in (
        ("user_id = ${}", user_id),
        ("server_slug = ${}", server_slug),
        ("outcome = ${}", outcome),
        ("ts >= ${}", from_ts),
        ("ts < ${}", to_ts),
        ("id < ${}", cursor),
    ):
        if value is not None:
            args.append(value)
            conditions.append(clause.format(len(args)))
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    args.append(limit)
    sql = f"SELECT {_COLUMNS} FROM audit_log {where}ORDER BY id DESC LIMIT ${len(args)}"  # noqa: S608
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    items = [AuditItem.model_validate(dict(r)) for r in rows]
    return AuditPage(items=items, next_cursor=str(items[-1].id) if len(items) == limit else None)

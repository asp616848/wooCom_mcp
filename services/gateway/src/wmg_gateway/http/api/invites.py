"""Invites (D56): admin creates a one-time link (valid 72 h, shown once); the invitee sets name + password.

GET/POST/DELETE /api/invites (admin); GET /api/invites/:token and POST /api/invites/:token/accept (public).
Only the token's SHA-256 is stored.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field

from wmg_common.errors import AppError
from wmg_gateway.db.repos import sessions, users
from wmg_gateway.db.repos.users import Role, User
from wmg_gateway.http.api.auth import set_session_cookie
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import AdminUser, Pool
from wmg_gateway.security.passwords import hash_password
from wmg_gateway.security.sessions import new_token, token_hash

router = APIRouter(prefix="/invites", tags=["invites"])

INVITE_TTL = timedelta(hours=72)
Email = Annotated[str, Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


class InviteCreate(ApiModel):
    email: Email
    role: Role


class InviteCreated(ApiModel):
    invite_url: str
    expires_at: datetime


class Invite(ApiModel):
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    created_at: datetime


class InvitesEnvelope(ApiModel):
    invites: list[Invite]


class InviteInfo(ApiModel):
    email: str
    role: Role
    valid: bool


class InviteAccept(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)


class UserEnvelope(ApiModel):
    user: User


@router.get("")
async def list_invites(_admin: AdminUser, pool: Pool) -> InvitesEnvelope:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, email, role, expires_at, created_at FROM invites "
            "WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() ORDER BY created_at DESC"
        )
    return InvitesEnvelope(invites=[Invite.model_validate(dict(r)) for r in rows])


@router.post("", status_code=201)
async def create_invite(body: InviteCreate, admin: AdminUser, request: Request, pool: Pool) -> InviteCreated:
    email = body.email.strip().lower()
    token = new_token()
    expires_at = datetime.now(UTC) + INVITE_TTL
    async with pool.acquire() as conn, conn.transaction():
        if await conn.fetchval("SELECT 1 FROM users WHERE email = $1", email):
            raise AppError(409, "CONFLICT", "A user with this email already exists")
        # a new invite replaces any pending one for the same email
        await conn.execute(
            "UPDATE invites SET revoked_at = now() "
            "WHERE email = $1 AND accepted_at IS NULL AND revoked_at IS NULL",
            email,
        )
        await conn.execute(
            "INSERT INTO invites (email, role, token_hash, invited_by, expires_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            email,
            body.role,
            token_hash(token),
            admin.id,
            expires_at,
        )
    base = request.app.state.settings.public_dashboard_url.rstrip("/")
    return InviteCreated(invite_url=f"{base}/invite/{token}", expires_at=expires_at)


@router.delete("/{invite_id}", status_code=204)
async def revoke_invite(invite_id: UUID, _admin: AdminUser, pool: Pool) -> None:
    async with pool.acquire() as conn:
        done = await conn.fetchval(
            "UPDATE invites SET revoked_at = now() "
            "WHERE id = $1 AND accepted_at IS NULL AND revoked_at IS NULL "
            "RETURNING id",
            invite_id,
        )
    if done is None:
        raise AppError(404, "NOT_FOUND", "Invite not found or no longer pending")


async def _lookup(conn: Any, token: str) -> tuple[Any, bool]:
    row = await conn.fetchrow(
        "SELECT id, email, role, expires_at, accepted_at, revoked_at FROM invites WHERE token_hash = $1",
        token_hash(token),
    )
    if row is None:
        raise AppError(404, "NOT_FOUND", "Invite not found")
    valid = row["accepted_at"] is None and row["revoked_at"] is None and row["expires_at"] > datetime.now(UTC)
    return row, valid


@router.get("/{token}")
async def invite_info(token: str, pool: Pool) -> InviteInfo:
    async with pool.acquire() as conn:
        row, valid = await _lookup(conn, token)
    return InviteInfo(email=row["email"], role=row["role"], valid=valid)


@router.post("/{token}/accept")
async def accept_invite(
    token: str, body: InviteAccept, request: Request, response: Response, pool: Pool
) -> UserEnvelope:
    async with pool.acquire() as conn, conn.transaction():
        row, valid = await _lookup(conn, token)
        if not valid:
            raise AppError(409, "CONFLICT", "This invite is no longer valid")
        created = await users.create(
            conn, row["email"], body.name.strip(), row["role"], hash_password(body.password)
        )
        if created is None:
            raise AppError(409, "CONFLICT", "A user with this email already exists")
        await conn.execute("UPDATE invites SET accepted_at = now() WHERE id = $1", row["id"])
        session_token = new_token()
        await sessions.create(conn, created["id"], session_token, request.headers.get("user-agent"), None)
    set_session_cookie(response, session_token, request.app.state.settings.cookie_secure)
    return UserEnvelope(user=users.to_user(created))

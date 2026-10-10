"""Users (admin): GET /api/users, PATCH /api/users/:id (DESIGN §7.3, D55)."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import model_validator

from wmg_common.errors import AppError
from wmg_gateway.db.repos import users
from wmg_gateway.db.repos.users import Role, Status, User
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import AdminUser, Pool

router = APIRouter(prefix="/users", tags=["users"])


class UsersEnvelope(ApiModel):
    users: list[User]


class UserEnvelope(ApiModel):
    user: User


class UserPatch(ApiModel):
    role: Role | None = None
    status: Status | None = None

    @model_validator(mode="after")
    def _something(self) -> UserPatch:
        if self.role is None and self.status is None:
            raise ValueError("give role and/or status")
        return self


@router.get("")
async def list_users(_admin: AdminUser, pool: Pool) -> UsersEnvelope:
    async with pool.acquire() as conn:
        return UsersEnvelope(users=await users.list_all(conn))


@router.patch("/{user_id}")
async def patch_user(user_id: UUID, body: UserPatch, admin: AdminUser, pool: Pool) -> UserEnvelope:
    if user_id == admin.id:
        raise AppError(409, "CONFLICT", "You can't change your own role or status")
    async with pool.acquire() as conn, conn.transaction():
        # serialize admin changes so two admins can't demote each other at the same time
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('wmg-user-roles'))")
        target = await users.get(conn, user_id)
        if target is None:
            raise AppError(404, "NOT_FOUND", "User not found")
        loses_admin = (
            target.role == "admin"
            and target.status == "active"
            and (body.role == "member" or body.status == "disabled")
        )
        if loses_admin and await users.active_admin_count(conn) <= 1:
            raise AppError(409, "CONFLICT", "At least one active admin must remain")
        updated = await users.update(conn, user_id, body.role, body.status)
        if body.status == "disabled":
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
    return UserEnvelope(user=updated)

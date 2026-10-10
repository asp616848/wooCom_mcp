"""users table (DESIGN §6)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Role = Literal["admin", "member"]
Status = Literal["active", "disabled"]


class User(BaseModel):
    """API shape of a user (camelCase on the wire). Never carries the password hash."""

    model_config = ConfigDict(
        alias_generator=to_camel, serialize_by_alias=True, validate_by_name=True, validate_by_alias=True
    )

    id: UUID
    email: str
    name: str
    role: Role
    status: Status
    created_at: datetime
    last_login_at: datetime | None


def to_user(row: Any) -> User:
    return User(
        **{k: row[k] for k in ("id", "email", "name", "role", "status", "created_at", "last_login_at")}
    )


async def get_login_row(conn: Any, email: str) -> Any:
    return await conn.fetchrow(
        "SELECT id, email, name, role, status, created_at, last_login_at, password_hash "
        "FROM users WHERE email = $1",
        email,
    )


async def mark_login(conn: Any, user_id: UUID) -> None:
    await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)


_USER_COLUMNS = "id, email, name, role, status, created_at, last_login_at"


async def list_all(conn: Any) -> list[User]:
    rows = await conn.fetch(f"SELECT {_USER_COLUMNS} FROM users ORDER BY created_at")  # noqa: S608
    return [to_user(r) for r in rows]


async def get(conn: Any, user_id: UUID) -> User | None:
    row = await conn.fetchrow(f"SELECT {_USER_COLUMNS} FROM users WHERE id = $1", user_id)  # noqa: S608
    return to_user(row) if row else None


async def active_admin_count(conn: Any) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM users WHERE role = 'admin' AND status = 'active'"))


async def update(conn: Any, user_id: UUID, role: Role | None, status: Status | None) -> User:
    row = await conn.fetchrow(
        "UPDATE users SET role = COALESCE($2::user_role, role), status = COALESCE($3::user_status, status), "  # noqa: S608
        f"updated_at = now() WHERE id = $1 RETURNING {_USER_COLUMNS}",
        user_id,
        role,
        status,
    )
    return to_user(row)


async def create(conn: Any, email: str, name: str, role: Role, password_hash: str) -> Any:
    """Returns the new row (with `password_hash`, like `get_login_row`), or None if the email exists."""
    return await conn.fetchrow(
        "INSERT INTO users (email, name, role, password_hash) VALUES ($1, $2, $3, $4) "  # noqa: S608
        f"ON CONFLICT (email) DO NOTHING RETURNING {_USER_COLUMNS}, password_hash",
        email,
        name,
        role,
        password_hash,
    )

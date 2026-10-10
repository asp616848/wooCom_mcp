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

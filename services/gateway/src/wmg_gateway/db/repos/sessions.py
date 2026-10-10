"""sessions table: server-side dashboard sessions (D51)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wmg_gateway.db.repos.users import User, to_user
from wmg_gateway.security.sessions import REFRESH_BELOW, SESSION_TTL, token_hash


async def create(conn: Any, user_id: UUID, token: str, user_agent: str | None, ip: str | None) -> None:
    await conn.execute(
        "INSERT INTO sessions (user_id, token_hash, expires_at, user_agent, ip) "
        "VALUES ($1, $2, $3, $4, $5::inet)",
        user_id,
        token_hash(token),
        datetime.now(UTC) + SESSION_TTL,
        (user_agent or "")[:300] or None,
        ip,
    )


async def user_for_token(conn: Any, token: str) -> User | None:
    """Active user behind a live session; slides the expiry forward when it is getting old."""
    row = await conn.fetchrow(
        "SELECT s.id AS session_id, s.expires_at, "
        "u.id, u.email, u.name, u.role, u.status, u.created_at, u.last_login_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = $1 AND s.expires_at > now() AND u.status = 'active'",
        token_hash(token),
    )
    if row is None:
        return None
    now = datetime.now(UTC)
    if row["expires_at"] - now < REFRESH_BELOW:
        await conn.execute(
            "UPDATE sessions SET expires_at = $2 WHERE id = $1", row["session_id"], now + SESSION_TTL
        )
    return to_user(row)


async def delete(conn: Any, token: str) -> None:
    await conn.execute("DELETE FROM sessions WHERE token_hash = $1", token_hash(token))

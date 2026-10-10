"""POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me (DESIGN §7.3)."""

import ipaddress
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from wmg_common.errors import AppError
from wmg_gateway.db.repos import counters, sessions, users
from wmg_gateway.db.repos.users import User
from wmg_gateway.http.deps import CurrentUser, Pool
from wmg_gateway.request_context import request_meta
from wmg_gateway.security.passwords import DUMMY_HASH, verify_password
from wmg_gateway.security.sessions import COOKIE_NAME, SESSION_TTL, new_token

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_MAX_FAILURES = 5


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class UserEnvelope(BaseModel):
    user: User


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _client_ip() -> str | None:
    meta = request_meta.get()
    if meta is None or meta.client_ip is None:
        return None
    try:
        return str(ipaddress.ip_address(meta.client_ip))
    except ValueError:
        return None


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response, pool: Pool) -> UserEnvelope:
    email = body.email.strip().lower()
    key = f"login:{email}"
    async with pool.acquire() as conn:
        if await counters.current(conn, key, LOGIN_WINDOW) >= LOGIN_MAX_FAILURES:
            raise AppError(429, "RATE_LIMITED", "Too many failed sign-ins. Try again in 15 minutes")
        row = await users.get_login_row(conn, email)
        ok = verify_password(body.password, row["password_hash"] if row else DUMMY_HASH)
        if row is None or not ok or row["status"] != "active":
            await counters.increment(conn, key, LOGIN_WINDOW)
            raise AppError(401, "UNAUTHENTICATED", "Invalid email or password")
        token = new_token()
        await sessions.create(conn, row["id"], token, request.headers.get("user-agent"), _client_ip())
        await users.mark_login(conn, row["id"])
    set_session_cookie(response, token, request.app.state.settings.cookie_secure)
    return UserEnvelope(user=users.to_user(row))


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, pool: Pool) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with pool.acquire() as conn:
            await sessions.delete(conn, token)
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me")
async def me(user: CurrentUser) -> UserEnvelope:
    return UserEnvelope(user=user)

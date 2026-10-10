"""FastAPI dependencies: database pool, current session user, admin check, CSRF header (DESIGN §7.3, D51)."""

from typing import Annotated, Any

from fastapi import Depends, Request

from wmg_common.errors import AppError
from wmg_gateway.db.repos import sessions
from wmg_gateway.db.repos.users import User
from wmg_gateway.security.sessions import COOKIE_NAME

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-WMG-CSRF"


async def get_pool(request: Request) -> Any:
    return request.app.state.pool


Pool = Annotated[Any, Depends(get_pool)]


async def csrf_check(request: Request) -> None:
    """Browsers can't add custom headers to cross-site form posts, so requiring one blocks CSRF."""
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise AppError(403, "FORBIDDEN", f"Missing {CSRF_HEADER} header")


async def current_user(request: Request, pool: Pool) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        async with pool.acquire() as conn:
            user = await sessions.user_for_token(conn, token)
        if user is not None:
            return user
    raise AppError(401, "UNAUTHENTICATED", "Please sign in")


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Admins only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]

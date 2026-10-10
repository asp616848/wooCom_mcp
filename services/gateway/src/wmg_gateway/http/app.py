"""FastAPI app factory: lifespan, health, metrics, /api routers, error handling (DESIGN §5.3).

The MCP endpoints (`/mcp/search` route + `/` mount, last) are added in P8.
"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from prometheus_client import make_asgi_app
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from wmg_common.errors import AppError, error_response
from wmg_common.logging import get_logger
from wmg_gateway.bootstrap import bootstrap
from wmg_gateway.db.migrate import migrate
from wmg_gateway.db.pool import create_pool
from wmg_gateway.http.api import auth
from wmg_gateway.http.deps import csrf_check
from wmg_gateway.request_context import RequestContextMiddleware
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.settings import Settings

log = get_logger(__name__)

API_BODY_LIMIT = 256 * 1024
READY_CACHE_S = 2.0
STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    429: "RATE_LIMITED",
}


class ApiBodyLimitMiddleware:
    """Reject `/api` bodies over 256 KB by Content-Length before reading them."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            for name, value in scope["headers"]:
                if name == b"content-length" and value.isdigit() and int(value) > API_BODY_LIMIT:
                    response = error_response(413, "PAYLOAD_TOO_LARGE", "Request body is larger than 256 KB")
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class Readiness:
    """Ready = migrations finished and a DB ping succeeded within the last 2 s."""

    def __init__(self) -> None:
        self.migrated = False
        self._checked_at = 0.0
        self._ok = False

    async def check(self, pool: Any) -> bool:
        if not self.migrated:
            return False
        if time.monotonic() - self._checked_at > READY_CACHE_S:
            try:
                async with pool.acquire(timeout=1) as conn:
                    self._ok = await conn.fetchval("SELECT 1") == 1
            except Exception as exc:
                log.warning("db ping failed", error=type(exc).__name__)
                self._ok = False
            self._checked_at = time.monotonic()
        return self._ok


def create_app(settings: Settings) -> FastAPI:
    readiness = Readiness()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cipher = CredentialCipher(settings.encryption_key())
        pool = await create_pool(
            settings.database_url.get_secret_value(), settings.db_pool_min, settings.db_pool_max
        )
        app.state.pool = pool
        app.state.cipher = cipher
        try:
            await migrate(pool, after=lambda conn: bootstrap(conn, settings, cipher))
            readiness.migrated = True
            log.info("gateway started")
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Woo MCP Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.get("/livez", include_in_schema=False)
    async def livez() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> Response:
        if await readiness.check(request.app.state.pool if readiness.migrated else None):
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready"}, status_code=503)

    app.mount("/metrics", make_asgi_app())

    api = APIRouter(prefix="/api", dependencies=[Depends(csrf_check)])
    api.include_router(auth.router)
    app.include_router(api)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> Response:
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> Response:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = (
            f"{where}: {first.get('msg', 'invalid')}" if where else str(first.get("msg", "Invalid request"))
        )
        return error_response(422, "VALIDATION_FAILED", message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> Response:
        code = STATUS_CODES.get(exc.status_code, "BAD_REQUEST" if exc.status_code < 500 else "INTERNAL")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> Response:
        log.error("unhandled error", error=type(exc).__name__, exc_info=exc)
        return error_response(500, "INTERNAL", "Internal error")

    app.add_middleware(ApiBodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    return app

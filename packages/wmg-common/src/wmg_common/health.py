"""Liveness / readiness endpoints shared by all services (D10).

`/livez` only proves the process serves HTTP (never checks dependencies, so a DB outage can't cause restarts).
`/readyz` runs an optional async check; a False result or an exception means "not ready" (HTTP 503).
"""

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from wmg_common.logging import get_logger

ReadyCheck = Callable[[], Awaitable[bool]]
Endpoint = Callable[[Request], Awaitable[Response]]

log = get_logger(__name__)


async def livez(_request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def make_readyz(check: ReadyCheck | None = None) -> Endpoint:
    async def readyz(_request: Request) -> Response:
        if check is None:
            return JSONResponse({"status": "ready"})
        try:
            ready = await check()
        except Exception as exc:
            log.warning("readiness check failed", error=type(exc).__name__)
            ready = False
        if ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready"}, status_code=503)

    return readyz


def health_routes(check: ReadyCheck | None = None) -> list[Route]:
    """Starlette routes for `/livez` and `/readyz` (GET only)."""
    return [
        Route("/livez", livez, methods=["GET"]),
        Route("/readyz", make_readyz(check), methods=["GET"]),
    ]

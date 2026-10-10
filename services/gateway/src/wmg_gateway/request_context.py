"""Per-request metadata (DESIGN §5.3 step 1): request id, MCP method/name headers, client IP, UA.

Pure ASGI middleware, outermost: echoes `X-Request-Id`, fills the `request_meta` ContextVar for handlers
and audit, and logs one line per request (health probes excluded to keep logs readable).
"""

import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from wmg_common.logging import get_logger

log = get_logger("wmg_gateway.http")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
QUIET_PATHS = frozenset({"/livez", "/readyz", "/metrics"})


@dataclass(frozen=True)
class RequestMeta:
    request_id: str
    mcp_method: str | None
    mcp_name: str | None
    client_ip: str | None
    user_agent: str | None


request_meta: ContextVar[RequestMeta | None] = ContextVar("request_meta", default=None)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        client = scope.get("client")
        meta = RequestMeta(
            request_id=request_id,
            mcp_method=headers.get("mcp-method"),
            mcp_name=headers.get("mcp-name"),
            client_ip=client[0] if client else None,
            user_agent=headers.get("user-agent"),
        )
        token = request_meta.set(meta)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        status = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["path"] not in QUIET_PATHS:
                log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    mcp_method=meta.mcp_method,
                )
            structlog.contextvars.unbind_contextvars("request_id")
            request_meta.reset(token)

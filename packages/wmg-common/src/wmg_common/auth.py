"""Static-credential guard for the MCP servers behind the gateway (DESIGN §9.0, D42).

Pure ASGI middleware: requests to protected paths must carry the configured token either as
`Authorization: Bearer <token>` (mode "bearer") or as `<header_name>: <token>` (mode "header").
Health endpoints stay open so Kubernetes probes work without the secret.
"""

import hmac
from collections.abc import Iterable
from typing import Literal

from starlette.types import ASGIApp, Receive, Scope, Send

AuthMode = Literal["bearer", "header"]

_UNAUTHORIZED_BODY = b'{"error":"unauthorized"}'


class StaticCredentialMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        mode: AuthMode,
        token: str,
        header_name: str = "X-Api-Key",
        protected_paths: Iterable[str] = ("/mcp",),
    ) -> None:
        if not token:
            raise ValueError("token must not be empty")
        self.app = app
        self._mode = mode
        self._token = token.encode()
        self._header = b"authorization" if mode == "bearer" else header_name.lower().encode("latin-1")
        self._paths = tuple(p.rstrip("/") or "/" for p in protected_paths)

    def _is_protected(self, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in self._paths)

    def _presented_token(self, scope: Scope) -> bytes | None:
        for name, value in scope.get("headers", []):
            if name != self._header:
                continue
            if self._mode == "header":
                return bytes(value).strip()
            scheme, _, credential = bytes(value).partition(b" ")
            return credential.strip() if scheme.lower() == b"bearer" and credential.strip() else None
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_protected(scope["path"]):
            await self.app(scope, receive, send)
            return
        presented = self._presented_token(scope)
        if presented is not None and hmac.compare_digest(presented, self._token):
            await self.app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_UNAUTHORIZED_BODY)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})

"""Test helpers: drive an ASGI app directly (no HTTP client library needed)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from starlette.types import ASGIApp, Message


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


async def call(
    app: ASGIApp, path: str, *, method: str = "GET", headers: dict[str, str] | None = None
) -> Response:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return Response(
        status=start["status"],
        headers={k.decode(): v.decode() for k, v in start.get("headers", [])},
        body=body,
    )


AsgiCall = Callable[..., Awaitable[Response]]


@pytest.fixture
def asgi_call() -> AsgiCall:
    return call

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from wmg_common.auth import StaticCredentialMiddleware
from wmg_common.health import health_routes

AsgiCall = Callable[..., Awaitable[Any]]  # the `asgi_call` fixture from conftest.py

TOKEN = "s3cret-token-0123456789"


async def mcp_endpoint(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("mcp ok")


def make_app(mode: str, header_name: str = "X-Api-Key") -> StaticCredentialMiddleware:
    inner = Starlette(routes=[Route("/mcp", mcp_endpoint, methods=["GET", "POST"]), *health_routes()])
    return StaticCredentialMiddleware(inner, mode=mode, token=TOKEN, header_name=header_name)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_bearer_valid_token_reaches_app(asgi_call: AsgiCall) -> None:
    resp = await asgi_call(make_app("bearer"), "/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status == 200
    assert resp.body == b"mcp ok"


@pytest.mark.anyio
async def test_bearer_scheme_is_case_insensitive(asgi_call: AsgiCall) -> None:
    resp = await asgi_call(make_app("bearer"), "/mcp", headers={"Authorization": f"bearer {TOKEN}"})
    assert resp.status == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": "Bearer "},
        {"X-Api-Key": TOKEN},  # right value, wrong place for bearer mode
    ],
)
async def test_bearer_rejects(headers: dict[str, str], asgi_call: AsgiCall) -> None:
    resp = await asgi_call(make_app("bearer"), "/mcp", headers=headers)
    assert resp.status == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.body == b'{"error":"unauthorized"}'


@pytest.mark.anyio
async def test_header_mode_valid_and_invalid(asgi_call: AsgiCall) -> None:
    app = make_app("header")
    assert (await asgi_call(app, "/mcp", headers={"X-Api-Key": TOKEN})).status == 200
    assert (await asgi_call(app, "/mcp", headers={"x-api-key": TOKEN})).status == 200
    assert (await asgi_call(app, "/mcp", headers={"X-Api-Key": "nope"})).status == 401
    assert (await asgi_call(app, "/mcp", headers={"Authorization": f"Bearer {TOKEN}"})).status == 401


@pytest.mark.anyio
async def test_subpaths_are_protected_and_health_is_open(asgi_call: AsgiCall) -> None:
    app = make_app("bearer")
    assert (await asgi_call(app, "/mcp/anything")).status == 401
    assert (await asgi_call(app, "/livez")).status == 200
    assert (await asgi_call(app, "/readyz")).status == 200
    # "/mcpx" is not under the protected prefix; the inner app simply has no such route
    assert (await asgi_call(app, "/mcpx")).status == 404


@pytest.mark.anyio
async def test_non_http_scopes_pass_through(asgi_call: AsgiCall) -> None:
    seen: list[str] = []

    async def inner(scope: dict[str, object], receive: object, send: object) -> None:
        seen.append(str(scope["type"]))

    app = StaticCredentialMiddleware(inner, mode="bearer", token=TOKEN)  # type: ignore[arg-type]
    await app({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]


def test_empty_token_rejected() -> None:
    with pytest.raises(ValueError):
        StaticCredentialMiddleware(Starlette(), mode="bearer", token="")

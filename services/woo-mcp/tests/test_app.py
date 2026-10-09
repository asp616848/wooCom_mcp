"""The ASGI app as the gateway sees it: credential check, Host guard, health, both protocol eras."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.types import ASGIApp

from wmg_common.mcp_app import build_upstream_app
from woo_mcp.tools import build_server
from woo_mcp.wc import WooClient

pytestmark = pytest.mark.anyio

TOKEN = "test-mcp-token-0123456789"


@asynccontextmanager
async def running(client: WooClient, settings_factory: Any) -> AsyncIterator[ASGIApp]:
    app = build_upstream_app(build_server(client), settings_factory())
    inner = app.app  # type: ignore[attr-defined]  # Starlette app inside StaticCredentialMiddleware
    async with inner.router.lifespan_context(inner):
        yield app


def http(app: ASGIApp, host: str = "main-woo-mcp", **headers: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=f"http://{host}", headers=headers
    )


async def test_health_is_open_and_mcp_needs_token(client: WooClient, settings_factory: Any) -> None:
    async with running(client, settings_factory) as app, http(app) as h:
        assert (await h.get("/livez")).status_code == 200
        assert (await h.get("/readyz")).status_code == 200
        r = await h.post("/mcp", json={})
        assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
        r = await h.post("/mcp", json={}, headers={"Authorization": "Bearer wrong-token-000000000"})
        assert r.status_code == 401


async def test_unknown_host_rejected(client: WooClient, settings_factory: Any) -> None:
    async with (
        running(client, settings_factory) as app,
        http(app, host="evil.example", Authorization=f"Bearer {TOKEN}") as h,
    ):
        assert (await h.post("/mcp", json={})).status_code == 421


@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_both_protocol_eras_list_tools(client: WooClient, settings_factory: Any, mode: str) -> None:
    async with running(client, settings_factory) as app, http(app, Authorization=f"Bearer {TOKEN}") as h:
        async with Client(streamable_http_client("http://main-woo-mcp/mcp", http_client=h), mode=mode) as mcp:
            names = {t.name for t in (await mcp.list_tools()).tools}
    assert len(names) == 8

"""Bearer credential on /mcp; health open."""

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mcp_gst.settings import GstSettings
from mcp_gst.tools import build_server
from wmg_common.mcp_app import build_upstream_app

pytestmark = pytest.mark.anyio
TOKEN = "gst-test-token-0123456789"


async def test_bearer_required_and_tools_listed() -> None:
    settings = GstSettings(mcp_auth_token=TOKEN, mcp_allowed_hosts="gst,gst:*")
    app = build_upstream_app(build_server("MH"), settings)
    inner = app.app  # type: ignore[attr-defined]
    async with inner.router.lifespan_context(inner):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://gst") as h:
            assert (await h.get("/livez")).status_code == 200
            assert (await h.post("/mcp", json={})).status_code == 401
            assert (await h.post("/mcp", json={}, headers={"X-Api-Key": TOKEN})).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with (
            httpx2.AsyncClient(transport=transport, base_url="http://gst", headers=headers) as h,
            Client(streamable_http_client("http://gst/mcp", http_client=h)) as mcp,
        ):
            assert len((await mcp.list_tools()).tools) == 2

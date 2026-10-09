"""X-Api-Key credential on /mcp (header mode); bearer is not accepted."""

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from mcp_pincode.data import load_places
from mcp_pincode.settings import PincodeSettings
from mcp_pincode.tools import build_server
from wmg_common.mcp_app import build_upstream_app

pytestmark = pytest.mark.anyio
TOKEN = "pin-test-token-0123456789"


async def test_header_credential_required() -> None:
    settings = PincodeSettings(
        mcp_auth_token=TOKEN, mcp_auth_mode="header", mcp_allowed_hosts="pincode,pincode:*"
    )
    app = build_upstream_app(build_server(load_places(), settings.origin_pincode), settings)
    inner = app.app  # type: ignore[attr-defined]
    async with inner.router.lifespan_context(inner):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://pincode") as h:
            assert (await h.post("/mcp", json={})).status_code == 401
            bearer = {"Authorization": f"Bearer {TOKEN}"}
            assert (await h.post("/mcp", json={}, headers=bearer)).status_code == 401
        async with (
            httpx2.AsyncClient(
                transport=transport, base_url="http://pincode", headers={"X-Api-Key": TOKEN}
            ) as h,
            Client(streamable_http_client("http://pincode/mcp", http_client=h)) as mcp,
        ):
            assert len((await mcp.list_tools()).tools) == 2

"""HTTP layer without a database: CSRF, body limit, request id, error envelope, health."""

from typing import Any

import httpx2
import pytest

from wmg_gateway.http.app import create_app
from wmg_gateway.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def client(base_env: dict[str, str]) -> httpx2.AsyncClient:
    app = create_app(Settings(database_url="postgresql://unused", **base_env))  # type: ignore[arg-type]
    app.state.pool = None  # lifespan not run: these paths must fail before touching the DB
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://dashboard.localhost")


async def test_csrf_header_required_on_post(client: Any) -> None:
    r = await client.post("/api/auth/login", json={"email": "a@b.co", "password": "x"})
    assert r.status_code == 403
    assert r.json() == {"error": {"code": "FORBIDDEN", "message": "Missing X-WMG-CSRF header"}}


async def test_body_limit(client: Any) -> None:
    r = await client.post("/api/auth/login", content=b"x" * (256 * 1024 + 1), headers={"X-WMG-CSRF": "1"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_validation_error_envelope(client: Any) -> None:
    r = await client.post("/api/auth/login", json={"email": "a@b.co"}, headers={"X-WMG-CSRF": "1"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED" and "password" in r.json()["error"]["message"]


async def test_request_id_echoed_and_generated(client: Any) -> None:
    r = await client.get("/livez", headers={"X-Request-Id": "abc-123"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "abc-123"
    r = await client.get("/livez", headers={"X-Request-Id": "bad id with spaces"})
    assert len(r.headers["x-request-id"]) == 32


async def test_not_ready_before_migrations_and_404_envelope(client: Any) -> None:
    assert (await client.get("/readyz")).status_code == 503
    r = await client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"

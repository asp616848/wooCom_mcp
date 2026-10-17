"""P7: migrations + bootstrap against real Postgres, and the session auth API end to end."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx2
import pytest

from wmg_gateway.db.migrate import migrate
from wmg_gateway.db.pool import create_pool
from wmg_gateway.http.app import create_app
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.settings import Settings

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CSRF = {"X-WMG-CSRF": "1"}
SEED = [
    {"slug": "woo-main", "name": "WooCommerce (main store)", "url": "http://w/mcp", "authType": "bearer"},
    {"slug": "pincode", "name": "Delivery checker", "url": "http://p/mcp", "authType": "header",
     "authHeaderName": "X-Api-Key"},
]  # fmt: skip


@pytest.fixture
def settings(tmp_path: Path, base_env: dict[str, str], database_url: str) -> Settings:
    (tmp_path / "seed.json").write_text(json.dumps(SEED))
    (tmp_path / "creds" / "woo-main").mkdir(parents=True)
    (tmp_path / "creds" / "woo-main" / "credential").write_text("SENTINEL-CRED-123\n")
    return Settings(  # type: ignore[arg-type]
        database_url=database_url,
        seed_servers_file=tmp_path / "seed.json",
        seed_credentials_dir=tmp_path / "creds",
        **base_env,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://dashboard.localhost"
        ) as c:
            yield c


async def test_migrations_idempotent_and_bootstrap_once(settings: Settings) -> None:
    pool = await create_pool(settings.database_url.get_secret_value(), 1, 2)
    try:
        assert await migrate(pool) == ["001_init", "002_stores", "003_hosted"]
        assert await migrate(pool) == []
    finally:
        await pool.close()


async def test_seeded_rows_and_encrypted_credentials(client: Any, settings: Settings) -> None:
    conn: Any = await asyncpg.connect(settings.database_url.get_secret_value())
    try:
        rows = await conn.fetch(
            "SELECT id, slug, auth_type::text, credential_enc FROM mcp_servers ORDER BY slug"
        )
        assert [r["slug"] for r in rows] == ["pincode", "woo-main"]
        pin, woo = rows
        assert pin["credential_enc"] is None
        assert woo["credential_enc"].startswith("v1:")
        cipher = CredentialCipher(settings.encryption_key())
        assert cipher.decrypt(woo["credential_enc"], str(woo["id"])) == "SENTINEL-CRED-123"
        policies = await conn.fetch("SELECT tool_name FROM tool_policies WHERE admin_only ORDER BY tool_name")
        assert [p["tool_name"] for p in policies] == [
            "create_product",
            "update_order_status",
            "update_product",
        ]
        dump = json.dumps([dict(r) for r in await conn.fetch("SELECT * FROM mcp_servers")], default=str)
        assert "SENTINEL-CRED-123" not in dump
        assert await conn.fetchval("SELECT count(*) FROM users WHERE role = 'admin'") == 1
    finally:
        await conn.close()


async def test_login_me_logout(client: Any) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.post(
        "/api/auth/login", json={"email": "Admin@Example.com", "password": "admin-password-123"}, headers=CSRF
    )
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "admin@example.com" and r.json()["user"]["role"] == "admin"
    assert "password" not in r.text.lower()
    cookie = r.headers["set-cookie"]
    assert "wmg_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["lastLoginAt"] is not None
    assert (await client.post("/api/auth/logout", headers=CSRF)).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_bad_password_then_lockout_on_sixth(client: Any) -> None:
    for _ in range(5):
        r = await client.post(
            "/api/auth/login", json={"email": "admin@example.com", "password": "nope"}, headers=CSRF
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "admin-password-123"}, headers=CSRF
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


async def test_unknown_user_same_error(client: Any) -> None:
    r = await client.post(
        "/api/auth/login", json={"email": "ghost@example.com", "password": "x"}, headers=CSRF
    )
    assert r.status_code == 401 and r.json()["error"]["message"] == "Invalid email or password"


async def test_ready_after_startup(client: Any) -> None:
    assert (await client.get("/readyz")).status_code == 200

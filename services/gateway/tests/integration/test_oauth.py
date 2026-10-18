"""SO-3 OAuth (DESIGN §15, D98) on a live gateway + real Postgres: the SDK's own OAuth client does discovery,
dynamic registration, the browser step (played by a signed-in session) and calls tools; the rest is the flow
attacked by hand: replayed codes, reused refresh tokens, wrong PKCE verifier, revocation, disabled users,
registration rules.
"""

import base64
import hashlib
import secrets
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CSRF = {"X-WMG-CSRF": "1"}
ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
MEMBER = {"email": "ritika@example.com", "password": "ritika-password-1"}
REDIRECT = "http://localhost:8765/callback"
ECHO = "fx-modern__echo"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


@pytest.fixture
def oauth_gateway(gateway_factory: Callable[..., Any]) -> Any:
    """A gateway whose public URLs are its real address, so discovery works from the test client."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    return gateway_factory(
        port=port,
        public_gateway_url=base,
        public_mcp_url=base + "/mcp",
        public_dashboard_url=base,  # the consent page is a dashboard route; the test plays the browser
    )


@asynccontextmanager
async def session(gw: Any, creds: dict[str, str] = ADMIN) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        assert (await http.post("/api/auth/login", json=creds)).status_code == 200
        yield http


async def add_member(gw: Any, admin: httpx2.AsyncClient) -> None:
    invite = await admin.post("/api/invites", json={"email": MEMBER["email"], "role": "member"})
    token = invite.json()["inviteUrl"].rsplit("/", 1)[1]
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
        body = {"name": "Ritika", "password": MEMBER["password"]}
        assert (await anon.post(f"/api/invites/{token}/accept", json=body)).status_code == 200


async def register_upstream(admin: httpx2.AsyncClient, url: str) -> None:
    r = await admin.post("/api/servers", json={"slug": "fx-modern", "name": "FX", "url": url})
    assert r.status_code == 201, r.text


@asynccontextmanager
async def mcp_client(gw: Any, token: str, path: str = "/mcp") -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http,
        Client(streamable_http_client(gw.url + path, http_client=http), cache=None) as client,
    ):
        yield client


async def rejected(gw: Any, token: str, path: str = "/mcp") -> bool:
    """True if the gateway answers 401 to this bearer token."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    async with httpx2.AsyncClient() as http:
        return (await http.post(gw.url + path, json=body, headers=headers)).status_code == 401


async def register_app(gw: Any, name: str = "Test App", **extra: Any) -> dict[str, Any]:
    body = {
        "client_name": name,
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": "mcp",
        **extra,
    }
    async with httpx2.AsyncClient() as http:
        r = await http.post(gw.url + "/register", json=body)
    assert r.status_code == 201, r.text
    info: dict[str, Any] = r.json()
    return info


async def start_authorization(gw: Any, client_id: str, challenge: str, state: str = "st-1") -> str:
    """GET /authorize; returns the consent page request id the browser would be sent to."""
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp",
        "resource": gw.url + "/mcp",
    }
    async with httpx2.AsyncClient() as http:
        r = await http.get(gw.url + "/authorize", params=query, follow_redirects=False)
    assert r.status_code == 302, r.text
    location = urlparse(r.headers["location"])
    assert location.path == "/oauth/consent"
    return parse_qs(location.query)["req"][0]


async def approve(user: httpx2.AsyncClient, request_id: str) -> dict[str, list[str]]:
    r = await user.post(f"/api/oauth/requests/{request_id}/approve")
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["redirectUrl"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == REDIRECT
    return parse_qs(url.query)


async def token_request(gw: Any, **form: str) -> httpx2.Response:
    async with httpx2.AsyncClient() as http:
        return await http.post(gw.url + "/token", data=form)


async def full_grant(
    gw: Any, user: httpx2.AsyncClient, name: str = "Test App"
) -> tuple[dict[str, Any], OAuthToken]:
    app = await register_app(gw, name)
    verifier, challenge = pkce()
    req = await start_authorization(gw, app["client_id"], challenge)
    code = (await approve(user, req))["code"][0]
    r = await token_request(
        gw,
        grant_type="authorization_code",
        code=code,
        redirect_uri=REDIRECT,
        client_id=app["client_id"],
        code_verifier=verifier,
    )
    assert r.status_code == 200, r.text
    return app, OAuthToken.model_validate(r.json())


class MemoryStorage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.info = client_info


async def test_OA01_sdk_oauth_client_end_to_end(
    oauth_gateway: Any, upstreams: Any, database_url: str
) -> None:
    """OA-01: discovery → registration → browser approval → tools listed and called → audit `oauth`."""
    gw = oauth_gateway
    storage = MemoryStorage()
    consent_seen: list[dict[str, Any]] = []
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")

        async def redirect_handler(authorization_url: str) -> None:
            """The user's browser: open the authorize URL, land on the consent page, press Approve."""
            async with httpx2.AsyncClient() as browser:
                r = await browser.get(authorization_url, follow_redirects=False)
            req = parse_qs(urlparse(r.headers["location"]).query)["req"][0]
            consent_seen.append((await admin.get(f"/api/oauth/requests/{req}")).json()["request"])
            callback.update({k: v[0] for k, v in (await approve(admin, req)).items()})

        callback: dict[str, str] = {}

        async def callback_handler() -> AuthorizationCodeResult:
            return AuthorizationCodeResult(code=callback["code"], state=callback.get("state"))

        provider = OAuthClientProvider(
            server_url=gw.url + "/mcp",
            client_metadata=OAuthClientMetadata(
                client_name="Claude Code (test)",
                redirect_uris=[REDIRECT],  # type: ignore[list-item]
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                scope="mcp",
            ),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )
        async with (
            httpx2.AsyncClient(auth=provider, timeout=30) as http,
            Client(streamable_http_client(gw.url + "/mcp", http_client=http), cache=None) as client,
        ):
            assert ECHO in [t.name for t in (await client.list_tools()).tools]
            result = await client.call_tool(ECHO, {"text": "via oauth"})
            assert not result.is_error

        assert len(consent_seen) == 1
        assert consent_seen[0]["clientName"] == "Claude Code (test)"
        assert consent_seen[0]["redirectHost"] == "localhost:8765" and consent_seen[0]["scopes"] == ["mcp"]
        assert storage.tokens is not None and storage.tokens.access_token.startswith("mgo_")
        assert storage.tokens.refresh_token and storage.tokens.refresh_token.startswith("mgr_")
        assert storage.info is not None and storage.info.client_secret is None

        apps = (await admin.get("/api/oauth/apps")).json()["apps"]
        assert [(a["clientName"], a["userEmail"], a["activeTokens"]) for a in apps] == [
            ("Claude Code (test)", "admin@example.com", 2)
        ]
        # search mode takes the same token (D98 #3)
        async with mcp_client(gw, storage.tokens.access_token, "/mcp/search") as c:
            names = [t.name for t in (await c.list_tools()).tools]
            assert names == ["search_tools", "call_tool", "list_servers"]
            assert not (await c.call_tool("call_tool", {"name": ECHO, "arguments": {"text": "x"}})).is_error

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            "SELECT credential_type, credential_id, user_email, client_mode, outcome FROM audit_log "
            "WHERE exposed_name = $1 AND outcome = 'success' ORDER BY id",
            ECHO,
        )
    finally:
        await conn.close()
    assert [(r["credential_type"], r["user_email"], r["client_mode"]) for r in rows] == [
        ("oauth", "admin@example.com", "full"),
        ("oauth", "admin@example.com", "search"),
    ]
    assert all(r["credential_id"].startswith("oauth:") for r in rows)


async def test_discovery_metadata_and_401_challenge(oauth_gateway: Any) -> None:
    gw = oauth_gateway
    async with httpx2.AsyncClient() as http:
        unauth = await http.post(gw.url + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert unauth.status_code == 401
        challenge = unauth.headers["www-authenticate"]
        assert f'resource_metadata="{gw.url}/.well-known/oauth-protected-resource/mcp"' in challenge
        resource = (await http.get(gw.url + "/.well-known/oauth-protected-resource/mcp")).json()
        assert resource["resource"] == gw.url + "/mcp"
        assert [s.rstrip("/") for s in resource["authorization_servers"]] == [gw.url]
        search = (await http.get(gw.url + "/.well-known/oauth-protected-resource/mcp/search")).json()
        assert search["resource"] == gw.url + "/mcp/search"
        meta = (await http.get(gw.url + "/.well-known/oauth-authorization-server")).json()
    assert (
        meta["authorization_endpoint"] == gw.url + "/authorize"
        and meta["token_endpoint"] == gw.url + "/token"
    )
    assert (
        meta["registration_endpoint"] == gw.url + "/register"
        and meta["revocation_endpoint"] == gw.url + "/revoke"
    )
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
    assert meta["grant_types_supported"] == ["authorization_code", "refresh_token"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"token_endpoint_auth_method": "client_secret_post"}, "Only public clients"),
        ({"token_endpoint_auth_method": None}, "Only public clients"),
        ({"redirect_uris": ["http://example.com/cb"]}, "https, or http on localhost"),
        ({"redirect_uris": ["https://app.example.com/cb#frag"]}, "fragment"),
        ({"redirect_uris": ["javascript:alert(1)"]}, "https, or http on localhost"),
        ({"scope": "mcp admin"}, "not valid"),
    ],
)
async def test_dynamic_registration_rules(oauth_gateway: Any, change: dict[str, Any], message: str) -> None:
    body = {
        "client_name": "Evil",
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        **change,
    }
    async with httpx2.AsyncClient() as http:
        r = await http.post(oauth_gateway.url + "/register", json=body)
    assert r.status_code == 400 and message in r.text, r.text


async def test_registration_accepts_https_and_loopback_and_cleans_the_name(
    oauth_gateway: Any, database_url: str
) -> None:
    body = {
        "client_name": "  Web\x00App\n" + "x" * 200,
        "redirect_uris": ["https://app.example.com/cb", "http://127.0.0.1:3000/cb", "http://[::1]:3000/cb"],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    async with httpx2.AsyncClient() as http:
        r = await http.post(oauth_gateway.url + "/register", json=body)
    assert r.status_code == 201, r.text
    info = r.json()
    assert info.get("client_secret") is None and info["scope"] == "mcp"
    conn = await asyncpg.connect(database_url)
    try:
        stored = await conn.fetchval(
            "SELECT metadata->>'client_name' FROM oauth_clients WHERE client_id = $1", info["client_id"]
        )
    finally:
        await conn.close()
    assert (
        stored.startswith("WebApp") and len(stored) <= 100 and stored.isprintable()
    )  # what the consent page shows


async def test_registration_cap(oauth_gateway: Any, database_url: str) -> None:
    from wmg_gateway.oauth.provider import MAX_CLIENTS

    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "INSERT INTO oauth_clients (client_id, metadata) SELECT 'bulk-' || g, '{}'::jsonb "
            "FROM generate_series(1, $1) g",
            MAX_CLIENTS,
        )
    finally:
        await conn.close()
    body = {
        "client_name": "One too many",
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }
    async with httpx2.AsyncClient() as http:
        r = await http.post(oauth_gateway.url + "/register", json=body)
    assert r.status_code == 400 and "Too many registered apps" in r.text, r.text


async def test_consent_rules_deny_expiry_and_single_answer(oauth_gateway: Any, database_url: str) -> None:
    gw = oauth_gateway
    app = await register_app(gw)
    _verifier, challenge = pkce()
    async with session(gw) as admin:
        await add_member(gw, admin)
        # anonymous browsers can't see or answer a request
        req = await start_authorization(gw, app["client_id"], challenge)
        async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
            assert (await anon.get(f"/api/oauth/requests/{req}")).status_code == 401
            assert (await anon.post(f"/api/oauth/requests/{req}/approve")).status_code == 401
        # CSRF header is required to answer
        async with httpx2.AsyncClient(base_url=gw.url, cookies=admin.cookies) as no_header:
            assert (await no_header.post(f"/api/oauth/requests/{req}/approve")).status_code == 403
        # deny → redirect with access_denied and the app's state; the request is gone afterwards
        denied = await admin.post(f"/api/oauth/requests/{req}/deny")
        query = parse_qs(urlparse(denied.json()["redirectUrl"]).query)
        assert query == {"error": ["access_denied"], "state": ["st-1"]}
        assert (await admin.post(f"/api/oauth/requests/{req}/approve")).status_code == 404
        assert (await admin.get(f"/api/oauth/requests/{req}")).status_code == 404
        assert (await admin.get("/api/oauth/requests/not-a-request")).status_code == 404
        # approving twice only works once
        req2 = await start_authorization(gw, app["client_id"], challenge)
        assert "code" in await approve(admin, req2)
        assert (await admin.post(f"/api/oauth/requests/{req2}/approve")).status_code == 404
        # expired
        req3 = await start_authorization(gw, app["client_id"], challenge)
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute(
                "UPDATE oauth_requests SET expires_at = now() - interval '1 second' WHERE id = $1", req3
            )
        finally:
            await conn.close()
        assert (await admin.post(f"/api/oauth/requests/{req3}/approve")).status_code == 404


async def test_authorize_rejects_bad_requests(oauth_gateway: Any) -> None:
    gw = oauth_gateway
    app = await register_app(gw)
    _v, challenge = pkce()
    good = {
        "response_type": "code",
        "client_id": app["client_id"],
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "s",
    }
    async with httpx2.AsyncClient() as http:
        for change in (
            {"client_id": "nobody"},
            {"redirect_uri": "http://localhost:1/other"},
            {"code_challenge_method": "plain"},
            {"code_challenge": None},
            {"scope": "mcp admin"},
        ):
            query = {k: v for k, v in {**good, **change}.items() if v is not None}
            r = await http.get(gw.url + "/authorize", params=query, follow_redirects=False)
            assert r.status_code in (302, 400), (change, r.status_code)
            if (
                r.status_code == 302
            ):  # errors are sent to the app's own redirect_uri, never to the consent page
                assert r.headers["location"].startswith(REDIRECT) and "error=" in r.headers["location"], (
                    change
                )
            else:
                assert "error" in r.json(), change


async def test_pending_request_cap(oauth_gateway: Any, database_url: str) -> None:
    from wmg_gateway.oauth.provider import MAX_PENDING_REQUESTS

    gw = oauth_gateway
    app = await register_app(gw)
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "INSERT INTO oauth_requests (id, client_id, params, expires_at) "
            "SELECT 'bulk-' || g, $1, '{}'::jsonb, "
            "now() + interval '5 minutes' FROM generate_series(1, $2) g",
            app["client_id"],
            MAX_PENDING_REQUESTS,
        )
    finally:
        await conn.close()
    _v, challenge = pkce()
    query = {
        "response_type": "code",
        "client_id": app["client_id"],
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "s",
    }
    async with httpx2.AsyncClient() as http:
        r = await http.get(gw.url + "/authorize", params=query, follow_redirects=False)
    assert r.status_code == 302 and "error=temporarily_unavailable" in r.headers["location"]


async def test_code_exchange_rules(oauth_gateway: Any, upstreams: Any) -> None:
    """PKCE is checked, redirect_uri must match, a code works once: replaying it revokes what it issued."""
    gw = oauth_gateway
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")
        app = await register_app(gw)
        cid = app["client_id"]
        verifier, challenge = pkce()
        base = {"grant_type": "authorization_code", "redirect_uri": REDIRECT, "client_id": cid}

        code = (await approve(admin, await start_authorization(gw, cid, challenge)))["code"][0]
        wrong = await token_request(gw, **base, code=code, code_verifier=pkce()[0])
        assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
        elsewhere = await token_request(
            gw, **{**base, "redirect_uri": "http://localhost:1/x"}, code=code, code_verifier=verifier
        )
        assert elsewhere.status_code == 400
        other_app = await register_app(gw, "Other")
        stolen = await token_request(
            gw, **{**base, "client_id": other_app["client_id"]}, code=code, code_verifier=verifier
        )
        assert stolen.status_code == 400  # a code is bound to the app it was issued to
        first = await token_request(gw, **base, code=code, code_verifier=verifier)
        assert first.status_code == 200
        access = first.json()["access_token"]
        async with mcp_client(gw, access) as c:
            assert not (await c.call_tool(ECHO, {"text": "x"})).is_error

        replay = await token_request(gw, **base, code=code, code_verifier=verifier)
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        assert await rejected(gw, access)  # the replay revoked what the first exchange issued


async def test_refresh_rotation_and_reuse_detection(oauth_gateway: Any, upstreams: Any) -> None:
    gw = oauth_gateway
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")
        app, tokens = await full_grant(gw, admin)
        cid = app["client_id"]
        assert tokens.refresh_token and tokens.expires_in == 3600 and tokens.scope == "mcp"

        r = await token_request(
            gw, grant_type="refresh_token", client_id=cid, refresh_token=tokens.refresh_token
        )
        assert r.status_code == 200, r.text
        second = OAuthToken.model_validate(r.json())
        assert second.access_token != tokens.access_token and second.refresh_token != tokens.refresh_token
        assert await rejected(gw, tokens.access_token)  # rotated out with its refresh token
        async with mcp_client(gw, second.access_token) as c:
            assert not (await c.call_tool(ECHO, {"text": "x"})).is_error

        # presenting the old refresh token again looks like theft: the whole family is revoked
        reuse = await token_request(
            gw, grant_type="refresh_token", client_id=cid, refresh_token=tokens.refresh_token
        )
        assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
        assert await rejected(gw, second.access_token)
        assert second.refresh_token
        dead = await token_request(
            gw, grant_type="refresh_token", client_id=cid, refresh_token=second.refresh_token
        )
        assert dead.status_code == 400
        # another app's refresh token can't be used by this one
        _other, other_tokens = await full_grant(gw, admin, "Other")
        assert other_tokens.refresh_token
        cross = await token_request(
            gw, grant_type="refresh_token", client_id=cid, refresh_token=other_tokens.refresh_token
        )
        assert cross.status_code == 400
        assert not await rejected(gw, other_tokens.access_token)


async def test_expired_tokens_are_rejected(oauth_gateway: Any, database_url: str) -> None:
    gw = oauth_gateway
    async with session(gw) as admin:
        app, tokens = await full_grant(gw, admin)
    assert not await rejected(gw, tokens.access_token)
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "UPDATE oauth_tokens SET expires_at = now() - interval '1 minute' WHERE kind = 'access'"
        )
    finally:
        await conn.close()
    assert await rejected(gw, tokens.access_token)
    assert tokens.refresh_token
    refreshed = await token_request(
        gw, grant_type="refresh_token", client_id=app["client_id"], refresh_token=tokens.refresh_token
    )
    assert refreshed.status_code == 200  # the refresh token outlives the access token
    assert not await rejected(gw, refreshed.json()["access_token"])
    assert await rejected(gw, "mgo_" + "A" * 43)  # unknown
    assert await rejected(gw, "mgo_")


async def test_revocation_endpoint_and_connected_apps(oauth_gateway: Any) -> None:
    gw = oauth_gateway
    async with session(gw) as admin:
        await add_member(gw, admin)
        app, tokens = await full_grant(gw, admin)
        cid = app["client_id"]
        async with httpx2.AsyncClient() as http:
            # RFC 7009 from the app itself: revoking the refresh token ends the whole authorization.
            # (the SDK's handler requires a `client_secret` form field; public clients send it empty)
            assert tokens.refresh_token
            r = await http.post(
                gw.url + "/revoke",
                data={"token": tokens.refresh_token, "client_id": cid, "client_secret": ""},
            )
            assert r.status_code == 200
            # unknown tokens are not an error
            assert (
                await http.post(
                    gw.url + "/revoke", data={"token": "mgr_nope", "client_id": cid, "client_secret": ""}
                )
            ).status_code == 200
        assert await rejected(gw, tokens.access_token)
        assert (await admin.get("/api/oauth/apps")).json()["apps"] == []


async def test_connected_apps_per_user_and_revoke_from_dashboard(oauth_gateway: Any, upstreams: Any) -> None:
    gw = oauth_gateway
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")
        await add_member(gw, admin)
        async with session(gw, MEMBER) as member:
            app, member_tokens = await full_grant(gw, member, "Ritika's app")
            cid = app["client_id"]
            _admin_app, admin_tokens = await full_grant(gw, admin, "Rohan's app")

            mine = (await member.get("/api/oauth/apps")).json()["apps"]
            assert [(a["clientName"], a["userEmail"]) for a in mine] == [
                ("Ritika's app", "ritika@example.com")
            ]
            everyone = (await admin.get("/api/oauth/apps?all=1")).json()["apps"]
            assert sorted(a["clientName"] for a in everyone) == ["Ritika's app", "Rohan's app"]
            assert (await member.get("/api/oauth/apps?all=1")).status_code == 403

            member_id = mine[0]["userId"]
            # a member can't touch someone else's grant (or learn it exists)
            assert (await member.delete(f"/api/oauth/apps/{_admin_app['client_id']}")).status_code == 404
            assert not await rejected(gw, admin_tokens.access_token)
            # member tokens work, are role-limited, and stop the moment an admin revokes the pair
            async with mcp_client(gw, member_tokens.access_token) as c:
                assert not (await c.call_tool(ECHO, {"text": "x"})).is_error
            assert (await admin.delete(f"/api/oauth/apps/{cid}?userId={member_id}")).status_code == 204
            assert await rejected(gw, member_tokens.access_token)
            assert (await member.get("/api/oauth/apps")).json()["apps"] == []
            assert (
                await admin.delete(f"/api/oauth/apps/{cid}?userId={member_id}")
            ).status_code == 204  # idempotent
            # members can revoke their own
            _app2, again = await full_grant(gw, member, "Ritika's app")
            second = (await member.get("/api/oauth/apps")).json()["apps"][0]["clientId"]
            assert (await member.delete(f"/api/oauth/apps/{second}")).status_code == 204
            assert await rejected(gw, again.access_token)
            assert (await member.delete("/api/oauth/apps/never-heard-of-it")).status_code == 404


async def test_role_and_status_are_read_live(oauth_gateway: Any, upstreams: Any, database_url: str) -> None:
    """A member's token can't call admin-only tools; disabling the user stops it at once and is audited."""
    gw = oauth_gateway
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")
        await add_member(gw, admin)
        server = (await admin.get("/api/servers")).json()["servers"][0]["id"]
        assert (
            await admin.put(f"/api/servers/{server}/tools/echo/policy", json={"adminOnly": True})
        ).status_code == 200
        async with session(gw, MEMBER) as member:
            _app, tokens = await full_grant(gw, member)
            member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
        async with mcp_client(gw, tokens.access_token) as c:
            with pytest.raises(MCPError) as err:
                await c.call_tool(ECHO, {"text": "x"})
        assert err.value.error.code == -32602 or err.value.error.code == -32010
        assert (await admin.patch(f"/api/users/{member_id}", json={"status": "disabled"})).status_code == 200
        assert await rejected(gw, tokens.access_token)
    conn = await asyncpg.connect(database_url)
    try:
        denied = await conn.fetch(
            "SELECT credential_type, error_message FROM audit_log "
            "WHERE outcome = 'denied' AND credential_type = 'oauth'"
        )
    finally:
        await conn.close()
    assert ("oauth", "User disabled") in [(r["credential_type"], r["error_message"]) for r in denied]
    assert ("oauth", "Admin-only tool") in [(r["credential_type"], r["error_message"]) for r in denied]


async def test_disabled_user_cannot_refresh(oauth_gateway: Any) -> None:
    gw = oauth_gateway
    async with session(gw) as admin:
        await add_member(gw, admin)
        async with session(gw, MEMBER) as member:
            app, tokens = await full_grant(gw, member)
            member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
        assert (await admin.patch(f"/api/users/{member_id}", json={"status": "disabled"})).status_code == 200
    assert tokens.refresh_token
    r = await token_request(
        gw, grant_type="refresh_token", client_id=app["client_id"], refresh_token=tokens.refresh_token
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


async def test_rate_limit_counts_per_user_and_app(
    gateway_factory: Callable[..., Any], upstreams: Any
) -> None:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    gw = gateway_factory(
        port=port,
        public_gateway_url=base,
        public_mcp_url=base + "/mcp",
        public_dashboard_url=base,
        rate_limit_per_min=2,
    )
    async with session(gw) as admin:
        await register_upstream(admin, upstreams.modern.url + "/mcp")
        _a, first = await full_grant(gw, admin, "First")
        _b, second = await full_grant(gw, admin, "Second")
        import time

        if time.time() % 60 > 50:  # keep the burst inside one calendar minute
            import anyio

            await anyio.sleep(11)
        async with mcp_client(gw, first.access_token) as c:
            for _ in range(2):
                assert not (await c.call_tool(ECHO, {"text": "x"})).is_error
            with pytest.raises(MCPError) as err:
                await c.call_tool(ECHO, {"text": "x"})
            assert err.value.error.code == -32011
        async with mcp_client(
            gw, second.access_token
        ) as c:  # another app of the same user has its own counter
            assert not (await c.call_tool(ECHO, {"text": "x"})).is_error


async def test_housekeeping_clears_oauth_leftovers(oauth_gateway: Any, database_url: str) -> None:
    from datetime import UTC, datetime, timedelta

    from wmg_gateway.db.pool import create_pool
    from wmg_gateway.housekeeping import Housekeeping

    gw = oauth_gateway
    async with session(gw) as admin:
        used, tokens = await full_grant(gw, admin, "Used")
        unused = await register_app(gw, "Never approved")
    pool = await create_pool(database_url, 1, 2)
    try:
        conn = await asyncpg.connect(database_url)
        try:
            await conn.execute("UPDATE oauth_clients SET created_at = now() - interval '8 days'")
            await conn.execute(
                "UPDATE oauth_tokens SET revoked_at = now() - interval '8 days' WHERE kind = 'refresh'"
            )
            await conn.execute(
                "INSERT INTO oauth_requests (id, client_id, params, expires_at) "
                "VALUES ('old', $1, '{}'::jsonb, now() - interval '1 minute')",
                used["client_id"],
            )
            await Housekeeping(pool).tick(datetime.now(UTC) + timedelta(seconds=1))
            clients = [r["client_id"] for r in await conn.fetch("SELECT client_id FROM oauth_clients")]
            assert clients == [used["client_id"]] and unused["client_id"] not in clients
            kinds = [r["kind"] for r in await conn.fetch("SELECT kind FROM oauth_tokens")]
            assert kinds == [
                "access"
            ]  # the refresh token revoked 8 days ago is gone, the live access token stays
            assert await conn.fetchval("SELECT count(*) FROM oauth_requests") == 0
        finally:
            await conn.close()
    finally:
        await pool.close()
    assert not await rejected(gw, tokens.access_token)

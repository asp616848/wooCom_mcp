"""TESTING §3.4 (TEST-A-*): /api permission matrix, CSRF, member redaction, no credential in any response."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

SENTINEL = "SENTINEL-CRED-123"
CSRF = {"X-WMG-CSRF": "1"}
ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
MEMBER = {"email": "ritika@example.com", "password": "ritika-password-1"}
SEEN: list[str] = []
ID = str(uuid.uuid4())  # any id: authorization is checked before the lookup

# (method, path, body) for every /api route that needs a signed-in user
ANY_USER = [
    ("GET", "/api/auth/me", None),
    ("GET", "/api/keys", None),
    ("POST", "/api/keys", {"name": "k"}),
    ("DELETE", f"/api/keys/{ID}", None),
    ("GET", "/api/servers", None),
    ("GET", f"/api/servers/{ID}/tools", None),
    ("GET", "/api/audit", None),
    ("GET", "/api/connect-info", None),
]
ADMIN_ONLY = [
    ("GET", "/api/users", None),
    ("PATCH", f"/api/users/{ID}", {"role": "member"}),
    ("GET", "/api/invites", None),
    ("POST", "/api/invites", {"email": "x@example.com", "role": "member"}),
    ("DELETE", f"/api/invites/{ID}", None),
    ("POST", "/api/servers/test", {"url": "http://x.mcp-upstreams.svc/mcp", "authType": "none"}),
    ("POST", "/api/servers", {"slug": "zz", "name": "Z", "url": "http://x.mcp-upstreams.svc/mcp"}),
    ("PATCH", f"/api/servers/{ID}", {"name": "Z"}),
    ("PUT", f"/api/servers/{ID}/tools/echo/policy", {"adminOnly": True}),
    ("DELETE", f"/api/servers/{ID}", None),
    ("GET", "/api/insights", None),
]


async def _record(response: httpx2.Response) -> None:
    await response.aread()
    SEEN.append(response.text)


@asynccontextmanager
async def client(gw: Any, creds: dict[str, str] | None = None) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(
        base_url=gw.url, headers=CSRF, timeout=30, event_hooks={"response": [_record]}
    ) as http:
        if creds:
            assert (await http.post("/api/auth/login", json=creds)).status_code == 200
        yield http


async def call(http: httpx2.AsyncClient, method: str, path: str, body: Any) -> httpx2.Response:
    return await http.request(method, path, json=body)


async def test_A_permission_matrix(gateway_factory: Any, upstreams: Any) -> None:
    gw = gateway_factory()
    async with client(gw, ADMIN) as admin:
        r = await admin.post(
            "/api/servers",
            json={"slug": "fx-secured", "name": "Secured", "url": upstreams.secured.url + "/mcp",
                  "authType": "bearer", "credential": SENTINEL},
        )  # fmt: skip
        assert r.status_code == 201
        server_id = r.json()["server"]["id"]
        token = (
            (await admin.post("/api/invites", json={"email": MEMBER["email"], "role": "member"}))
            .json()["inviteUrl"]
            .rsplit("/", 1)[1]
        )
        admin_keys = await admin.post("/api/keys", json={"name": "admin-key"})
        admin_id = (await admin.get("/api/auth/me")).json()["user"]["id"]
        # exercise every admin read so its body is scanned for the credential
        for path in (
            "/api/servers",
            f"/api/servers/{server_id}/tools",
            "/api/users",
            "/api/invites",
            "/api/keys?all=1",
        ):
            assert (await admin.get(path)).status_code == 200
        patched = await admin.patch(f"/api/servers/{server_id}", json={"name": "Secured 2"})
        assert patched.status_code == 200 and patched.json()["server"]["hasCredential"] is True
    async with client(gw) as anon:
        accepted = await anon.post(
            f"/api/invites/{token}/accept", json={"name": "Ritika", "password": MEMBER["password"]}
        )
        assert accepted.status_code == 200
        for method, path, body in ANY_USER + ADMIN_ONLY:  # unauthenticated → 401
            # the accept call set a session cookie on this client; drop it
            anon.cookies.clear()
            r = await call(anon, method, path, body)
            assert r.status_code == 401, (method, path, r.status_code)
            assert r.json()["error"]["code"] == "UNAUTHENTICATED"

    async with client(gw, MEMBER) as member:
        for method, path, body in ADMIN_ONLY:  # member on admin routes → 403
            r = await call(member, method, path, body)
            assert r.status_code == 403, (method, path, r.status_code)
            assert r.json()["error"]["code"] == "FORBIDDEN"
        view = (await member.get("/api/servers")).json()["servers"]
        assert view and all(
            not {"url", "authType", "authHeaderName", "hasCredential", "lastError"} & set(s) for s in view
        )
        assert (await member.get(f"/api/keys?userId={admin_id}")).status_code == 403
        assert (await member.delete(f"/api/keys/{admin_keys.json()['id']}")).status_code == 404
        no_csrf = await member.post("/api/keys", json={"name": "k"}, headers={"X-WMG-CSRF": ""})
        assert no_csrf.status_code == 403 and "X-WMG-CSRF" in no_csrf.json()["error"]["message"]
        own = await member.post("/api/keys", json={"name": "mine"})
        assert own.status_code == 201 and own.json()["key"].startswith("mgw_")
        audit = (await member.get(f"/api/audit?userId={admin_id}")).json()  # forced to self
        assert all(item["userEmail"] == MEMBER["email"] for item in audit["items"])
        info = (await member.get("/api/connect-info")).json()
        assert info["mcpUrl"].endswith("/mcp") and "<YOUR_API_KEY>" in info["snippets"]["claudeCode"]

    assert SEEN and not any(SENTINEL in body for body in SEEN)


async def test_A_audit_filters_and_pagination(
    gateway_factory: Any, upstreams: Any, database_url: str
) -> None:
    import asyncpg

    gw = gateway_factory()
    conn = await asyncpg.connect(database_url)
    try:
        rows = [
            (
                "tools/call",
                "success" if i % 3 else "timeout",
                i,
                "gst" if i % 2 else "pincode",
                "a@example.com",
            )
            for i in range(1, 8)
        ]
        await conn.executemany(
            "INSERT INTO audit_log (method, outcome, duration_ms, server_slug, user_email) "
            "VALUES ($1, $2, $3, $4, $5)",
            rows,
        )
    finally:
        await conn.close()
    async with client(gw, ADMIN) as admin:
        first = (await admin.get("/api/audit?limit=3")).json()
        assert [i["durationMs"] for i in first["items"]] == [7, 6, 5] and first["nextCursor"]
        second = (await admin.get(f"/api/audit?limit=3&cursor={first['nextCursor']}")).json()
        assert [i["durationMs"] for i in second["items"]] == [4, 3, 2]
        gst = (await admin.get("/api/audit?serverSlug=gst")).json()["items"]
        assert {i["serverSlug"] for i in gst} == {"gst"} and len(gst) == 4
        timeouts = (await admin.get("/api/audit?outcome=timeout")).json()
        assert [i["durationMs"] for i in timeouts["items"]] == [6, 3] and timeouts["nextCursor"] is None
        assert (await admin.get("/api/audit?outcome=bogus")).status_code == 422
        assert (await admin.get("/api/audit?limit=500")).status_code == 422

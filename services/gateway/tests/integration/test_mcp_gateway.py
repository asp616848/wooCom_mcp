"""P8: the gateway's /mcp endpoint end to end (TESTING §3.3, G-01 … G-13) + servers/keys/users/invites API.

Each test gets a fresh database and its own gateway(s) on real HTTP; fixture upstreams are shared per session.
"""

import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import asyncpg
import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
CSRF = {"X-WMG-CSRF": "1"}
FIXTURE_TOKEN = "fixture-token-123"


@asynccontextmanager
async def session(gw: Any, creds: dict[str, str] = ADMIN) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        r = await http.post("/api/auth/login", json=creds)
        assert r.status_code == 200, r.text
        yield http


async def new_key(http: httpx2.AsyncClient, name: str = "test") -> str:
    r = await http.post("/api/keys", json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["key"])


async def register(http: httpx2.AsyncClient, slug: str, url: str, **extra: Any) -> dict[str, Any]:
    r = await http.post("/api/servers", json={"slug": slug, "name": slug.upper(), "url": url, **extra})
    assert r.status_code == 201, r.text
    server: dict[str, Any] = r.json()["server"]
    return server


@asynccontextmanager
async def mcp(gw: Any, key: str, mode: str = "auto") -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
        Client(streamable_http_client(gw.mcp_url, http_client=http), mode=mode, cache=None) as client,  # type: ignore[arg-type]
    ):
        yield client


async def eventually(check: Callable[[], Awaitable[bool]], within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not await check():
        assert time.monotonic() < deadline, "condition not reached in time"
        await anyio.sleep(0.1)


async def tool_names(gw: Any, key: str) -> list[str]:
    async with mcp(gw, key) as c:
        return [t.name for t in (await c.list_tools()).tools]


async def has_tools(gw: Any, key: str, *names: str) -> bool:
    listed = await tool_names(gw, key)
    return all(n in listed for n in names)


async def db(database_url: str) -> Any:
    return await asyncpg.connect(database_url)


async def test_G01_G02_G07_G13_dual_era_register_list_call(
    gateway_factory: Any, upstreams: Any, database_url: str
) -> None:
    gw = gateway_factory()
    async with session(gw) as http:
        modern = await register(http, "fx-modern", upstreams.modern.url + "/mcp")
        legacy = await register(http, "fx-legacy", upstreams.legacy.url + "/mcp")
        copy = await register(http, "fx-copy", upstreams.modern.url + "/mcp")
        key = await new_key(http)
    assert (modern["status"], modern["protocolEra"], modern["toolCount"]) == ("connected", "modern", 4)
    assert (legacy["status"], legacy["protocolEra"], legacy["toolCount"]) == ("connected", "legacy", 1)
    assert copy["status"] == "connected"
    await eventually(lambda: has_tools(gw, key, "fx-modern__echo", "fx-legacy__echo", "fx-copy__echo"))

    for mode in ("auto", "legacy"):  # G-13: a modern and a legacy downstream client
        async with mcp(gw, key, mode) as c:
            listed = await c.list_tools()
            names = [t.name for t in listed.tools]
            assert names == sorted(names)  # G-02
            assert all(n.split("__")[0] in ("fx-modern", "fx-legacy", "fx-copy") for n in names)
            if mode == "auto":
                assert c.protocol_version == "2026-07-28"
                assert (listed.ttl_ms, listed.cache_scope) == (10000, "private")
            echo = next(t for t in listed.tools if t.name == "fx-modern__echo")
            assert echo.description == "[fx-modern] Echo the text back" and echo.title == "FX-MODERN: echo"
            # G-07: same tool name on three servers, each routed to its own upstream
            assert (await c.call_tool("fx-modern__echo", {"text": "hi"})).content[0].text == "hi"  # type: ignore[union-attr]
            assert (await c.call_tool("fx-legacy__echo", {"text": "hi"})).content[0].text == "legacy:hi"  # type: ignore[union-attr]
            assert (await c.call_tool("fx-copy__echo", {"text": "yo"})).content[0].text == "yo"  # type: ignore[union-attr]
            failed = await c.call_tool("fx-modern__fail", {})
            assert failed.is_error and "boom" in failed.content[0].text  # type: ignore[union-attr]

    conn = await db(database_url)
    try:
        eras = dict(await conn.fetch("SELECT slug, protocol_era FROM mcp_servers"))
        assert eras == {"fx-modern": "modern", "fx-legacy": "legacy", "fx-copy": "modern"}
        outcomes = [r["outcome"] for r in await conn.fetch("SELECT outcome FROM audit_log ORDER BY id")]
        assert outcomes == ["success", "success", "success", "tool_error"] * 2  # every call audited
        row = await conn.fetchrow("SELECT * FROM audit_log WHERE outcome = 'tool_error' LIMIT 1")
        assert row["user_email"] == "admin@example.com" and row["credential_type"] == "api_key"
        assert row["tool_name"] == "fail" and row["exposed_name"] == "fx-modern__fail"
    finally:
        await conn.close()


async def test_modern_call_needs_no_extra_tools_list(gateway_factory: Any, upstreams: Any) -> None:
    gw = gateway_factory(health_interval_ms=60000)  # keep the health loop out of the request count
    async with session(gw) as http:
        await register(http, "fx-modern", upstreams.modern.url + "/mcp")
        key = await new_key(http)
    await eventually(lambda: has_tools(gw, key, "fx-modern__echo"))
    before = len(upstreams.modern_rec.methods)
    async with mcp(gw, key) as c:
        for _ in range(3):
            await c.call_tool("fx-modern__echo", {"text": "x"})
    assert upstreams.modern_rec.methods[before:] == ["tools/call"] * 3


async def test_G03_dead_upstream_unreachable_and_hidden(
    gateway_factory: Any, upstreams: Any, fresh_modern: Any
) -> None:
    dying, _ = fresh_modern()
    gw = gateway_factory()
    async with session(gw) as http:
        await register(http, "fx-dying", dying.url + "/mcp")
        await register(http, "fx-modern", upstreams.modern.url + "/mcp")
        closed = await register(http, "fx-closed", upstreams.closed_url)
        key = await new_key(http)
        assert (closed["status"], closed["lastErrorCode"]) == ("unreachable", "CONNECTION_REFUSED")
        await eventually(lambda: has_tools(gw, key, "fx-dying__echo", "fx-modern__echo"))
        dying.stop()
        started = time.monotonic()

        async def unreachable() -> bool:
            servers = (await http.get("/api/servers")).json()["servers"]
            return next(s for s in servers if s["slug"] == "fx-dying")["status"] == "unreachable"

        await eventually(unreachable, within=5)
        assert time.monotonic() - started < 3.5  # within one health tick (1 s) + probe time
    names = await tool_names(gw, key)
    assert "fx-modern__echo" in names and not any(n.startswith(("fx-dying__", "fx-closed__")) for n in names)
    async with mcp(gw, key) as c:
        assert (await c.call_tool("fx-modern__echo", {"text": "ok"})).content[0].text == "ok"  # type: ignore[union-attr]
        # hidden from the list but still in the catalog: a direct call gets a readable tool error
        result = await c.call_tool("fx-dying__echo", {"text": "x"})
        assert result.is_error and "unreachable" in result.content[0].text  # type: ignore[union-attr]


async def test_G04_slow_upstream_times_out_others_unaffected(gateway_factory: Any, upstreams: Any) -> None:
    gw = gateway_factory()
    async with session(gw) as http:
        await register(http, "fx-slow", upstreams.modern.url + "/mcp", timeoutMs=1000)
        await register(http, "fx-legacy", upstreams.legacy.url + "/mcp")
        key = await new_key(http)
    await eventually(lambda: has_tools(gw, key, "fx-slow__slow", "fx-legacy__echo"))
    timings: dict[str, float] = {}

    async with mcp(gw, key) as c:

        async def slow() -> None:
            t0 = time.monotonic()
            result = await c.call_tool("fx-slow__slow", {"ms": 5000})
            timings["slow"] = time.monotonic() - t0
            assert result.is_error and result.content[0].text == "[gateway] 'fx-slow' timed out after 1000ms"  # type: ignore[union-attr]

        async def fast() -> None:
            await anyio.sleep(0.2)
            t0 = time.monotonic()
            result = await c.call_tool("fx-legacy__echo", {"text": "quick"})
            timings["fast"] = time.monotonic() - t0
            assert result.content[0].text == "legacy:quick"  # type: ignore[union-attr]

        async with anyio.create_task_group() as tg:
            tg.start_soon(slow)
            tg.start_soon(fast)
    assert 0.9 < timings["slow"] < 2.0
    assert timings["fast"] < 0.5, timings
    async with session(gw) as http:  # slow ≠ dead
        servers = (await http.get("/api/servers")).json()["servers"]
        assert next(s for s in servers if s["slug"] == "fx-slow")["status"] == "connected"


async def test_G05_concurrency_cap(gateway_factory: Any, fresh_modern: Any) -> None:
    upstream, concurrency = fresh_modern()
    try:
        gw = gateway_factory()
        async with session(gw) as http:
            await register(http, "fx-busy", upstream.url + "/mcp", timeoutMs=2000)
            key = await new_key(http)
        await eventually(lambda: has_tools(gw, key, "fx-busy__slow"))
        results: list[Any] = []
        in_flight_before_timeouts = 0
        t0 = time.monotonic()
        async with mcp(gw, key) as c:

            async def one() -> None:
                results.append(await c.call_tool("fx-busy__slow", {"ms": 3000}))

            async def sample() -> None:
                # Before the first 2 s timeout: the cap is at the upstream, 4 calls wait in the gateway.
                # (Later the upstream keeps sleeping on abandoned calls, so its count is not the cap.)
                nonlocal in_flight_before_timeouts
                await anyio.sleep(1.5)
                in_flight_before_timeouts = concurrency.current

            async with anyio.create_task_group() as tg:
                tg.start_soon(sample)
                for _ in range(20):
                    tg.start_soon(one)
        elapsed = time.monotonic() - t0
        assert in_flight_before_timeouts == 16
        assert len(results) == 20 and all(r.is_error and "timed out" in r.content[0].text for r in results)
        assert elapsed < 3.5
    finally:
        upstream.stop()


async def test_G06_disable_hides_tools_and_notifies_listeners(gateway_factory: Any, upstreams: Any) -> None:
    gw = gateway_factory()
    async with session(gw) as http:
        server = await register(http, "fx-modern", upstreams.modern.url + "/mcp")
        await register(http, "fx-legacy", upstreams.legacy.url + "/mcp")
        key = await new_key(http)
        await eventually(lambda: has_tools(gw, key, "fx-modern__echo", "fx-legacy__echo"))
        events: list[str] = []
        async with mcp(gw, key) as listener:
            async with listener.listen(tools_list_changed=True) as sub:
                r = await http.patch(f"/api/servers/{server['id']}", json={"enabled": False})
                assert r.status_code == 200 and r.json()["server"]["status"] == "disabled"
                with anyio.fail_after(5):
                    async for event in sub:
                        events.append(type(event).__name__)
                        break
    assert events == ["ToolsListChanged"]
    names = await tool_names(gw, key)
    assert "fx-legacy__echo" in names and not any(n.startswith("fx-modern__") for n in names)
    async with mcp(gw, key) as c:
        with pytest.raises(MCPError) as err:
            await c.call_tool("fx-modern__echo", {"text": "x"})
    assert err.value.error.code == -32602 and "server disabled" in err.value.error.message


async def test_G08_credential_injected_and_downstream_token_not_forwarded(
    gateway_factory: Any, upstreams: Any
) -> None:
    gw = gateway_factory()
    secured_url = upstreams.secured.url + "/mcp"
    async with session(gw) as http:
        probe = {"url": secured_url, "authType": "bearer"}
        test_ok = (await http.post("/api/servers/test", json={**probe, "credential": FIXTURE_TOKEN})).json()
        test_bad = (await http.post("/api/servers/test", json={**probe, "credential": "wrong"})).json()
        assert test_ok == {"ok": True, "era": "modern", "toolCount": 1, "errorCode": None, "error": None}
        assert test_bad["ok"] is False and test_bad["errorCode"] == "AUTH_REJECTED"
        bad = await register(http, "fx-badauth", secured_url, authType="bearer", credential="wrong")
        assert (bad["status"], bad["lastErrorCode"]) == ("unreachable", "AUTH_REJECTED")
        good = await register(http, "fx-secured", secured_url, authType="bearer", credential=FIXTURE_TOKEN)
        assert good["status"] == "connected" and good["hasCredential"] is True and "credential" not in good
        key = await new_key(http)
    await eventually(lambda: has_tools(gw, key, "fx-secured__ping"))
    before = len(upstreams.secured_rec.headers)
    async with mcp(gw, key) as c:
        assert (await c.call_tool("fx-secured__ping", {})).content[0].text == "pong"  # type: ignore[union-attr]
    seen = upstreams.secured_rec.headers[before:]
    assert seen and all(h.get("authorization") == f"Bearer {FIXTURE_TOKEN}" for h in seen)
    assert not any("mgw_" in v for h in upstreams.secured_rec.headers for v in h.values())


async def test_G09_revoked_key_401_and_denied_audit(gateway_factory: Any, database_url: str) -> None:
    gw = gateway_factory()
    async with session(gw) as http:
        key = await new_key(http, "to-revoke")
        key_id = (await http.get("/api/keys")).json()["keys"][0]["id"]
        async with mcp(gw, key) as c:
            await c.list_tools()
        assert (await http.delete(f"/api/keys/{key_id}")).status_code == 204
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream",
               "Mcp-Method": "tools/list"}  # fmt: skip
    async with httpx2.AsyncClient() as raw:
        r = await raw.post(gw.mcp_url, json=body, headers=headers)
        assert r.status_code == 401 and r.json()["error"] == "invalid_token"
        garbage = await raw.post(gw.mcp_url, json=body, headers={**headers, "Authorization": "Bearer nope"})
        assert garbage.status_code == 401
    conn = await db(database_url)
    try:
        rows = await conn.fetch("SELECT * FROM audit_log WHERE outcome = 'denied'")
        assert len(rows) == 1
        assert rows[0]["user_email"] == "admin@example.com" and rows[0]["credential_id"] == key_id
        assert rows[0]["method"] == "tools/list" and rows[0]["error_message"] == "API key revoked"
    finally:
        await conn.close()


async def test_G10_oversize_result(gateway_factory: Any, upstreams: Any) -> None:
    gw = gateway_factory()
    async with session(gw) as http:
        await register(http, "fx-modern", upstreams.modern.url + "/mcp")
        key = await new_key(http)
    await eventually(lambda: has_tools(gw, key, "fx-modern__big"))
    async with mcp(gw, key) as c:
        result = await c.call_tool("fx-modern__big", {})
    assert result.is_error and result.content[0].text.startswith("[gateway] result too large (")  # type: ignore[union-attr]


async def test_G11_G12_two_replicas_share_catalog_and_one_leader(
    gateway_factory: Any, upstreams: Any
) -> None:
    a = gateway_factory()
    b = gateway_factory()
    async with session(a) as http_a:
        server = await register(http_a, "fx-modern", upstreams.modern.url + "/mcp")
        key = await new_key(http_a)
    await eventually(lambda: has_tools(b, key, "fx-modern__echo"))  # G-11: B learned it via NOTIFY
    async with session(b) as http_b:
        await http_b.patch(f"/api/servers/{server['id']}", json={"enabled": False})

    async def gone_from_a() -> bool:
        return not any(n.startswith("fx-modern__") for n in await tool_names(a, key))

    await eventually(gone_from_a)
    leaders = [a.app.state.leader.is_leader, b.app.state.leader.is_leader]
    assert sorted(leaders) == [False, True], leaders  # G-12


async def test_invites_members_and_admin_only_tools(
    gateway_factory: Any, upstreams: Any, database_url: str
) -> None:
    gw = gateway_factory()
    async with session(gw) as admin:
        server = await register(admin, "fx-modern", upstreams.modern.url + "/mcp")
        invite = await admin.post("/api/invites", json={"email": "Ritika@Example.com", "role": "member"})
        assert invite.status_code == 201
        token = invite.json()["inviteUrl"].rsplit("/", 1)[1]
    conn = await db(database_url)
    try:  # SO-2 policy editing arrives in P15; set one directly to check enforcement
        await conn.execute(
            "INSERT INTO tool_policies (server_id, tool_name, admin_only) VALUES ($1, 'fail', true)",
            server["id"],
        )
        await conn.execute("""SELECT pg_notify('wmg_events', '{"type":"catalog_changed"}')""")
    finally:
        await conn.close()
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
        info = (await anon.get(f"/api/invites/{token}")).json()
        assert info == {"email": "ritika@example.com", "role": "member", "valid": True}
        weak = await anon.post(f"/api/invites/{token}/accept", json={"name": "Ritika", "password": "short"})
        assert weak.status_code == 422
        accepted = await anon.post(
            f"/api/invites/{token}/accept", json={"name": "Ritika", "password": "ritika-password-1"}
        )
        assert accepted.status_code == 200 and accepted.json()["user"]["role"] == "member"
        again = await anon.post(
            f"/api/invites/{token}/accept", json={"name": "X", "password": "another-pass-12"}
        )
        assert again.status_code == 409
    member_creds = {"email": "ritika@example.com", "password": "ritika-password-1"}
    async with session(gw, member_creds) as member:
        view = (await member.get("/api/servers")).json()["servers"][0]
        assert view["slug"] == "fx-modern" and view["toolCount"] == 3
        assert not {"url", "authType", "authHeaderName", "lastError", "hasCredential"} & set(view)
        tools = (await member.get(f"/api/servers/{server['id']}/tools")).json()["tools"]
        assert "fx-modern__fail" not in [t["exposedName"] for t in tools]
        assert (await member.post("/api/servers", json={})).status_code == 403
        assert (await member.get("/api/keys?all=1")).status_code == 403
        member_key = await new_key(member)
    await eventually(lambda: has_tools(gw, member_key, "fx-modern__echo"))
    async with mcp(gw, member_key) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        assert "fx-modern__fail" not in names
        with pytest.raises(MCPError) as err:
            await c.call_tool("fx-modern__fail", {})
    assert err.value.error.code == -32010


async def test_users_api_guards(gateway_factory: Any) -> None:
    gw = gateway_factory()
    async with session(gw) as admin:
        users = (await admin.get("/api/users")).json()["users"]
        me = users[0]
        assert me["role"] == "admin"
        r = await admin.patch(f"/api/users/{me['id']}", json={"role": "member"})
        assert r.status_code == 409  # self
        keys = [await new_key(admin, f"k{i}") for i in range(10)]
        assert len(keys) == 10
        r = await admin.post("/api/keys", json={"name": "eleventh"})
        assert r.status_code == 409  # max 10 active keys


async def test_delegated_jwt_and_disabled_user(
    gateway_factory: Any, base_env: dict[str, str], database_url: str
) -> None:
    from wmg_gateway.security import jwt as delegated_jwt

    gw = gateway_factory()
    async with session(gw) as admin:
        invite = (
            await admin.post("/api/invites", json={"email": "rohan@example.com", "role": "member"})
        ).json()
        token = invite["inviteUrl"].rsplit("/", 1)[1]
        async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
            accepted = await anon.post(
                f"/api/invites/{token}/accept", json={"name": "Rohan", "password": "rohan-password-1"}
            )
        rohan_id = accepted.json()["user"]["id"]
        chat_token = delegated_jwt.mint(rohan_id, base_env["jwt_secret"])
        async with mcp(gw, chat_token) as c:
            assert (await c.list_tools()).tools == []
        forged = delegated_jwt.mint(rohan_id, "x" * 40)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        headers = {"Accept": "application/json, text/event-stream", "Mcp-Method": "tools/list"}
        async with httpx2.AsyncClient() as raw:
            r = await raw.post(
                gw.mcp_url, json=body, headers={**headers, "Authorization": f"Bearer {forged}"}
            )
            assert r.status_code == 401
            assert (
                await admin.patch(f"/api/users/{rohan_id}", json={"status": "disabled"})
            ).status_code == 200
            r = await raw.post(
                gw.mcp_url, json=body, headers={**headers, "Authorization": f"Bearer {chat_token}"}
            )
            assert r.status_code == 401
    conn = await db(database_url)
    try:
        row = await conn.fetchrow("SELECT * FROM audit_log WHERE outcome = 'denied'")
        assert row["user_email"] == "rohan@example.com" and row["credential_type"] == "chat_agent"
        assert row["credential_id"] == f"chat:{rohan_id}" and row["error_message"] == "User disabled"
    finally:
        await conn.close()


async def test_SO2_01_policy_toggle(gateway_factory: Any, upstreams: Any) -> None:
    """SO2-01: an admin-only tool is hidden from and rejected for members, open to admins; a toggle through
    the API takes effect within 1 s on every replica."""
    gw, other = gateway_factory(), gateway_factory()
    async with session(gw) as admin:
        server = await register(admin, "fx-modern", upstreams.modern.url + "/mcp")
        admin_key = await new_key(admin)
        invite = await admin.post("/api/invites", json={"email": "ritika@example.com", "role": "member"})
        token = invite.json()["inviteUrl"].rsplit("/", 1)[1]
        async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
            r = await anon.post(
                f"/api/invites/{token}/accept", json={"name": "Ritika", "password": "ritika-password-1"}
            )
            assert r.status_code == 200
        async with session(gw, {"email": "ritika@example.com", "password": "ritika-password-1"}) as member:
            member_key = await new_key(member)
            await eventually(lambda: has_tools(other, member_key, "fx-modern__echo"))
            policy = f"/api/servers/{server['id']}/tools/echo/policy"

            r = await admin.put(policy, json={"adminOnly": True})
            assert r.status_code == 200, r.text
            assert r.json()["policy"] == {**r.json()["policy"], "toolName": "echo", "adminOnly": True}
            # the other replica learns it through LISTEN/NOTIFY
            await eventually(lambda: _lacks(other, member_key, "fx-modern__echo"), within=1.0)
            tools = (await member.get(f"/api/servers/{server['id']}/tools")).json()["tools"]
            assert "fx-modern__echo" not in [t["exposedName"] for t in tools]
            admin_tools = (await admin.get(f"/api/servers/{server['id']}/tools")).json()["tools"]
            assert {"exposedName": "fx-modern__echo", "adminOnly": True}.items() <= next(
                t for t in admin_tools if t["name"] == "echo"
            ).items()
            async with mcp(other, member_key) as c:
                with pytest.raises(MCPError) as err:
                    await c.call_tool("fx-modern__echo", {"text": "x"})
            assert err.value.error.code == -32010
            async with mcp(other, admin_key) as c:
                assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error

            r = await admin.put(policy, json={"adminOnly": False})
            assert r.status_code == 200 and r.json()["policy"]["adminOnly"] is False
            await eventually(lambda: has_tools(other, member_key, "fx-modern__echo"), within=1.0)
            async with mcp(other, member_key) as c:
                assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error

            assert (await member.put(policy, json={"adminOnly": True})).status_code == 403
        missing = await admin.put(f"/api/servers/{server['id']}/tools/nope/policy", json={"adminOnly": True})
        assert missing.status_code == 404 and missing.json()["error"]["message"] == "Tool not found"
        bad = await admin.put(policy, json={"adminOnly": "yes please"})
        assert bad.status_code == 422


async def _lacks(gw: Any, key: str, name: str) -> bool:
    return name not in await tool_names(gw, key)


def _metric(text: str, name: str, **labels: str) -> float:
    """One sample from Prometheus text output (0 if absent)."""
    want = ",".join(f'{k}="{v}"' for k, v in labels.items())
    for line in text.splitlines():
        sample, _, value = line.rpartition(" ")
        if sample == (f"{name}{{{want}}}" if want else name):
            return float(value)
    return 0.0


async def test_SO8_01_metrics_and_insights_match_the_audit(
    gateway_factory: Any, upstreams: Any, database_url: str
) -> None:
    gw = gateway_factory()
    async with session(gw) as admin:
        await register(admin, "fx-modern", upstreams.modern.url + "/mcp")
        key = await new_key(admin)
        await eventually(lambda: has_tools(gw, key, "fx-modern__echo"))
        before = (await admin.get("/metrics")).text
        async with mcp(gw, key) as c:
            for i in range(3):
                assert not (await c.call_tool("fx-modern__echo", {"text": str(i)})).is_error
            assert (await c.call_tool("fx-modern__fail", {})).is_error
        after = (await admin.get("/metrics")).text
        for outcome, n in (("success", 3), ("tool_error", 1)):
            delta = _metric(after, "wmg_tool_calls_total", outcome=outcome, server="fx-modern") - _metric(
                before, "wmg_tool_calls_total", outcome=outcome, server="fx-modern"
            )
            assert delta == n, (outcome, delta)
        assert _metric(after, "wmg_upstream_up", server="fx-modern") == 1
        assert _metric(after, "wmg_upstream_tools", server="fx-modern") == 4
        assert _metric(after, "wmg_tools_list_duration_seconds_count") >= 1
        assert 'wmg_tool_call_duration_seconds_bucket{le="0.01",server="fx-modern"}' in after

        r = await admin.get("/api/insights", params={"window": "1h"})
        assert r.status_code == 200, r.text
        body = r.json()
        conn = await db(database_url)
        try:
            expected = await conn.fetchrow(
                "SELECT count(*) AS calls, count(*) FILTER (WHERE outcome <> 'success') AS errors "
                "FROM audit_log WHERE method = 'tools/call' AND server_slug = 'fx-modern'"
            )
        finally:
            await conn.close()
        (server,) = body["servers"]
        assert (server["serverSlug"], server["calls"], server["errors"]) == (
            "fx-modern",
            expected["calls"],
            expected["errors"],
        )
        assert server["calls"] == 4 and server["errorRate"] == 0.25
        assert server["outcomes"] == {"success": 3, "tool_error": 1}
        assert server["p95Ms"] >= server["p50Ms"] >= 0
        assert body["calls"] == 4 and body["window"] == "1h"
        assert [t["exposedName"] for t in body["tools"]] == ["fx-modern__echo", "fx-modern__fail"]
        (failure,) = body["failures"]
        assert failure["serverSlug"] == "fx-modern" and failure["outcome"] == "tool_error"
        assert failure["count"] == 1 and failure["lastSeen"].endswith("Z")
        assert (await admin.get("/api/insights", params={"window": "2h"})).status_code == 422


ORDERS = "fx-many__list_orders"


async def test_SO4_01_search_mode(gateway_factory: Any, many_tools: Any, database_url: str) -> None:
    gw = gateway_factory()
    async with session(gw) as admin:
        server = await register(admin, "fx-many", many_tools.url + "/mcp")
        await eventually(lambda: _tool_count(admin, server["id"], 60))
        key = await new_key(admin)
    async with mcp_at(gw, key, "/mcp/search") as c:
        listed = sorted(t.name for t in (await c.list_tools()).tools)
        assert listed == ["call_tool", "list_servers", "search_tools"]
        found = await c.call_tool("search_tools", {"query": "orders today", "limit": 3})
        tools = found.structured_content["tools"]  # type: ignore[index]
        assert tools[0]["name"] == ORDERS and "period" in tools[0]["inputSchema"]["properties"]
        result = await c.call_tool("call_tool", {"name": ORDERS, "arguments": {"period": "today"}})
        assert not result.is_error and "2 orders today" in str(result.content[0])
        servers = await c.call_tool("list_servers", {})
        expected = {"servers": [{"slug": "fx-many", "name": "FX-MANY", "toolCount": 60}]}
        assert servers.structured_content == expected
        with pytest.raises(MCPError) as err:
            await c.call_tool("call_tool", {"name": "fx-many__nope"})
        assert err.value.error.code == -32602
        with pytest.raises(MCPError):
            await c.call_tool("search_tools", {"query": ""})
    conn = await db(database_url)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM audit_log WHERE exposed_name = 'fx-many__list_orders' AND outcome = 'success'"
        )
    finally:
        await conn.close()
    # audited as the real tool, flagged as search mode
    assert row is not None and row["tool_name"] == "list_orders" and row["client_mode"] == "search"


async def _tool_count(http: httpx2.AsyncClient, server_id: str, n: int) -> bool:
    return len((await http.get(f"/api/servers/{server_id}/tools")).json()["tools"]) == n


@asynccontextmanager
async def mcp_at(gw: Any, key: str, path: str) -> AsyncIterator[Client]:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
        Client(streamable_http_client(gw.url + path, http_client=http), cache=None) as client,  # type: ignore[arg-type]
    ):
        yield client


async def _fresh_minute(need_s: float = 20.0) -> None:
    """Rate windows are calendar minutes: start a burst early enough in one that it can't straddle two."""
    into = time.time() % 60
    if into > 60 - need_s:
        await anyio.sleep(60 - into + 0.2)


async def _rate_limited(gw: Any, key: str, path: str = "/mcp", tool: str = "fx-modern__echo") -> str:
    async with mcp_at(gw, key, path) as c:
        with pytest.raises(MCPError) as err:
            if path == "/mcp":
                await c.call_tool(tool, {"text": "x"})
            else:
                await c.call_tool("call_tool", {"name": tool, "arguments": {"text": "x"}})
    assert err.value.error.code == -32011, err.value.error
    return err.value.error.message


async def test_SO7_02_rate_limit_shared_across_replicas(
    gateway_factory: Any, upstreams: Any, database_url: str
) -> None:
    """SO7-02 (D95): 120 tool calls a minute per credential, counted across replicas; the 121st → -32011 and
    a `rate_limited` audit row; other keys are unaffected. A per-key override applies on the next request,
    counts search mode's call_tool too (not search_tools), and null restores the default."""
    gw, other = gateway_factory(rate_limit_per_min=120), gateway_factory(rate_limit_per_min=120)
    async with session(gw) as admin:
        await register(admin, "fx-modern", upstreams.modern.url + "/mcp")
        burst, spare, small = (
            await new_key(admin, "burst"),
            await new_key(admin, "spare"),
            await new_key(admin, "s"),
        )
        await eventually(lambda: has_tools(other, burst, "fx-modern__echo"))
        await _fresh_minute()
        async with mcp(gw, burst) as a, mcp(other, burst) as b:
            for i in range(120):
                result = await (a if i % 2 else b).call_tool("fx-modern__echo", {"text": str(i)})
                assert not result.is_error, (i, result.content)
        message = await _rate_limited(other, burst)
        assert re.fullmatch(r"Rate limit exceeded; retry in \d+s", message), message
        async with mcp(gw, spare) as c:  # per credential, not per user
            assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error

        keys = (await admin.get("/api/keys")).json()
        assert keys["defaultRateLimitPerMin"] == 120
        small_row = next(k for k in keys["keys"] if k["name"] == "s")
        assert small_row["rateLimitPerMin"] is None
        r = await admin.patch(f"/api/keys/{small_row['id']}", json={"rateLimitPerMin": 2})
        assert r.status_code == 200 and r.json()["key"]["rateLimitPerMin"] == 2, r.text
        await _fresh_minute(10)
        async with mcp_at(gw, small, "/mcp/search") as c:
            for _ in range(3):  # searching is free
                await c.call_tool("search_tools", {"query": "echo"})
            result = await c.call_tool("call_tool", {"name": "fx-modern__echo", "arguments": {"text": "x"}})
            assert not result.is_error
        async with mcp(other, small) as c:
            assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error
        await _rate_limited(gw, small, "/mcp/search")
        r = await admin.patch(f"/api/keys/{small_row['id']}", json={"rateLimitPerMin": None})
        assert r.status_code == 200 and r.json()["key"]["rateLimitPerMin"] is None
        async with mcp(gw, small) as c:
            assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error

        spare_id = next(k["id"] for k in keys["keys"] if k["name"] == "spare")
        for body, status in (({"rateLimitPerMin": 0}, 422), ({"rateLimitPerMin": 100_001}, 422), ({}, 422)):
            assert (await admin.patch(f"/api/keys/{spare_id}", json=body)).status_code == status, body
        missing = await admin.patch(f"/api/keys/{uuid.uuid4()}", json={"rateLimitPerMin": 5})
        assert missing.status_code == 404
        assert (await admin.delete(f"/api/keys/{spare_id}")).status_code == 204
        revoked = await admin.patch(f"/api/keys/{spare_id}", json={"rateLimitPerMin": 5})
        assert revoked.status_code == 409 and revoked.json()["error"]["message"] == "Key is revoked"
        invite = await admin.post("/api/invites", json={"email": "ritika@example.com", "role": "member"})
        token = invite.json()["inviteUrl"].rsplit("/", 1)[1]
        async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF) as anon:
            body = {"name": "Ritika", "password": "ritika-password-1"}
            assert (await anon.post(f"/api/invites/{token}/accept", json=body)).status_code == 200
        async with session(gw, {"email": "ritika@example.com", "password": "ritika-password-1"}) as member:
            await new_key(member, "mine")
            mine = next(k for k in (await member.get("/api/keys")).json()["keys"] if k["name"] == "mine")
            assert (
                await member.patch(f"/api/keys/{mine['id']}", json={"rateLimitPerMin": 500})
            ).status_code == 403

    conn = await db(database_url)
    try:
        rows = await conn.fetch("SELECT outcome, client_mode FROM audit_log WHERE outcome = 'rate_limited'")
    finally:
        await conn.close()
    assert sorted((r["outcome"], r["client_mode"]) for r in rows) == [
        ("rate_limited", "full"),
        ("rate_limited", "search"),
    ]


async def test_rate_limit_off_by_default_in_settings(gateway_factory: Any, upstreams: Any) -> None:
    """RATE_LIMIT_PER_MIN=0 turns the limiter off, per-key overrides included (D95)."""
    gw = gateway_factory()  # Settings default 0; the chart sets 120
    async with session(gw) as admin:
        await register(admin, "fx-modern", upstreams.modern.url + "/mcp")
        key = await new_key(admin, "k")
        key_id = next(k["id"] for k in (await admin.get("/api/keys")).json()["keys"] if k["name"] == "k")
        assert (await admin.patch(f"/api/keys/{key_id}", json={"rateLimitPerMin": 1})).status_code == 200
        await eventually(lambda: has_tools(gw, key, "fx-modern__echo"))
    async with mcp(gw, key) as c:
        for _ in range(3):
            assert not (await c.call_tool("fx-modern__echo", {"text": "x"})).is_error

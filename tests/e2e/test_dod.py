"""TESTING §5: the Definition-of-Done acceptance tests against the live cluster (`make e2e`, after `make up`).

Tests run in file order and share one `World` (users, keys, order). Each run creates its own users
(`rohan.e2e<ts>@example.com` admin, `ritika.e2e<ts>@example.com` member), so the suite also runs on a cluster
that already has data. Chat steps need the `platform-llm` Secret; without it they are skipped loudly.
Passwords and keys are generated here and never printed.
"""

import base64
import json
import os
import secrets
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.e2e

# Local by default; against the VPS: E2E_SCHEME=https E2E_DOMAIN=137-23-40-25.sslip.io (and KUBECONFIG=...)
SCHEME = os.environ.get("E2E_SCHEME", "http")
DOMAIN = os.environ.get("E2E_DOMAIN", "localhost")
DASHBOARD = f"{SCHEME}://dashboard.{DOMAIN}"
MCP_URL = f"{SCHEME}://gateway.{DOMAIN}/mcp"
SHOP = f"{SCHEME}://shop.{DOMAIN}"
STORE_API = f"{SHOP}/wp-json/wc/store/v1"
CSRF = {"X-WMG-CSRF": "1"}
POSTCODE = "560001"  # Bengaluru (KA); the store ships from Maharashtra → other_state, 4 days
SHIPPING = {
    "first_name": "Ritika",
    "last_name": "E2E",
    "address_1": "1 MG Road",
    "city": "Bengaluru",
    "state": "KA",
    "postcode": POSTCODE,
    "country": "IN",
    "phone": "9999999999",
}


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["kubectl", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=120,
        check=check,
    )


@dataclass
class World:
    ts: str = field(default_factory=lambda: time.strftime("%H%M%S"))
    admin: httpx2.Client | None = None
    rohan: httpx2.Client | None = None
    ritika: httpx2.Client | None = None
    ids: dict[str, str] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    order: dict[str, Any] = field(default_factory=dict)
    product_id: int | None = None
    llm: bool = False


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    w.llm = kubectl("get", "secret", "platform-llm", "-n", "mcp-platform", check=False).returncode == 0
    yield w
    for client in (w.admin, w.rohan, w.ritika):
        if client is not None:
            client.close()


def need(value: Any, what: str) -> Any:
    if value is None or value == {}:
        pytest.skip(f"needs {what} from an earlier step")
    return value


def need_llm(w: World) -> None:
    if not w.llm:
        pytest.skip("!!! LLM Secret platform-llm missing: chat tests NOT RUN (make secrets-llm) !!!")


def session() -> httpx2.Client:
    return httpx2.Client(base_url=DASHBOARD, headers=CSRF, timeout=60)


def eventually(check: Callable[[], bool], within: float, step: float = 1.0) -> float:
    """Seconds until `check()` was true; fails after `within`."""
    started = time.monotonic()
    while not check():
        assert time.monotonic() - started < within, f"not reached within {within:.0f} s"
        time.sleep(step)
    return time.monotonic() - started


# ------------------------------------------------------------------------------------------------ MCP helpers


async def _with_mcp(key: str, mode: str, work: Callable[[Client], Any]) -> Any:
    try:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
            Client(streamable_http_client(MCP_URL, http_client=http), mode=mode, cache=None) as client,
        ):
            return await work(client)
    except BaseExceptionGroup as group:  # nested task groups wrap errors; surface a lone MCPError as-is
        errors = _leaves(group)
        if len(errors) == 1 and isinstance(errors[0], MCPError):
            raise errors[0] from None
        raise


def _leaves(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for sub in error.exceptions for leaf in _leaves(sub)]
    return [error]


def mcp_tools(key: str, mode: str = "auto") -> list[str]:
    async def work(c: Client) -> list[str]:
        return [t.name for t in (await c.list_tools()).tools]

    result: list[str] = anyio.run(_with_mcp, key, mode, work)
    return result


def mcp_call(key: str, name: str, args: dict[str, Any], mode: str = "auto") -> dict[str, Any]:
    async def work(c: Client) -> dict[str, Any]:
        r = await c.call_tool(name, args)
        assert not r.is_error, r.content
        assert isinstance(r.structured_content, dict)
        return r.structured_content

    result: dict[str, Any] = anyio.run(_with_mcp, key, mode, work)
    return result


def delegated_tools(user_id: str) -> list[str]:
    """The chat agent's tool list for this user (delegated JWT over loopback, run inside the gateway pod)."""
    code = (
        "import anyio, os, httpx2\n"
        "from mcp import Client\n"
        "from mcp.client.streamable_http import streamable_http_client\n"
        "from wmg_gateway.security import jwt\n"
        "async def main():\n"
        f"    tok = jwt.mint('{user_id}', os.environ['JWT_SECRET'])\n"
        "    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + tok}) as h, "
        "Client(streamable_http_client('http://127.0.0.1:8080/mcp', http_client=h),"
        " mode='2026-07-28', cache=None) as c:\n"
        "        print(' '.join(t.name for t in (await c.list_tools()).tools))\n"
        "anyio.run(main)\n"
    )
    out = kubectl("exec", "-n", "mcp-platform", "deploy/platform-gateway", "--", "python", "-c", code).stdout
    return out.split()


# --------------------------------------------------------------------------------------- store / chat helpers


def store_order(product_id: int) -> dict[str, Any]:
    """Checkout through the WooCommerce Store API (cart → COD checkout), shipping to POSTCODE."""
    with httpx2.Client(timeout=60) as http:
        head = http.get(f"{STORE_API}/cart")
        headers = {"Cart-Token": head.headers["cart-token"], "Nonce": head.headers["nonce"]}
        item = {"id": product_id, "quantity": 1}
        added = http.post(f"{STORE_API}/cart/add-item", headers=headers, json=item)
        assert added.status_code in (200, 201), added.text
        body = {
            "billing_address": {**SHIPPING, "email": "ritika.e2e@example.com"},
            "shipping_address": SHIPPING,
            "payment_method": "cod",
        }
        r = http.post(f"{STORE_API}/checkout", headers=headers, json=body)
        assert r.status_code == 200, r.text
        order: dict[str, Any] = r.json()
        return order


def any_product_id() -> int:
    with httpx2.Client(timeout=30) as http:
        products = http.get(f"{STORE_API}/products", params={"per_page": 1}).json()
    return int(products[0]["id"])


def chat(client: httpx2.Client, text: str) -> list[tuple[str, dict[str, Any]]]:
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]}, timeout=180)
    assert r.status_code == 200, r.text
    events = []
    for block in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def tool_calls(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    return [data["name"] for kind, data in events if kind == "tool_call"]


def server_id(client: httpx2.Client, slug: str) -> str:
    servers = client.get("/api/servers").json()["servers"]
    return str(next(s["id"] for s in servers if s["slug"] == slug))


def server_status(client: httpx2.Client, slug: str) -> str:
    servers = client.get("/api/servers").json()["servers"]
    return str(next(s["status"] for s in servers if s["slug"] == slug))


# ---------------------------------------------------------------------------------------- 1-2 people and keys


def test_01_admin_invites_rohan_and_ritika(world: World) -> None:
    raw = kubectl(
        "get", "secret", "platform-credentials", "-n", "mcp-platform", "-o", "jsonpath={.data.admin-password}"
    ).stdout
    world.admin = session()
    r = world.admin.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": base64.b64decode(raw).decode()}
    )
    assert r.status_code == 200, r.text
    for who, role in (("rohan", "admin"), ("ritika", "member")):
        email = f"{who}.e2e{world.ts}@example.com"
        r = world.admin.post("/api/invites", json={"email": email, "role": role})
        assert r.status_code == 201, r.text
        token = r.json()["inviteUrl"].rsplit("/", 1)[1]
        client = session()
        body = {"name": f"{who.capitalize()} E2E", "password": secrets.token_urlsafe(18)}
        accepted = client.post(f"/api/invites/{token}/accept", json=body)
        assert accepted.status_code == 200, accepted.text
        user = accepted.json()["user"]
        assert user["role"] == role
        world.ids[who] = user["id"]
        setattr(world, who, client)


def test_02_ritika_and_rohan_create_keys(world: World) -> None:
    for who in ("ritika", "rohan"):
        client: httpx2.Client = need(getattr(world, who), who)
        r = client.post("/api/keys", json={"name": f"{who} e2e laptop"})
        assert r.status_code == 201, r.text
        world.keys[who] = r.json()["key"]
        world.ids[f"{who}_key"] = r.json()["id"]
        assert world.keys[who].startswith("mgw_")


# ------------------------------------------------------------------------------------------------- 3 AT-S1/S2


def test_03_AT_S1_S2_store_order_visible_through_gateway(world: World) -> None:
    rohan_key = need(world.keys.get("rohan"), "Rohan's key")
    world.order = store_order(any_product_id())
    order_id = world.order["order_id"]
    detail = mcp_call(rohan_key, "woo-main__get_order", {"id": order_id})
    assert detail["id"] == order_id
    assert detail["payment_method_title"]
    assert detail["status"] in ("processing", "on-hold", "pending")


# --------------------------------------------------------------------------------------------------- 4-5 chat


def test_04_AT_C1_chat_creates_product(world: World) -> None:
    need_llm(world)
    rohan = need(world.rohan, "Rohan")
    name = f"E2E Lamp {world.ts}"
    events = chat(rohan, f"Create a product named '{name}' priced 1299")
    assert "woo-main__create_product" in tool_calls(events), events
    result = next(d for k, d in events if k == "tool_result" and d["name"] == "woo-main__create_product")
    assert result["ok"], result
    search = {"s": name, "post_type": "product"}
    page = httpx2.get(f"{SHOP}/", params=search, follow_redirects=True)
    assert name in page.text
    with httpx2.Client(timeout=30) as http:
        found = http.get(f"{STORE_API}/products", params={"search": name}).json()
    world.product_id = int(found[0]["id"])


def test_05_AT_C2_chat_lists_todays_orders(world: World) -> None:
    need_llm(world)
    rohan = need(world.rohan, "Rohan")
    product_id = need(world.product_id, "the chat-created product")
    order = store_order(product_id)
    events = chat(rohan, "What orders came in today?")
    assert "woo-main__list_orders" in tool_calls(events), events
    answer = next(d["text"] for k, d in events if k == "answer")
    assert str(order["order_id"]) in answer, answer


# ---------------------------------------------------------------------------------------- 6-7 external client


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_06_AT_E1_E2_member_client_lists_and_calls(world: World, mode: str) -> None:
    key = need(world.keys.get("ritika"), "Ritika's key")
    order_id = need(world.order.get("order_id"), "the order from step 3")
    names = mcp_tools(key, mode)
    for prefix in ("woo-main__", "gst__", "pincode__"):
        assert any(n.startswith(prefix) for n in names), (prefix, names)
    assert "woo-main__create_product" not in names  # admin-only write tool hidden from members
    orders = mcp_call(key, "woo-main__list_orders", {"period": "today"}, mode)
    assert order_id in [o["id"] for o in orders["orders"]]


def test_07_AT_E3_two_servers_answer_a_delivery_question(world: World) -> None:
    key = need(world.keys.get("ritika"), "Ritika's key")
    order_id = need(world.order.get("order_id"), "the order from step 3")
    order = mcp_call(key, "woo-main__get_order", {"id": order_id})
    postcode = order["shipping"]["postcode"]
    assert postcode == POSTCODE
    delivery = mcp_call(key, "pincode__check_delivery", {"pincode": postcode})
    assert delivery["zone"] == "other_state" and delivery["eta_days"] == 4


# ----------------------------------------------------------------------------------------------- 8-10 control


def test_08_AT_T2_disable_hides_tools(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    key = need(world.keys.get("ritika"), "Ritika's key")
    gst = server_id(rohan, "gst")
    assert rohan.patch(f"/api/servers/{gst}", json={"enabled": False}).status_code == 200
    try:
        assert not any(n.startswith("gst__") for n in mcp_tools(key))
        with pytest.raises(MCPError) as exc:
            mcp_call(key, "gst__calculate_gst", {"amount": 100, "rate": 18})
        assert exc.value.error.code == -32602
        assert not any(n.startswith("gst__") for n in delegated_tools(world.ids["rohan"]))
    finally:
        assert rohan.patch(f"/api/servers/{gst}", json={"enabled": True}).status_code == 200
    eventually(lambda: server_status(rohan, "gst") == "connected", within=30)


def test_09_AT_T3_dead_upstream_isolated_and_recovers(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    key = need(world.keys.get("ritika"), "Ritika's key")
    kubectl("scale", "deploy/pincode", "-n", "mcp-upstreams", "--replicas=0")
    try:
        down = eventually(lambda: server_status(rohan, "pincode") == "unreachable", within=20)
        print(f"pincode unreachable after {down:.1f} s")
        assert not any(n.startswith("pincode__") for n in mcp_tools(key))
        assert "orders" in mcp_call(key, "woo-main__list_orders", {"period": "today"})
    finally:
        kubectl("scale", "deploy/pincode", "-n", "mcp-upstreams", "--replicas=1")
    up = eventually(lambda: server_status(rohan, "pincode") == "connected", within=30)
    print(f"pincode connected again after {up:.1f} s")
    # self-healing: a deleted pod is replaced by its Deployment
    kubectl("delete", "pod", "-n", "mcp-upstreams", "-l", "app.kubernetes.io/name=pincode", "--wait=false")
    kubectl("rollout", "status", "deploy/pincode", "-n", "mcp-upstreams", "--timeout=90s")
    eventually(lambda: server_status(rohan, "pincode") == "connected", within=30)


def test_10_AT_T1_revoked_key_rejected(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    key = need(world.keys.get("ritika"), "Ritika's key")
    assert rohan.delete(f"/api/keys/{world.ids['ritika_key']}").status_code == 204
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json, text/event-stream",
        "Mcp-Method": "tools/list",
    }
    assert httpx2.post(MCP_URL, json=body, headers=headers, timeout=30).status_code == 401


# --------------------------------------------------------------------------------------------------- 11 audit


def test_11_AT_T4_audit_attributes_every_call(world: World) -> None:
    rohan = need(world.rohan, "Rohan")

    def rows(who: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = rohan.get(
            "/api/audit", params={"userId": world.ids[who], "limit": 200}
        ).json()["items"]
        return items

    ritika_rows = rows("ritika")
    outcomes = {r["outcome"] for r in ritika_rows}
    assert {"success", "unknown_tool", "denied"} <= outcomes, outcomes
    assert {r["credentialType"] for r in ritika_rows} == {"api_key"}
    assert any(r["exposedName"] == "pincode__check_delivery" for r in ritika_rows)
    if world.llm:
        chat_rows = [r for r in rows("rohan") if r["credentialType"] == "chat_agent"]
        assert {"woo-main__create_product", "woo-main__list_orders"} <= {r["exposedName"] for r in chat_rows}


def test_12_cleanup_disables_e2e_users(world: World) -> None:
    admin = need(world.admin, "the admin session")
    for who in ("rohan", "ritika"):
        if who in world.ids:
            r = admin.patch(f"/api/users/{world.ids[who]}", json={"status": "disabled"})
            assert r.status_code == 200, r.text

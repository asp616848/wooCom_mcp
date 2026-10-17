"""TESTING §6 B-01..B-08 + SO-6 (P14): bonus store provisioning on the running cluster (`make e2e-stores`).

Ordered steps sharing one `World`: an e2e admin (Rohan) creates `paris` and `tokyo` concurrently; while they
provision, one deployer Job pod is killed mid-Helm (B-04) and the gateway pods are deleted (B-03). Then:
both Ready within 10 min, URLs + tools (B-01), limits and names (B-02), quota (B-07), cross-store isolation
(B-05/K-08), the deployer's admission scope (SO-6), the BR-AT chat comparison (B-08, skipped loudly without
an LLM key), and deleting `tokyo` (B-06). `paris` is deleted at the end unless E2E_KEEP_STORES=1. ~15 min.
"""

import base64
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = [pytest.mark.e2e, pytest.mark.bonus]

SCHEME = os.environ.get("E2E_SCHEME", "http")
DOMAIN = os.environ.get("E2E_DOMAIN", "localhost")
DASHBOARD = f"{SCHEME}://dashboard.{DOMAIN}"
MCP_URL = f"{SCHEME}://gateway.{DOMAIN}/mcp"
CSRF = {"X-WMG-CSRF": "1"}
STORES = ("paris", "tokyo")
READY_WITHIN_S = 600  # BR exit gate: both Ready < 10 min
DEPLOYER = "system:serviceaccount:mcp-platform:wmg-deployer"
CURL_IMAGE = "curlimages/curl:8.22.0"
SETTLE_S = 8.0  # kube-router programs a new pod's IP a few seconds after start (see test_k8s.py)
SHIPPING = {
    "first_name": "Ritika",
    "last_name": "E2E",
    "address_1": "1 MG Road",
    "city": "Bengaluru",
    "state": "KA",
    "postcode": "560001",
    "country": "IN",
    "phone": "9999999999",
}


def kubectl(*args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["kubectl", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


def eventually(check: Callable[[], bool], within: float, step: float = 2.0) -> float:
    started = time.monotonic()
    while not check():
        assert time.monotonic() - started < within, f"not reached within {within:.0f} s"
        time.sleep(step)
    return time.monotonic() - started


@dataclass
class World:
    ts: str = field(default_factory=lambda: time.strftime("%H%M%S"))
    admin: httpx2.Client | None = None
    rohan: httpx2.Client | None = None
    rohan_id: str = ""
    key: str = ""
    started: float = 0.0
    started_at: str = ""  # RFC 3339 UTC, compared with pod creationTimestamps
    ready: bool = False
    llm: bool = False
    probe_pods: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    w.llm = kubectl("get", "secret", "platform-llm", "-n", "mcp-platform", check=False).returncode == 0
    yield w
    for namespace, pod in w.probe_pods:
        kubectl("delete", "pod", pod, "-n", namespace, "--wait=false", check=False)
    for client in (w.admin, w.rohan):
        if client is not None:
            client.close()


def need(value: Any, what: str) -> Any:
    if not value:
        pytest.skip(f"needs {what} from an earlier step")
    return value


def session() -> httpx2.Client:
    return httpx2.Client(base_url=DASHBOARD, headers=CSRF, timeout=60)


def stores(client: httpx2.Client) -> dict[str, dict[str, Any]]:
    r = client.get("/api/stores")
    assert r.status_code == 200, r.text
    return {s["name"]: s for s in r.json()["stores"]}


def shop_url(name: str) -> str:
    return f"{SCHEME}://{name}.shop.{DOMAIN}"


def mcp_tools(key: str) -> list[str]:
    async def run() -> list[str]:
        async with (
            httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
            Client(streamable_http_client(MCP_URL, http_client=http), cache=None) as client,
        ):
            return [t.name for t in (await client.list_tools()).tools]

    result: list[str] = anyio.run(run)
    return result


def deployer_pods(store: str, since: str) -> list[str]:
    """This run's provisioning pods for `store` (earlier runs' pods stay for the Job TTL of 1 h)."""
    out = kubectl(
        "get", "pods", "-n", "mcp-platform", "-o", "json",
        "-l", f"app.kubernetes.io/name=deployer,wmg.dev/action=provision,wmg.dev/store={store}",
    ).stdout  # fmt: skip
    pods = [p for p in json.loads(out)["items"] if p["metadata"]["creationTimestamp"] >= since]
    return [p["metadata"]["name"] for p in sorted(pods, key=lambda p: p["metadata"]["creationTimestamp"])]


def store_order(name: str) -> int:
    """One COD checkout through the store's WooCommerce Store API; returns the order id."""
    api = f"{shop_url(name)}/wp-json/wc/store/v1"
    with httpx2.Client(timeout=60) as http:
        product = http.get(f"{api}/products", params={"per_page": 1}).json()[0]["id"]
        head = http.get(f"{api}/cart")
        headers = {"Cart-Token": head.headers["cart-token"], "Nonce": head.headers["nonce"]}
        added = http.post(f"{api}/cart/add-item", headers=headers, json={"id": product, "quantity": 1})
        assert added.status_code in (200, 201), added.text
        body = {
            "billing_address": {**SHIPPING, "email": "ritika.e2e@example.com"},
            "shipping_address": SHIPPING,
            "payment_method": "cod",
        }
        r = http.post(f"{api}/checkout", headers=headers, json=body)
        assert r.status_code == 200, r.text
        return int(r.json()["order_id"])


def chat(client: httpx2.Client, text: str) -> list[tuple[str, dict[str, Any]]]:
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]}, timeout=240)
    assert r.status_code == 200, r.text
    events = []
    for block in r.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


_UNITS = {"m": 1e-3, "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30}


def quantity(value: str) -> float:
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]*)", value)
    assert match, value
    return float(match.group(1)) * _UNITS[match.group(2)]


def probe(w: World, namespace: str, url: str, labels: dict[str, str]) -> str:
    """HTTP code (`connected` for telnet://) from a settled pod with `labels`; else `blocked`."""
    label_arg = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    pod = next((p for ns, p in w.probe_pods if ns == namespace and p.endswith(labels["probe"])), None)
    if pod is None:
        pod = f"nettest-{uuid.uuid4().hex[:6]}-{labels['probe']}"
        overrides = {
            "apiVersion": "v1",
            "spec": {
                "terminationGracePeriodSeconds": 1,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 100},
                "containers": [
                    {
                        "name": "probe",
                        "image": CURL_IMAGE,
                        "command": ["sleep", "600"],
                        "resources": {
                            "requests": {"cpu": "10m", "memory": "16Mi"},
                            "limits": {"memory": "32Mi"},
                        },
                    }
                ],
            },
        }
        kubectl(
            "run", pod, "--restart=Never", f"--image={CURL_IMAGE}", "-n", namespace, f"--labels={label_arg}",
            f"--overrides={json.dumps(overrides)}",
        )  # fmt: skip
        kubectl("wait", "-n", namespace, "--for=condition=Ready", f"pod/{pod}", "--timeout=90s")
        time.sleep(SETTLE_S)
        w.probe_pods.append((namespace, pod))
    tcp = url.startswith("telnet://")
    fmt = "RESULT=%{time_connect};" if tcp else "RESULT=%{http_code};"
    curl = ["curl", "-s", "-o", "/dev/null", "--connect-timeout", "3", "-m", "4", "-w", fmt, url]
    out = kubectl("exec", "-n", namespace, pod, "--", *curl, check=False).stdout
    match = re.search(r"RESULT=([^;]*);", out)
    value = match.group(1) if match else ""
    if tcp:
        return "connected" if value and float(value) > 0 else "blocked"
    return value if value and value != "000" else "blocked"


def wait_gone(client: httpx2.Client, name: str, within: float = 420) -> None:
    eventually(lambda: name not in stores(client), within, step=5)


# ------------------------------------------------------------------------------------------------- setup


def test_00_rohan_and_a_clean_slate(world: World) -> None:
    raw = kubectl(
        "get", "secret", "platform-credentials", "-n", "mcp-platform", "-o", "jsonpath={.data.admin-password}"
    ).stdout
    world.admin = session()
    r = world.admin.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": base64.b64decode(raw).decode()}
    )
    assert r.status_code == 200, r.text
    for name, store in stores(world.admin).items():  # leftovers from an interrupted run
        if name in STORES:
            if store["status"] != "deleting":
                assert world.admin.delete(f"/api/stores/{name}").status_code == 202
            wait_gone(world.admin, name)
    email = f"rohan.stores{world.ts}@example.com"
    invite = world.admin.post("/api/invites", json={"email": email, "role": "admin"})
    assert invite.status_code == 201, invite.text
    world.rohan = session()
    accepted = world.rohan.post(
        f"/api/invites/{invite.json()['inviteUrl'].rsplit('/', 1)[1]}/accept",
        json={"name": "Rohan Stores E2E", "password": secrets.token_urlsafe(18)},
    )
    assert accepted.status_code == 200, accepted.text
    world.rohan_id = accepted.json()["user"]["id"]
    key = world.rohan.post("/api/keys", json={"name": "rohan stores e2e"})
    assert key.status_code == 201, key.text
    world.key = key.json()["key"]


# -------------------------------------------------------------------------------------------- create + chaos


def test_01_B01_create_two_stores_concurrently(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    results: dict[str, httpx2.Response] = {}

    def create(name: str) -> None:
        with session() as client:
            client.cookies.update(rohan.cookies)
            results[name] = client.post("/api/stores", json={"name": name})

    world.started = time.monotonic()
    world.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1))
    threads = [threading.Thread(target=create, args=(name,)) for name in STORES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for name in STORES:
        assert results[name].status_code == 202, results[name].text
        store = results[name].json()["store"]
        assert store["status"] == "provisioning" and store["url"] == shop_url(name)
        assert store["toolsPrefix"] == f"woo-{name}__"


def test_02_B02_limits_and_names(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    third = rohan.post("/api/stores", json={"name": "berlin"})
    assert third.status_code == 409 and "per user" in third.json()["error"]["message"], third.text
    for bad in ("Paris", "ab", "par-is"):
        assert rohan.post("/api/stores", json={"name": bad}).status_code == 422
    reserved = rohan.post("/api/stores", json={"name": "main"})
    assert reserved.status_code == 422 and "reserved" in reserved.json()["error"]["message"]


def test_03_B04_kill_deployer_pod_mid_helm(world: World) -> None:
    """The Job retries (backoffLimit 1) and the script recovers the interrupted Helm release."""
    need(world.started, "the stores")

    def helm_running() -> bool:
        pods = deployer_pods("paris", world.started_at)
        return (
            bool(pods) and "5/6 helm" in kubectl("logs", "-n", "mcp-platform", pods[-1], check=False).stdout
        )

    eventually(helm_running, 180)
    time.sleep(20)  # Helm is now creating the release's resources
    (victim,) = deployer_pods("paris", world.started_at)
    kubectl("delete", "pod", victim, "-n", "mcp-platform", "--wait=false")
    eventually(lambda: any(p != victim for p in deployer_pods("paris", world.started_at)), 120)


def test_04_B03_kill_gateway_pods_during_provisioning(world: World) -> None:
    rohan = need(world.rohan, "Rohan")
    assert stores(rohan)["tokyo"]["status"] == "provisioning"
    kubectl("delete", "pod", "-n", "mcp-platform", "-l", "app.kubernetes.io/name=gateway", "--wait=false")
    kubectl(
        "rollout", "status", "deploy/platform-gateway", "-n", "mcp-platform", "--timeout=180s", timeout=200
    )
    eventually(lambda: rohan.get("/api/stores").status_code == 200, 60)  # sessions live in Postgres


def test_05_both_ready_within_10_minutes(world: World) -> None:
    rohan = need(world.rohan, "Rohan")

    def ready() -> bool:
        current = stores(rohan)
        for name in STORES:
            assert current[name]["status"] != "failed", f"{name}: {current[name]['statusReason']}"
        return all(current[name]["status"] == "ready" for name in STORES)

    remaining = READY_WITHIN_S - (time.monotonic() - world.started)
    eventually(ready, max(remaining, 1), step=5)
    world.ready = True
    print(f"both stores ready {time.monotonic() - world.started:.0f} s after creation")
    paris_job = json.loads(
        kubectl("get", "jobs", "-n", "mcp-platform", "-l", "wmg.dev/store=paris", "-o", "json").stdout
    )
    assert any((j["status"].get("failed") or 0) >= 1 for j in paris_job["items"]), (
        "B-04: the killed pod is counted"
    )


# -------------------------------------------------------------------------------------- B-01 / B-07 / B-05


def test_06_B01_urls_and_tools(world: World) -> None:
    need(world.ready, "both stores Ready (test_05)")
    key = need(world.key, "Rohan's key")
    with httpx2.Client(timeout=30, follow_redirects=True) as http:
        for name in STORES:
            page = http.get(shop_url(name))
            assert page.status_code == 200, (name, page.status_code)
            assert f"{name.capitalize()} Store" in page.text

    def tools_present() -> bool:
        tools = mcp_tools(key)
        return all(f"woo-{n}__list_orders" in tools and f"woo-{n}__create_product" in tools for n in STORES)

    eventually(tools_present, 60)


def test_07_B07_quota_and_limits(world: World) -> None:
    need(world.ready, "both stores Ready (test_05)")
    for name in STORES:
        quota = json.loads(
            kubectl("get", "resourcequota", f"{name}-quota", "-n", f"store-{name}", "-o", "json").stdout
        )
        hard, used = quota["status"]["hard"], quota["status"]["used"]
        assert set(hard) == {
            "requests.cpu",
            "requests.memory",
            "limits.cpu",
            "limits.memory",
            "pods",
            "persistentvolumeclaims",
            "requests.storage",
        }
        for resource, limit in hard.items():
            assert quantity(used[resource]) <= quantity(limit), (name, resource, used[resource], limit)
        assert kubectl("get", "limitrange", f"{name}-limits", "-n", f"store-{name}").returncode == 0


def test_08_B05_K08_cross_store_isolation(world: World) -> None:
    need(world.ready, "both stores Ready (test_05)")
    paris_mcp = {"app.kubernetes.io/name": "woo-mcp", "app.kubernetes.io/instance": "paris", "probe": "mcp"}
    assert probe(world, "store-paris", "http://tokyo-wordpress.store-tokyo/", paris_mcp) == "blocked"
    assert probe(world, "store-paris", "telnet://tokyo-mariadb.store-tokyo:3306", paris_mcp) == "blocked"
    assert probe(world, "store-paris", "http://tokyo-woo-mcp.store-tokyo:8080/livez", paris_mcp) == "blocked"
    # positive control from the same pod: its own store's WordPress answers
    assert (
        probe(world, "store-paris", "http://paris-wordpress/wp-includes/images/blank.gif", paris_mcp) == "200"
    )


@pytest.mark.parametrize(
    ("args", "allowed"),
    [
        (("create", "namespace", "wmg-evil"), False),
        (("delete", "namespace", "store-main"), False),
        (("delete", "namespace", "mcp-platform"), False),
        (
            (
                "create",
                "rolebinding",
                "evil",
                "-n",
                "mcp-platform",
                "--clusterrole=wmg-store-deployer",
                "--serviceaccount=mcp-platform:wmg-deployer",
            ),
            False,
        ),
        (("create", "namespace", "store-zzz"), True),  # control: a store namespace is fine
    ],
)
def test_09_SO6_deployer_admission_scope(world: World, args: tuple[str, ...], allowed: bool) -> None:
    """RBAC lets wmg-deployer manage namespaces and RoleBindings; admission confines it to store-*."""
    result = kubectl(*args, "--dry-run=server", f"--as={DEPLOYER}", check=False)
    if allowed:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "wmg-deployer-scope" in result.stderr and "may only change store-<name>" in result.stderr


@pytest.mark.parametrize(
    ("verb", "resource", "namespace", "allowed"),
    [
        ("get", "secrets", "mcp-platform", False),  # P14 refinement: no namespaced rights outside store-*
        ("list", "secrets", "store-main", False),
        ("create", "namespaces", "", True),
        ("get", "secrets", "store-paris", True),  # via the RoleBinding the deployer made there
    ],
)
def test_09b_deployer_rbac(world: World, verb: str, resource: str, namespace: str, allowed: bool) -> None:
    if namespace == "store-paris":
        need(world.ready, "store paris Ready (test_05)")
    ns = ["-n", namespace] if namespace else []
    out = kubectl("auth", "can-i", verb, resource, *ns, f"--as={DEPLOYER}", check=False).stdout.strip()
    assert out == ("yes" if allowed else "no"), (verb, resource, namespace, out)


# ------------------------------------------------------------------------------------------------- BR-AT


def test_10_B08_BR_AT_chat_compares_todays_orders(world: World) -> None:
    need(world.ready, "both stores Ready (test_05)")
    rohan = need(world.rohan, "Rohan")
    if not world.llm:
        pytest.skip("!!! LLM Secret platform-llm missing: BR-AT chat NOT RUN (make secrets-llm) !!!")
    orders = {name: store_order(name) for name in STORES}
    events = chat(rohan, "Compare today's orders across paris and tokyo")
    errors = [d.get("message", "") for k, d in events if k == "error"]
    if any("rate limit" in e for e in errors):  # an external quota, not a product failure: say so loudly
        pytest.skip(f"!!! LLM provider rate-limited: BR-AT chat NOT VERIFIED ({errors[0]}) !!!")
    calls = [data["name"] for kind, data in events if kind == "tool_call"]
    assert "woo-paris__list_orders" in calls and "woo-tokyo__list_orders" in calls, calls
    answer = next((d["text"] for k, d in events if k == "answer"), None)
    assert answer is not None, f"no answer event: {[(k, d) for k, d in events if k != 'tool_result']}"
    for name, order_id in orders.items():
        assert name in answer.lower() and str(order_id) in answer, (name, order_id, answer)


# ------------------------------------------------------------------------------------------------ B-06


def test_11_B06_delete_tokyo_removes_everything(world: World) -> None:
    need(world.ready, "both stores Ready (test_05)")
    rohan = need(world.rohan, "Rohan")
    key = need(world.key, "Rohan's key")
    pvs_before = kubectl(
        "get", "pv", "-o", "jsonpath={range .items[*]}{.spec.claimRef.namespace} {end}"
    ).stdout
    assert "store-tokyo" in pvs_before.split()
    r = rohan.delete("/api/stores/tokyo")
    assert r.status_code == 202 and r.json()["store"]["status"] == "deleting"
    eventually(lambda: not any(t.startswith("woo-tokyo__") for t in mcp_tools(key)), 30)  # tools vanish first
    assert any(t.startswith("woo-paris__") for t in mcp_tools(key))
    wait_gone(rohan, "tokyo")
    assert kubectl("get", "namespace", "store-tokyo", check=False).returncode != 0

    def pvs_released() -> bool:
        claims = kubectl(
            "get", "pv", "-o", "jsonpath={range .items[*]}{.spec.claimRef.namespace} {end}"
        ).stdout
        return "store-tokyo" not in claims.split()

    eventually(pvs_released, 120)
    leftovers = kubectl(
        "get",
        "secrets,jobs",
        "-n",
        "mcp-platform",
        "-l",
        "wmg.dev/store=tokyo,wmg.dev/action=provision",
        "-o",
        "name",
    ).stdout
    assert "secret/" not in leftovers  # the transient input Secret is gone


def test_12_cleanup(world: World) -> None:
    admin = need(world.admin, "the admin session")
    if os.environ.get("E2E_KEEP_STORES") != "1" and "paris" in stores(admin):
        assert admin.delete("/api/stores/paris").status_code == 202
        wait_gone(admin, "paris")
    if world.rohan_id:
        assert admin.patch(f"/api/users/{world.rohan_id}", json={"status": "disabled"}).status_code == 200

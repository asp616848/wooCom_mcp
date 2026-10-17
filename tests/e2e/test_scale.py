"""TESTING §6 SO7-01/SO7-02 and §7 performance (P16, D95): two gateway replicas on the running cluster
(`make platform SCALE=1`, then `make e2e-scale`, ~4 min; needs Node for `npx autocannon`).

NFR-P1/P2 are measured in-cluster (`tests/perf/incluster.py` inside a gateway pod), as the NFRs define
them. The pod kills run autocannon end to end through Traefik (`tests/perf/run.sh`): what MCP clients see.
"""

import base64
import json
import os
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client

pytestmark = [pytest.mark.e2e, pytest.mark.bonus]

SCHEME = os.environ.get("E2E_SCHEME", "http")
DOMAIN = os.environ.get("E2E_DOMAIN", "localhost")
DASHBOARD = f"{SCHEME}://dashboard.{DOMAIN}"
MCP_URL = f"{SCHEME}://gateway.{DOMAIN}/mcp"
CSRF = {"X-WMG-CSRF": "1"}
NS = "mcp-platform"
GATEWAY = "app.kubernetes.io/name=gateway"
PERF = Path(__file__).resolve().parents[1] / "perf"
RATE, LOAD_S, KILL_AT_S = 50, 40, 10
FAILOVER_WITHIN_S = 10


def kubectl(*args: str, check: bool = True, stdin: str | None = None, timeout: int = 120) -> str:
    result = subprocess.run(  # noqa: S603
        ["kubectl", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        input=stdin,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)}: {result.stderr}")
    return result.stdout + (result.stderr if not check else "")


def gateway_pods() -> list[str]:
    out = kubectl(
        "get", "pod", "-n", NS, "-l", GATEWAY, "--field-selector=status.phase=Running", "-o", "name"
    )
    return [p.removeprefix("pod/") for p in out.split()]


def metric(pod: str, name: str) -> float | None:
    script = (
        "import urllib.request as u\n"
        "t = u.urlopen('http://127.0.0.1:8080/metrics', timeout=2).read().decode()\n"
        f"print(next((l.split()[1] for l in t.splitlines() if l.startswith('{name} ')), ''))"
    )
    out = kubectl("exec", "-n", NS, pod, "--", "python", "-c", script, check=False).strip().splitlines()
    try:
        return float(out[-1]) if out else None
    except ValueError:
        return None


def leaders(pods: list[str] | None = None) -> list[str]:
    return [p for p in (pods or gateway_pods()) if metric(p, "wmg_leader") == 1.0]


def wait_two_ready(within: float = 120) -> None:
    deadline = time.monotonic() + within
    while True:
        ready = kubectl(
            "get", "deploy", "platform-gateway", "-n", NS, "-o", "jsonpath={.status.readyReplicas}"
        )
        if ready.strip() == "2" and len(gateway_pods()) == 2:
            return
        assert time.monotonic() < deadline, f"gateway not back to 2 ready replicas (ready={ready!r})"
        time.sleep(2)


@dataclass
class World:
    admin: httpx2.Client | None = None
    key: str = ""
    key_ids: list[str] = field(default_factory=list)


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    yield w
    if w.admin is not None:
        for key_id in w.key_ids:
            w.admin.delete(f"/api/keys/{key_id}")
        w.admin.close()


def new_key(w: World, name: str, limit: int | None) -> str:
    assert w.admin is not None
    r = w.admin.post("/api/keys", json={"name": name})
    assert r.status_code == 201, r.text
    created = r.json()
    w.key_ids.append(created["id"])
    if limit is not None:
        p = w.admin.patch(f"/api/keys/{created['id']}", json={"rateLimitPerMin": limit})
        assert p.status_code == 200, p.text
    return str(created["key"])


def in_cluster(pod: str, kind: str, credential: str, seconds: int = 30) -> dict[str, object]:
    script = (PERF / "incluster.py").read_text()
    args = ("exec", "-i", "-n", NS, pod, "--", "python", "-", kind, str(RATE), str(seconds), credential)
    out = kubectl(*args, stdin=script, timeout=seconds + 60)
    result: dict[str, object] = json.loads(out.strip().splitlines()[-1])
    print(f"in-cluster {kind}: {result}")
    return result


def start_load(key: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PERF_AUTH": f"Authorization=Bearer {key}", "PERF_URL": MCP_URL}
    return subprocess.Popen(  # noqa: S603
        [str(PERF / "run.sh"), "call", str(LOAD_S), str(RATE)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def finish_load(proc: subprocess.Popen[str]) -> dict[str, int]:
    out, err = proc.communicate(timeout=LOAD_S + 120)
    assert proc.returncode == 0, err[-2000:]
    raw = json.loads(out.strip().splitlines()[-1])
    summary = {k: int(raw[k]) for k in ("errors", "timeouts", "mismatches", "non2xx")}
    summary["requests"] = int(raw["requests"]["total"])
    summary["p97_5"] = int(raw["latency"]["p97_5"])
    print(f"load through Traefik: {summary}")
    return summary


def kill_leader_under_load(world: World, *force: str) -> tuple[dict[str, int], float, str, str]:
    pods = gateway_pods()
    current = leaders(pods)
    assert len(current) == 1, current
    old = current[0]
    load = start_load(world.key)
    time.sleep(KILL_AT_S)
    killed_at = time.monotonic()
    kubectl("delete", "pod", "-n", NS, old, "--wait=false", *force)
    while True:
        now_leading = [p for p in leaders() if p != old]
        if now_leading:
            break
        assert time.monotonic() - killed_at < FAILOVER_WITHIN_S, "no new leader within 10 s"
    took = time.monotonic() - killed_at
    print(f"leader {old} killed{' (forced)' if force else ''}: {now_leading[0]} leads after ≤ {took:.1f} s")
    return finish_load(load), took, old, now_leading[0]


def test_00_two_replicas_pdb_keepalives_one_leader(world: World) -> None:
    replicas = kubectl("get", "deploy", "platform-gateway", "-n", NS, "-o", "jsonpath={.spec.replicas}")
    if replicas.strip() != "2":
        pytest.skip("needs 2 gateway replicas: run `make platform SCALE=1` first")
    wait_two_ready()
    pdb = json.loads(kubectl("get", "pdb", "platform-gateway", "-n", NS, "-o", "json"))
    assert pdb["spec"]["minAvailable"] == 1
    for setting, value in (
        ("tcp_keepalives_idle", "5"),
        ("tcp_keepalives_interval", "1"),
        ("tcp_keepalives_count", "3"),
    ):
        shown = kubectl(
            "exec",
            "-n",
            NS,
            "platform-postgres-0",
            "--",
            "psql",
            "-U",
            "wmg",
            "-d",
            "wmg",
            "-h",
            "127.0.0.1",  # over TCP: a Unix-socket session always shows keepalives as 0
            "-tAc",
            f"SHOW {setting}",
        )
        assert shown.strip() == value, (setting, shown)
    assert len(leaders()) == 1

    raw = kubectl("get", "secret", "platform-credentials", "-n", NS, "-o", "jsonpath={.data.admin-password}")
    world.admin = httpx2.Client(base_url=DASHBOARD, headers=CSRF, timeout=60)
    login = {"email": "admin@example.com", "password": base64.b64decode(raw).decode()}
    assert world.admin.post("/api/auth/login", json=login).status_code == 200
    world.key = new_key(world, "scale e2e load", 100_000)  # a load key needs its own limit (D95)


def test_01_NFR_P1_P2_in_cluster(world: World) -> None:
    if not world.key:
        pytest.skip("needs test_00")
    pod = gateway_pods()[0]
    token = base64.b64decode(
        kubectl("get", "secret", "pincode-mcp-auth", "-n", "mcp-upstreams", "-o", "jsonpath={.data.token}")
    ).decode()
    listed = in_cluster(pod, "list", world.key)
    called = in_cluster(pod, "call", world.key)
    direct = in_cluster(pod, "direct", token)
    assert listed["errors"] == called["errors"] == direct["errors"] == 0
    assert float(listed["p95"]) < 20, listed  # type: ignore[arg-type]
    overhead = float(called["p95"]) - float(direct["p95"])  # type: ignore[arg-type]
    print(f"NFR-P2 overhead p95 = {overhead:.1f} ms")
    assert overhead < 25


def test_02_SO7_01_graceful_leader_kill_under_load(world: World) -> None:
    if not world.key:
        pytest.skip("needs test_00")
    wait_two_ready()
    load, took, old, new = kill_leader_under_load(world)
    # the follower and the replacement pod race for the lock: either may win, once the old leader let go
    assert new != old
    assert took < FAILOVER_WITHIN_S
    assert load["requests"] == RATE * LOAD_S
    assert load["errors"] == load["timeouts"] == load["mismatches"] == load["non2xx"] == 0, load
    wait_two_ready()


def test_03_SO7_01_forced_leader_kill_under_load(world: World) -> None:
    """SIGKILL, no grace period: only requests in flight on that pod may fail (≤ the 20 connections)."""
    if not world.key:
        pytest.skip("needs test_00")
    wait_two_ready()
    load, took, _old, _new = kill_leader_under_load(world, "--grace-period=0", "--force")
    assert took < FAILOVER_WITHIN_S
    failed = load["errors"] + load["timeouts"] + load["mismatches"] + load["non2xx"]
    assert failed <= 20, load
    wait_two_ready()


def test_04_pdb_refuses_evicting_the_last_ready_gateway() -> None:
    wait_two_ready()
    first, second = gateway_pods()

    def evict(pod: str) -> str:
        body = json.dumps(
            {"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": pod, "namespace": NS}}
        )
        path = f"/api/v1/namespaces/{NS}/pods/{pod}/eviction"
        return kubectl("create", "--raw", path, "-f", "-", stdin=body, check=False)

    assert "Failure" not in evict(first)
    refused = evict(second)
    assert "disruption budget" in refused, refused
    wait_two_ready()


async def _call(key: str) -> tuple[int, str] | None:
    """One tools/call through the ingress; returns the JSON-RPC error (code, message) or None on success."""
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
        Client(streamable_http_client(MCP_URL, http_client=http), cache=None) as client,
    ):
        try:
            result = await client.call_tool("pincode__list_serviceable_cities", {})
        except MCPError as exc:  # caught here: past the client's task group it becomes an ExceptionGroup
            return exc.error.code, exc.error.message
        assert not result.is_error, result.content
        return None


def test_05_SO7_02_rate_limit_through_ingress(world: World) -> None:
    if world.admin is None:
        pytest.skip("needs test_00")
    if time.time() % 60 > 45:  # keep the burst inside one calendar minute
        time.sleep(60 - time.time() % 60 + 0.5)
    key = new_key(world, "scale e2e limit", 3)
    assert [anyio.run(_call, key) for _ in range(3)] == [None, None, None]  # spread over both replicas
    error = anyio.run(_call, key)
    assert error is not None and error[0] == -32011 and "retry in" in error[1], error

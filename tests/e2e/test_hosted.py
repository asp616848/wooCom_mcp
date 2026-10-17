"""TESTING §6 SO5-01 (P15, D94): a hosted MCP server deployed from an image on the running cluster
(`make e2e-hosted`, ~3 min).

The pincode server's own image (the one `mcp-upstreams/pincode` runs) is deployed as `pincode-hosted` with a
header credential: Connected, tools listed and callable through the gateway, guardrails and the deployer's
admission limits in `mcp-hosted`, then deleted: Helm release, Service and Secrets gone.
"""

import base64
import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
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
SLUG = "pincode-hosted"
REL = f"hosted-{SLUG}"
NS = "mcp-hosted"
DEPLOYER = "system:serviceaccount:mcp-platform:wmg-deployer"
DEPLOYED_WITHIN_S = 300


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
    admin: httpx2.Client | None = None
    key: str = ""
    server_id: str = ""
    deployed: bool = False
    pods_before: int = 0  # other hosted servers may already run here (e.g. one deployed from the dashboard)


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    yield w
    if w.admin is not None:
        w.admin.close()


def need(value: Any, what: str) -> Any:
    if not value:
        pytest.skip(f"needs {what} from an earlier step")
    return value


def hosted(client: httpx2.Client) -> dict[str, dict[str, Any]]:
    r = client.get("/api/hosted-servers")
    assert r.status_code == 200, r.text
    return {h["slug"]: h for h in r.json()["hosted"]}


async def _mcp(key: str, call: tuple[str, dict[str, Any]] | None) -> Any:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=30) as http,
        Client(streamable_http_client(MCP_URL, http_client=http), cache=None) as client,
    ):
        if call is None:
            return [t.name for t in (await client.list_tools()).tools]
        return await client.call_tool(*call)


def mcp_tools(key: str) -> list[str]:
    result: list[str] = anyio.run(_mcp, key, None)
    return result


def pincode_image() -> str:
    """The image mcp-upstreams/pincode runs (local `wmg/mcp-pincode:<tag>`, prod `ghcr.io/<owner>/wmg-…`)."""
    path = "jsonpath={.spec.template.spec.containers[0].image}"
    return kubectl("get", "deploy", "pincode", "-n", "mcp-upstreams", "-o", path).stdout.strip()


def test_00_admin_and_a_clean_slate(world: World) -> None:
    raw = kubectl(
        "get", "secret", "platform-credentials", "-n", "mcp-platform", "-o", "jsonpath={.data.admin-password}"
    ).stdout
    world.admin = httpx2.Client(base_url=DASHBOARD, headers=CSRF, timeout=60)
    r = world.admin.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": base64.b64decode(raw).decode()}
    )
    assert r.status_code == 200, r.text
    left = hosted(world.admin).get(SLUG)  # leftover from an interrupted run
    if left is not None:
        if left["deployStatus"] != "removing":
            assert world.admin.delete(f"/api/hosted-servers/{left['serverId']}").status_code == 202
        eventually(lambda: SLUG not in hosted(world.admin), 300, step=5)  # type: ignore[arg-type]
    key = world.admin.post("/api/keys", json={"name": "hosted e2e"})
    assert key.status_code == 201, key.text
    world.key = key.json()["key"]


def quota_pods() -> int:
    quota = json.loads(kubectl("get", "resourcequota", "hosted-quota", "-n", NS, "-o", "json").stdout)
    return int(quota["status"]["used"]["pods"])


def test_01_SO5_deploy_pincode_hosted(world: World) -> None:
    admin = need(world.admin, "an admin session")
    world.pods_before = quota_pods()
    body = {
        "slug": SLUG,
        "name": "Pincode (hosted)",
        "image": pincode_image(),
        "authType": "header",
        "authHeaderName": "X-Api-Key",
        "env": {"LOG_LEVEL": "info"},
    }
    r = admin.post("/api/hosted-servers", json=body)
    assert r.status_code == 202, r.text
    world.server_id = r.json()["hosted"]["serverId"]

    def deployed() -> bool:
        h = hosted(admin)[SLUG]
        assert h["deployStatus"] != "failed", h["deployReason"]
        ready = h["deployStatus"] == "deployed" and h["serverStatus"] == "connected"
        return bool(ready and h["toolCount"] > 0)

    took = eventually(deployed, DEPLOYED_WITHIN_S, step=3)
    print(f"pincode-hosted deployed and connected in {took:.0f} s")
    world.deployed = True


def test_02_tools_listed_and_callable(world: World) -> None:
    need(world.deployed, "pincode-hosted deployed (test_01)")
    eventually(lambda: f"{SLUG}__check_delivery" in mcp_tools(world.key), 30)
    names = mcp_tools(world.key)
    hosted_tools = sorted(n.removeprefix(f"{SLUG}__") for n in names if n.startswith(f"{SLUG}__"))
    seeded = sorted(n.removeprefix("pincode__") for n in names if n.startswith("pincode__"))
    assert hosted_tools == seeded == ["check_delivery", "list_serviceable_cities"]
    result = anyio.run(_mcp, world.key, (f"{SLUG}__list_serviceable_cities", {}))
    assert not result.is_error, result.content


def test_03_guardrails_in_mcp_hosted(world: World) -> None:
    need(world.deployed, "pincode-hosted deployed (test_01)")
    assert quota_pods() == world.pods_before + 1
    netpol = kubectl("get", "networkpolicy", REL, "-n", NS, "-o", "json")
    assert json.loads(netpol.stdout)["spec"]["policyTypes"] == ["Ingress", "Egress"]
    labels = json.loads(kubectl("get", "namespace", NS, "-o", "json").stdout)["metadata"]["labels"]
    assert labels["wmg.dev/role"] == "hosted" and labels["pod-security.kubernetes.io/enforce"] == "restricted"
    # the credential is the gateway's: the pod reads it from the Secret the deployer made
    secret = kubectl("get", "secret", f"{REL}-mcp-auth", "-n", NS, "-o", "jsonpath={.data.token}").stdout
    assert len(base64.b64decode(secret)) == 48


@pytest.mark.parametrize(
    ("args", "allowed"),
    [
        (("create", "deployment", "x", "--image=wmg/x:1", "-n", NS), True),
        (("create", "quota", "x", "--hard=pods=100", "-n", NS), False),
        (("delete", "resourcequota", "hosted-quota", "-n", NS), False),
        (("delete", "rolebinding", "wmg-deployer", "-n", NS), False),
        (("label", "namespace", NS, "wmg.dev/role=store", "--overwrite"), False),
        (("delete", "namespace", NS), False),
    ],
)
def test_04_SO6_deployer_limits_in_mcp_hosted(world: World, args: tuple[str, ...], allowed: bool) -> None:
    result = kubectl(*args, "--dry-run=server", f"--as={DEPLOYER}", check=False)
    if allowed:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "wmg-deployer-scope" in result.stderr or "forbidden" in result.stderr.lower(), result.stderr


def test_05_delete_removes_everything(world: World) -> None:
    admin = need(world.admin, "an admin session")
    server_id = need(world.server_id, "a hosted server (test_01)")
    r = admin.delete(f"/api/hosted-servers/{server_id}")
    assert r.status_code == 202, r.text
    # tools vanish at once, before the uninstall
    eventually(lambda: not any(n.startswith(f"{SLUG}__") for n in mcp_tools(world.key)), 15)
    eventually(lambda: SLUG not in hosted(admin), 240, step=5)
    assert all(s["slug"] != SLUG for s in admin.get("/api/servers").json()["servers"])
    left = kubectl("get", "all", "-n", NS, "-l", f"app.kubernetes.io/instance={REL}")
    assert "No resources found" in left.stderr
    for kind, name in (("service", REL), ("secret", f"{REL}-mcp-auth"), ("secret", f"{REL}-env")):
        assert kubectl("get", kind, name, "-n", NS, check=False).returncode != 0, (kind, name)
    releases = kubectl("get", "secret", "-n", NS, "-l", f"owner=helm,name={REL}", "-o", "name").stdout
    assert releases.strip() == ""  # no Helm release records left

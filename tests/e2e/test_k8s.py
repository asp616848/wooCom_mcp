"""TESTING §4 (P11): network isolation, PSA, RBAC and the live SSRF table against the running cluster.

Network cases start a throwaway `curlimages/curl` pod with the labels of the workload it impersonates and
report the HTTP code, or "blocked" when no connection opens. "Blocked" cases sit next to a positive control
from the same kind of probe, so a broken probe can't pass as isolation. Run: `make test-k8s`.
"""

import base64
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.e2e

CURL_IMAGE = "curlimages/curl:8.22.0"
SCHEME = os.environ.get("E2E_SCHEME", "http")
DOMAIN = os.environ.get("E2E_DOMAIN", "localhost")
DASHBOARD = f"{SCHEME}://dashboard.{DOMAIN}"
SHOP = f"{SCHEME}://shop.{DOMAIN}"
GATEWAY_SA = "system:serviceaccount:mcp-platform:wmg-gateway"
# Pod-level settings that satisfy PSA "restricted" (the curl image's user is uid 100).
RESTRICTED = {
    "securityContext": {"runAsNonRoot": True, "runAsUser": 100, "seccompProfile": {"type": "RuntimeDefault"}},
}
RESTRICTED_CONTAINER = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["kubectl", *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=120,
        check=check,
    )


# k3s's NetworkPolicy controller (kube-router) programs a new pod's IP seconds after it starts, so probes run
# from long-lived pods that have settled, not from one-shot `kubectl run` pods.
SETTLE_S = 8.0
_PODS: dict[tuple[str, str], str] = {}


def _probe_pod(namespace: str, labels: dict[str, str] | None) -> str:
    label_arg = ",".join(f"{k}={v}" for k, v in sorted((labels or {}).items()))
    key = (namespace, label_arg)
    if key not in _PODS:
        name = f"nettest-{uuid.uuid4().hex[:8]}"
        container = {
            "name": name,
            "image": CURL_IMAGE,
            "command": ["sleep", "600"],
            "securityContext": RESTRICTED_CONTAINER,
        }
        spec = {**RESTRICTED, "terminationGracePeriodSeconds": 1, "containers": [container]}
        overrides = {"apiVersion": "v1", "spec": spec}
        cmd = ["run", name, "--restart=Never", f"--image={CURL_IMAGE}", "-n", namespace]
        cmd += [f"--labels={label_arg}"] if label_arg else []
        kubectl(*cmd, f"--overrides={json.dumps(overrides)}")
        kubectl("wait", "-n", namespace, "--for=condition=Ready", f"pod/{name}", "--timeout=90s")
        time.sleep(SETTLE_S)
        _PODS[key] = name
    return _PODS[key]


@pytest.fixture(scope="module", autouse=True)
def _cleanup_probe_pods() -> Iterator[None]:
    yield
    for (namespace, _), name in _PODS.items():
        kubectl("delete", "pod", name, "-n", namespace, "--wait=false", check=False)
    _PODS.clear()


def probe(namespace: str, url: str, labels: dict[str, str] | None = None) -> str:
    """HTTP status as a string; `connected` for a TCP port that accepted; `blocked` if nothing connected."""
    pod = _probe_pod(namespace, labels)
    tcp = url.startswith("telnet://")
    fmt = "RESULT=%{time_connect};" if tcp else "RESULT=%{http_code};"
    curl = ["curl", "-s", "-o", "/dev/null", "--connect-timeout", "3", "-m", "4", "-w", fmt, url]
    result = kubectl("exec", "-n", namespace, pod, "--", *curl, check=False)
    match = re.search(r"RESULT=([^;]*);", result.stdout)
    out = match.group(1) if match else ""
    if tcp:
        return "connected" if out and float(out) > 0 else "blocked"
    return out if out and out != "000" else "blocked"


def woo_mcp_labels() -> dict[str, str]:
    return {"app.kubernetes.io/name": "woo-mcp", "app.kubernetes.io/instance": "main"}


# ---------------------------------------------------------------------------------------------- network


def test_K01_storefront_public() -> None:
    assert httpx2.get(f"{SHOP}/", timeout=10).status_code in (200, 302)


def test_K02_woo_mcp_has_no_ingress_route() -> None:
    assert httpx2.post(f"{SHOP}/mcp", json={}, timeout=10).status_code == 404
    rules = json.loads(kubectl("get", "ingress", "-A", "-o", "json").stdout)
    backends = [
        p["backend"]["service"]["name"]
        for i in rules["items"]
        for r in i["spec"]["rules"]
        for p in r["http"]["paths"]
    ]
    assert not any("woo-mcp" in b for b in backends), backends


def test_K03_default_namespace_cannot_reach_woo_mcp() -> None:
    assert probe("default", "http://main-woo-mcp.store-main:8080/livez") == "blocked"
    # control: the same probe reaches Traefik (no policy there; 404 = connected, unknown host)
    assert probe("default", "http://traefik.kube-system/") == "404"


def test_K03b_wordpress_cannot_reach_woo_mcp() -> None:
    wordpress = {"app.kubernetes.io/name": "wordpress", "app.kubernetes.io/instance": "main"}
    assert probe("store-main", "http://main-woo-mcp:8080/livez", wordpress) == "blocked"


def test_K04_woo_mcp_cannot_reach_mariadb_but_reaches_wordpress() -> None:
    assert probe("store-main", "telnet://main-mariadb:3306", woo_mcp_labels()) == "blocked"
    assert (
        probe("store-main", "http://main-wordpress/wp-includes/images/blank.gif", woo_mcp_labels()) == "200"
    )


def test_K04b_wordpress_reaches_mariadb() -> None:
    wordpress = {"app.kubernetes.io/name": "wordpress", "app.kubernetes.io/instance": "main"}
    assert probe("store-main", "telnet://main-mariadb:3306", wordpress) == "connected"


def test_K05_gateway_reaches_woo_mcp() -> None:
    url = "http://main-woo-mcp.store-main:8080/livez"
    code = f"import urllib.request; print(urllib.request.urlopen('{url}', timeout=3).status)"
    out = kubectl("exec", "-n", "mcp-platform", "deploy/platform-gateway", "--", "python", "-c", code).stdout
    assert out.strip() == "200"


def test_K06_upstream_cannot_reach_store() -> None:
    gst = {"app.kubernetes.io/name": "gst", "app.kubernetes.io/instance": "gst"}
    assert probe("mcp-upstreams", "http://main-wordpress.store-main/", gst) == "blocked"
    assert probe("mcp-upstreams", "https://example.com/", gst) == "blocked"


def test_K07_postgres_only_from_gateway() -> None:
    assert probe("default", "telnet://platform-postgres.mcp-platform:5432") == "blocked"
    gateway_like = {"app.kubernetes.io/name": "gateway", "app.kubernetes.io/instance": "other"}
    assert probe("mcp-platform", "telnet://platform-postgres:5432", gateway_like) == "blocked"
    code = "import socket; socket.create_connection(('platform-postgres', 5432), 3); print('ok')"
    out = kubectl("exec", "-n", "mcp-platform", "deploy/platform-gateway", "--", "python", "-c", code).stdout
    assert out.strip() == "ok"


def test_unlabelled_pod_in_platform_is_isolated() -> None:
    assert probe("mcp-platform", "http://platform-gateway:8080/livez") == "blocked"
    assert probe("mcp-platform", "https://example.com/") == "blocked"


# ---------------------------------------------------------------------------------------------- PSA


@pytest.mark.parametrize(
    ("namespace", "level", "role"),
    [
        ("mcp-platform", "restricted", "platform"),
        ("mcp-upstreams", "restricted", "upstreams"),
        ("store-main", "baseline", "store"),
    ],
)
def test_psa_labels_and_no_violations(namespace: str, level: str, role: str) -> None:
    labels = json.loads(kubectl("get", "ns", namespace, "-o", "json").stdout)["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == level
    assert labels["wmg.dev/role"] == role
    # a server-side dry run of the same level reports any running pod that would violate it
    result = kubectl(
        "label",
        "--dry-run=server",
        "--overwrite",
        "ns",
        namespace,
        f"pod-security.kubernetes.io/enforce={level}",
    )
    assert "violate" not in result.stderr, result.stderr
    pods = json.loads(kubectl("get", "pods", "-n", namespace, "-o", "json").stdout)["items"]
    assert pods and all(p["status"]["phase"] in ("Running", "Succeeded") for p in pods)


# ---------------------------------------------------------------------------------------------- RBAC (K-10)


@pytest.mark.parametrize(
    ("verb", "resource", "namespace", "allowed"),
    [
        ("create", "jobs.batch", "mcp-platform", True),
        ("delete", "jobs.batch", "mcp-platform", True),
        ("create", "secrets", "mcp-platform", True),
        ("get", "pods/log", "mcp-platform", True),
        ("list", "secrets", "mcp-platform", False),
        ("update", "secrets", "mcp-platform", False),
        ("create", "deployments.apps", "mcp-platform", False),
        ("create", "jobs.batch", "store-main", False),
        ("get", "secrets", "store-main", False),
        ("get", "secrets", "kube-system", False),
        ("create", "namespaces", "", False),
        ("create", "clusterrolebindings.rbac.authorization.k8s.io", "", False),
    ],
)
def test_K10_gateway_rbac(verb: str, resource: str, namespace: str, allowed: bool) -> None:
    ns = ["-n", namespace] if namespace else []
    result = kubectl("auth", "can-i", verb, resource, *ns, f"--as={GATEWAY_SA}", check=False)
    assert result.stdout.strip() == ("yes" if allowed else "no")


# ------------------------------------------------------------------------------------------ SSRF (§3.2, live)


@pytest.fixture(scope="module")
def admin() -> Any:
    raw = kubectl(
        "get", "secret", "platform-credentials", "-n", "mcp-platform", "-o", "jsonpath={.data.admin-password}"
    ).stdout
    password = base64.b64decode(raw).decode()
    http = httpx2.Client(base_url=DASHBOARD, headers={"X-WMG-CSRF": "1"}, timeout=30)
    r = http.post("/api/auth/login", json={"email": "admin@example.com", "password": password})
    assert r.status_code == 200
    yield http
    http.close()


BLOCKED = [
    "http://platform-postgres.mcp-platform.svc.cluster.local:5432",
    "http://kubernetes.default.svc",
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1:8080/mcp",
    "http://localhost/mcp",
    "http://gateway.localhost/mcp",
    "https://2130706433/",
    "https://0x7f.1/",
    "https://[::ffff:127.0.0.1]/",
    "http://10.43.0.1/",
    "https://192.168.1.10/",
    "https://100.64.0.1/",
    "http://example.com/mcp",
    "https://user:pass@example.com/",
    "ftp://example.com/",
]


@pytest.mark.parametrize("url", BLOCKED)
def test_ssrf_blocked_live(admin: Any, url: str) -> None:
    r = admin.post("/api/servers/test", json={"url": url, "authType": "none"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] in ("SSRF_BLOCKED", "VALIDATION_FAILED")


@pytest.mark.parametrize(
    "url",
    ["http://gst.mcp-upstreams.svc.cluster.local:8080/mcp", "http://main-woo-mcp.store-main.svc:8080/mcp"],
)
def test_ssrf_allowed_live(admin: Any, url: str) -> None:
    r = admin.post("/api/servers/test", json={"url": url, "authType": "none"})
    assert r.status_code == 200, r.text  # reachable but 401 without a credential: ok=false, not an SSRF block

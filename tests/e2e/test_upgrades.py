"""TESTING §6 SO9-01 (P16, D96): Helm upgrade and rollback of the gateway, an MCP server and the store on the
running local cluster (`make e2e-upgrades`, ~6 min).

The "new" gateway and pincode versions are the running images retagged `<tag>-next` and imported into k3d
(the VPS demo upgrades to a real new commit instead; TRACKER "P16 evidence"). The store is backed up, upgraded
with a new title and given new data (a product and an upload), rolled back (the setup Job re-applies the old
title), then restored (the new data is gone).
"""

import base64
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
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
SHOP = f"{SCHEME}://shop.{DOMAIN}"
CSRF = {"X-WMG-CSRF": "1"}
ROOT = Path(__file__).resolve().parents[2]
CLUSTER = os.environ.get("CLUSTER", "woo-mcp")
MARKER = f"SO9 marker {int(time.time())}"
UPLOAD = "wp-content/uploads/so9-marker.txt"


def run(*args: str, timeout: int = 900, stdin: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603
        list(args), capture_output=True, text=True, timeout=timeout, check=False, cwd=ROOT, input=stdin
    )
    assert result.returncode == 0, f"{' '.join(args)}: {result.stderr[-2000:]}"
    return result.stdout


def kubectl(*args: str, **kw: Any) -> str:
    return run("kubectl", *args, **kw)


def helm_revision(release: str, ns: str) -> int:
    history = json.loads(run("helm", "history", release, "-n", ns, "--max", "1", "-o", "json"))
    return int(history[-1]["revision"])


def images(ns: str, deployment: str) -> set[str]:
    path = "jsonpath={range .items[*]}{.spec.containers[0].image}{'\\n'}{end}"
    selector = f"app.kubernetes.io/instance={deployment}"
    pods = kubectl(
        "get", "pod", "-n", ns, "-l", selector, "--field-selector=status.phase=Running", "-o", path
    )
    return set(pods.split())


def gateway_images() -> set[str]:
    return {i for i in images("mcp-platform", "platform") if "/gateway:" in i}


def deployment_image(ns: str, name: str) -> str:
    return kubectl(
        "get", "deploy", name, "-n", ns, "-o", "jsonpath={.spec.template.spec.containers[0].image}"
    )


def retag_next(image: str) -> str:
    """The running image under a new tag, imported into k3d: a new version as far as Helm is concerned."""
    nxt = f"{image}-next"
    run("docker", "tag", image, nxt)
    run("k3d", "image", "import", "-c", CLUSTER, nxt, timeout=300)
    return nxt


def eventually(check: Callable[[], bool], within: float, step: float = 3.0) -> None:
    deadline = time.monotonic() + within
    while not check():
        assert time.monotonic() < deadline, f"not reached within {within:.0f} s"
        time.sleep(step)


def sql(query: str) -> str:
    """One value from the store's database (root password read inside the pod)."""
    script = 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" exec mariadb -uroot -N -B wordpress'
    return kubectl(
        "exec", "-i", "-n", "store-main", "main-mariadb-0", "--", "sh", "-c", script, stdin=query
    ).strip()


def marker_count() -> str:
    return sql(f"SELECT count(*) FROM wp_posts WHERE post_title = '{MARKER}'")  # noqa: S608 (our own constant)


def in_wordpress(path: str) -> bool:
    found = subprocess.run(  # noqa: S603
        ["kubectl", "exec", "-n", "store-main", "deploy/main-wordpress", "--", "test", "-e", path],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return found.returncode == 0


def platform_counts() -> dict[str, int]:
    query = (
        "SELECT json_build_object('users', (SELECT count(*) FROM users), "
        "'keys', (SELECT count(*) FROM api_keys), 'servers', (SELECT count(*) FROM mcp_servers), "
        "'migrations', (SELECT count(*) FROM schema_migrations))"
    )
    args = (
        "exec",
        "-n",
        "mcp-platform",
        "platform-postgres-0",
        "--",
        "psql",
        "-U",
        "wmg",
        "-d",
        "wmg",
        "-tAc",
        query,
    )
    counts: dict[str, int] = json.loads(kubectl(*args))
    return counts


async def _mcp(key: str, call: tuple[str, dict[str, Any]] | None) -> Any:
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60) as http,
        Client(streamable_http_client(MCP_URL, http_client=http), cache=None) as client,
    ):
        if call is None:
            return [t.name for t in (await client.list_tools()).tools]
        result = await client.call_tool(*call)
        return None if result.is_error else result


def works(key: str, tool: str, args: dict[str, Any] | None = None) -> bool:
    try:
        return anyio.run(_mcp, key, (tool, args or {})) is not None
    except Exception:
        return False


@dataclass
class World:
    admin: httpx2.Client | None = None
    key: str = ""
    key_id: str = ""
    backup: str = ""
    store_revision: int = 0


@pytest.fixture(scope="module")
def world() -> Iterator[World]:
    w = World()
    yield w
    if w.admin is not None:
        if w.key_id:
            w.admin.delete(f"/api/keys/{w.key_id}")
        w.admin.close()


def need(value: Any, what: str) -> Any:
    if not value:
        pytest.skip(f"needs {what} from an earlier step")
    return value


def test_00_admin_key_and_local_images(world: World) -> None:
    if not deployment_image("mcp-platform", "platform-gateway").startswith("wmg/"):
        pytest.skip("local k3d only: the VPS demo upgrades to a real new commit (docs/upgrades-rollback.md)")
    raw = kubectl(
        "get", "secret", "platform-credentials", "-n", "mcp-platform", "-o", "jsonpath={.data.admin-password}"
    )
    world.admin = httpx2.Client(base_url=DASHBOARD, headers=CSRF, timeout=60)
    login = {"email": "admin@example.com", "password": base64.b64decode(raw).decode()}
    assert world.admin.post("/api/auth/login", json=login).status_code == 200
    r = world.admin.post("/api/keys", json={"name": "upgrades e2e"})
    assert r.status_code == 201, r.text
    world.key, world.key_id = r.json()["key"], r.json()["id"]
    assert works(world.key, "pincode__list_serviceable_cities")


def test_01_SO9_gateway_upgrade_then_rollback(world: World) -> None:
    """New image → a new revision serving the same DB; rollback → the previous image, same DB, same keys."""
    key = need(world.key, "an admin key (test_00)")
    before_rev = helm_revision("platform", "mcp-platform")
    before_image = deployment_image("mcp-platform", "platform-gateway")
    before_counts = platform_counts()
    tag = retag_next(before_image).rsplit(":", 1)[1]

    run("helm", "upgrade", "platform", "charts/mcp-platform", "-n", "mcp-platform", "--reuse-values",
        "--set-string", f"gateway.image.tag={tag}", "--wait", "--timeout", "5m")  # fmt: skip
    assert helm_revision("platform", "mcp-platform") == before_rev + 1
    eventually(lambda: gateway_images() == {f"{before_image}-next"}, 120)  # old pods gone too
    assert works(key, "pincode__list_serviceable_cities")
    assert platform_counts() == before_counts

    run("helm", "rollback", "platform", str(before_rev), "-n", "mcp-platform", "--wait", "--timeout", "5m")
    assert helm_revision("platform", "mcp-platform") == before_rev + 2
    eventually(lambda: gateway_images() == {before_image}, 120)
    assert deployment_image("mcp-platform", "platform-gateway") == before_image
    eventually(lambda: works(key, "pincode__list_serviceable_cities"), 60)
    assert platform_counts() == before_counts
    last = json.loads(run("helm", "history", "platform", "-n", "mcp-platform", "--max", "1", "-o", "json"))[
        -1
    ]
    assert last["description"] == f"Rollback to {before_rev}"


def test_02_SO9_mcp_server_upgrade_then_rollback(world: World) -> None:
    """pincode (mcp-server chart): the gateway keeps reaching it through upgrade and rollback."""
    key = need(world.key, "an admin key (test_00)")
    before_rev = helm_revision("pincode", "mcp-upstreams")
    before_image = deployment_image("mcp-upstreams", "pincode")
    tag = retag_next(before_image).rsplit(":", 1)[1]

    run("helm", "upgrade", "pincode", "charts/mcp-server", "-n", "mcp-upstreams", "--reuse-values",
        "--set-string", f"image.tag={tag}", "--wait", "--timeout", "5m")  # fmt: skip
    assert deployment_image("mcp-upstreams", "pincode") == f"{before_image}-next"
    eventually(lambda: works(key, "pincode__list_serviceable_cities"), 60)

    run("helm", "rollback", "pincode", str(before_rev), "-n", "mcp-upstreams", "--wait", "--timeout", "5m")
    assert helm_revision("pincode", "mcp-upstreams") == before_rev + 2
    assert deployment_image("mcp-upstreams", "pincode") == before_image
    eventually(lambda: works(key, "pincode__list_serviceable_cities"), 60)


def test_03_SO9_store_backup_upgrade_rollback_restore(world: World) -> None:
    key = need(world.key, "an admin key (test_00)")
    hooks = run("helm", "get", "hooks", "main", "-n", "store-main")
    assert "post-rollback" in hooks, (
        "the store runs woo-store < 0.3.1 (no post-rollback setup): `make store` first"
    )
    title = sql("SELECT option_value FROM wp_options WHERE option_name = 'blogname'")
    products = sql("SELECT count(*) FROM wp_posts WHERE post_type = 'product'")
    world.store_revision = helm_revision("main", "store-main")
    world.backup = run("scripts/backup-store.sh", "main", timeout=600).strip().splitlines()[-1]
    assert (ROOT / world.backup / "SHA256SUMS").is_file()

    # upgrade: new config (title) through Helm; the setup Job converges WordPress to it
    run("helm", "upgrade", "main", "charts/woo-store", "-n", "store-main", "--reuse-values",
        "--set-string", f"store.title={title} v2", "--wait", "--timeout", "10m")  # fmt: skip
    assert sql("SELECT option_value FROM wp_options WHERE option_name = 'blogname'") == f"{title} v2"
    # new data while on the new version
    created = anyio.run(_mcp, key, ("woo-main__create_product", {"name": MARKER, "regular_price": "1.00"}))
    assert created is not None
    kubectl(
        "exec",
        "-n",
        "store-main",
        "deploy/main-wordpress",
        "--",
        "sh",
        "-c",
        f"echo so9 > /var/www/html/{UPLOAD}",
    )

    # rollback: the old config comes back (post-rollback setup Job), the data doesn't
    run(
        "helm",
        "rollback",
        "main",
        str(world.store_revision),
        "-n",
        "store-main",
        "--wait",
        "--timeout",
        "10m",
    )
    assert sql("SELECT option_value FROM wp_options WHERE option_name = 'blogname'") == title
    assert marker_count() == "1"

    # restore: the data comes back to the backup
    run("scripts/restore-store.sh", "main", world.backup.rsplit("/", 1)[1], "--yes", timeout=600)
    assert marker_count() == "0"
    assert sql("SELECT count(*) FROM wp_posts WHERE post_type = 'product'") == products
    assert not in_wordpress(f"/var/www/html/{UPLOAD}"), "the upload made after the backup is still there"
    # the store still works end to end: storefront and the woo-mcp tools through the gateway
    assert httpx2.get(SHOP, timeout=30, follow_redirects=True).status_code == 200
    eventually(lambda: works(key, "woo-main__get_store_info"), 60)
    shutil.rmtree(ROOT / world.backup)  # only the backup this test made; real backups are kept

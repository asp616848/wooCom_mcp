"""P14 (DESIGN §13): the stores API rules and the reconciler state machine, on real Postgres + a fake JobApi.

The cluster side (deployer image, Helm, RBAC, admission policy, isolation) is in tests/e2e/test_stores.py.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest

from wmg_gateway.db.pool import create_pool
from wmg_gateway.provisioner.k8s import K8sError, del_job_name, prov_job_name
from wmg_gateway.provisioner.stores import StoreReconciler

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CSRF = {"X-WMG-CSRF": "1"}
ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
PROVISIONING = {"deployer_image": "wmg/deployer:t", "woo_mcp_image": "wmg/woo-mcp:t"}


async def job(pool: Any, kind: str, name: str, attempt: int) -> str:
    """The Job name the reconciler uses for this store row (name + id prefix + attempt)."""
    async with pool.acquire() as conn:
        store_id = await conn.fetchval("SELECT id FROM stores WHERE name = $1", name)
    return (prov_job_name if kind == "prov" else del_job_name)(name, str(store_id), attempt)


@asynccontextmanager
async def session(gw: Any, creds: dict[str, str]) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        assert (await http.post("/api/auth/login", json=creds)).status_code == 200
        yield http


async def add_member(admin: httpx2.AsyncClient, gw: Any, email: str) -> dict[str, str]:
    invite = await admin.post("/api/invites", json={"email": email, "role": "member"})
    token = invite.json()["inviteUrl"].rsplit("/", 1)[1]
    creds = {"email": email, "password": "member-password-1"}
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as anon:
        r = await anon.post(
            f"/api/invites/{token}/accept", json={"name": "Ritika", "password": creds["password"]}
        )
        assert r.status_code == 200
    return creds


async def test_provisioning_disabled_without_images(gateway_factory: Any) -> None:
    gw = gateway_factory(run_leader=False)
    async with session(gw, ADMIN) as admin:
        r = await admin.post("/api/stores", json={"name": "paris"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "PROVISIONING_DISABLED"
        assert (await admin.get("/api/stores")).json() == {"stores": []}


async def test_store_api_rules(gateway_factory: Any) -> None:
    """B-02: names, duplicates, per-user and total limits, visibility, retry/delete rules."""
    gw = gateway_factory(run_leader=False, **PROVISIONING)
    async with session(gw, ADMIN) as admin:
        created = await admin.post("/api/stores", json={"name": "paris"})
        assert created.status_code == 202
        store = created.json()["store"]
        assert store == {
            "name": "paris",
            "status": "provisioning",
            "statusReason": None,
            "url": "http://paris.shop.localhost",
            "attempt": 1,
            "owner": "admin@example.com",
            "toolsPrefix": "woo-paris__",
            "createdAt": store["createdAt"],
            "readyAt": None,
        }
        assert (await admin.post("/api/stores", json={"name": "paris"})).status_code == 409
        for bad in ("Paris", "ab", "1paris", "par-is", "a" * 17):
            r = await admin.post("/api/stores", json={"name": bad})
            assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_FAILED", bad
        reserved = await admin.post("/api/stores", json={"name": "main"})
        assert reserved.status_code == 422 and "reserved" in reserved.json()["error"]["message"]
        assert (await admin.post("/api/stores", json={"name": "tokyo"})).status_code == 202
        third = await admin.post("/api/stores", json={"name": "berlin"})
        assert third.status_code == 409 and "per user" in third.json()["error"]["message"]

        ritika = await add_member(admin, gw, "ritika@example.com")
        rohan = await add_member(admin, gw, "rohan@example.com")
        async with session(gw, ritika) as member:
            assert (await member.post("/api/stores", json={"name": "delhi"})).status_code == 202
            assert (await member.post("/api/stores", json={"name": "mumbai"})).status_code == 202
            assert [s["name"] for s in (await member.get("/api/stores")).json()["stores"]] == [
                "delhi",
                "mumbai",
            ]
            assert (await member.delete("/api/stores/paris")).status_code == 404  # someone else's: hidden
            assert (await member.post("/api/stores/paris/retry")).status_code == 404
            retry = await member.post("/api/stores/delhi/retry")
            assert retry.status_code == 409 and "failed" in retry.json()["error"]["message"]
            assert (await member.delete("/api/stores/UPPER")).status_code == 422
        async with session(gw, rohan) as other:
            full = await other.post("/api/stores", json={"name": "pune"})
            assert full.status_code == 409 and "in total" in full.json()["error"]["message"]
        names = [s["name"] for s in (await admin.get("/api/stores")).json()["stores"]]
        assert names == ["paris", "tokyo", "delhi", "mumbai"]  # admin sees all

        deleting = await admin.delete("/api/stores/delhi")  # admins may delete any store
        assert deleting.status_code == 202
        assert deleting.json()["store"]["status"] == "deleting" and deleting.json()["store"]["attempt"] == 2
        again = await admin.delete("/api/stores/delhi")  # idempotent
        assert again.status_code == 202 and again.json()["store"]["attempt"] == 2


async def test_store_name_taken_by_manual_server(gateway_factory: Any) -> None:
    gw = gateway_factory(run_leader=False, **PROVISIONING)
    async with session(gw, ADMIN) as admin:
        server = {"slug": "woo-goa", "name": "Manual", "url": "http://x.mcp-upstreams.svc/mcp"}
        assert (await admin.post("/api/servers", json=server)).status_code == 201
        r = await admin.post("/api/stores", json={"name": "goa"})
        assert r.status_code == 409


async def test_reconciler_lifecycle(gateway_factory: Any, database_url: str, fake_jobs: Any) -> None:
    """Queueing, ready + registration, failure + logs, retry, delete (registration first), teardown fail."""
    gw = gateway_factory(
        run_leader=False, provision_concurrency=2, max_stores_per_user=10, max_stores_total=10, **PROVISIONING
    )
    rt = gw.app.state.runtime
    fake = fake_jobs
    ready: list[uuid.UUID] = []
    removed: list[uuid.UUID] = []

    async def on_ready(server_id: uuid.UUID) -> None:
        ready.append(server_id)

    async def on_removed(server_id: uuid.UUID) -> None:
        removed.append(server_id)

    pool = await create_pool(database_url, 1, 4)
    recon = StoreReconciler(pool, fake, rt.settings, rt.cipher, on_ready=on_ready, on_removed=on_removed)

    async def row(name: str) -> Any:
        async with pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM stores WHERE name = $1", name)

    try:
        async with session(gw, ADMIN) as admin:
            for name in ("paris", "tokyo", "delhi"):
                assert (await admin.post("/api/stores", json={"name": name})).status_code == 202

            await recon.tick()
            assert fake.created == [
                await job(pool, "prov", "paris", 1),
                await job(pool, "prov", "tokyo", 1),
            ]  # concurrency 2
            assert (await row("delhi"))["status_reason"] == "queued"
            assert (await row("paris"))["status_reason"] == "installing"
            paris = await row("paris")
            token = rt.cipher.decrypt(paris["mcp_token_enc"], str(paris["id"]))
            inputs = fake.secrets["wmg-prov-paris"]
            assert inputs["mcp-token"] == token and len(token) == 48
            assert json.loads(inputs["values.json"])["store"]["host"] == "paris.shop.localhost"

            await recon.tick()
            assert len(fake.created) == 2  # nothing duplicated while Jobs run

            # paris succeeds → registered, ready, input removed; the freed slot starts delhi
            fake.finish(await job(pool, "prov", "paris", 1), "succeeded")
            await recon.tick()
            paris = await row("paris")
            assert (
                paris["status"] == "ready"
                and paris["ready_at"] is not None
                and paris["status_reason"] is None
            )
            async with pool.acquire() as conn:
                server = await conn.fetchrow("SELECT * FROM mcp_servers WHERE slug = 'woo-paris'")
                policies = await conn.fetch(
                    "SELECT tool_name FROM tool_policies WHERE server_id = $1 AND admin_only", server["id"]
                )
            assert server["source"] == "provisioned" and server["auth_type"] == "bearer"
            assert server["url"] == "http://paris-woo-mcp.store-paris.svc.cluster.local:8080/mcp"
            assert rt.cipher.decrypt(server["credential_enc"], str(server["id"])) == token
            assert paris["server_id"] == server["id"] and ready == [server["id"]]
            assert sorted(p["tool_name"] for p in policies) == [
                "create_product",
                "update_order_status",
                "update_product",
            ]
            assert "wmg-prov-paris" not in fake.secrets
            assert fake.created[-1] == await job(pool, "prov", "delhi", 1)
            servers = (await admin.get("/api/servers")).json()["servers"]
            assert any(s["slug"] == "woo-paris" and s["source"] == "provisioned" for s in servers)
            denied = await admin.delete(f"/api/servers/{server['id']}")
            assert denied.status_code == 409  # provisioned servers go with their store

            # tokyo fails → reason has the Job condition and the log tail; retry → attempt 2, new Job name
            fake.logs[await job(pool, "prov", "tokyo", 1)] = (
                "Error: INSTALLATION FAILED: context deadline exceeded"
            )
            fake.finish(
                await job(pool, "prov", "tokyo", 1),
                "failed",
                "BackoffLimitExceeded: Job has reached the backoff limit",
            )
            await recon.tick()
            tokyo = await row("tokyo")
            assert tokyo["status"] == "failed"
            assert tokyo["status_reason"].startswith("provisioning failed (BackoffLimitExceeded")
            assert "INSTALLATION FAILED" in tokyo["status_reason"]
            retried = await admin.post("/api/stores/tokyo/retry")
            assert retried.status_code == 202 and retried.json()["store"]["attempt"] == 2
            await recon.tick()
            assert fake.created[-1] == await job(pool, "prov", "tokyo", 2)

            # a fresh reconciler (new leader) resumes from DB + Job state without duplicating work
            await StoreReconciler(pool, fake, rt.settings, rt.cipher).tick()
            assert fake.created.count(await job(pool, "prov", "tokyo", 2)) == 1

            # delete paris: MCP registration removed first, then teardown, then the row
            assert (await admin.delete("/api/stores/paris")).status_code == 202
            await recon.tick()
            async with pool.acquire() as conn:
                assert await conn.fetchval("SELECT count(*) FROM mcp_servers WHERE slug = 'woo-paris'") == 0
                assert (
                    await conn.fetchval(
                        "SELECT count(*) FROM server_tools WHERE server_id = $1", server["id"]
                    )
                    == 0
                )
            assert removed == [server["id"]] and fake.created[-1] == await job(pool, "del", "paris", 2)
            assert (await row("paris"))["status_reason"] == "removing"
            fake.finish(await job(pool, "del", "paris", 2), "succeeded")
            await recon.tick()
            assert await row("paris") is None

            # regression: a recreated store must not inherit the old store's finished Jobs (kept 1 h by TTL)
            old_job = next(n for n in fake.jobs if n.startswith("wmg-prov-paris-"))
            fake.finish(old_job, "failed", "BackoffLimitExceeded: old store")
            fake.finish(await job(pool, "prov", "tokyo", 2), "succeeded")  # frees a slot (concurrency 2)
            assert (await admin.post("/api/stores", json={"name": "paris"})).status_code == 202
            await recon.tick()
            assert (await row("paris"))["status"] == "provisioning"
            assert fake.created[-1] == await job(pool, "prov", "paris", 1) != old_job
            assert (await admin.delete("/api/stores/paris")).status_code == 202
            await recon.tick()
            fake.finish(await job(pool, "del", "paris", 2), "succeeded")
            await recon.tick()
            assert await row("paris") is None
            assert [s["name"] for s in (await admin.get("/api/stores")).json()["stores"]] == [
                "tokyo",
                "delhi",
            ]

            # delete delhi while its provisioning Job runs → that Job is stopped before teardown starts
            assert (await admin.delete("/api/stores/delhi")).status_code == 202
            await recon.tick()
            assert await job(pool, "prov", "delhi", 1) in fake.deleted and fake.created[-1] == await job(
                pool, "del", "delhi", 2
            )
            fake.finish(await job(pool, "del", "delhi", 2), "failed", "DeadlineExceeded: too slow")
            await recon.tick()
            delhi = await row("delhi")
            assert delhi["status"] == "failed" and delhi["status_reason"].startswith("teardown failed")
            refused = await admin.post("/api/stores/delhi/retry")
            assert (
                refused.status_code == 409 and "delete the store again" in refused.json()["error"]["message"]
            )
            assert (await admin.delete("/api/stores/delhi")).json()["store"]["attempt"] == 3
            await recon.tick()
            assert fake.created[-1] == await job(pool, "del", "delhi", 3)
    finally:
        await pool.close()


async def test_reconciler_timeouts_errors_and_fencing(
    gateway_factory: Any, database_url: str, fake_jobs: Any
) -> None:
    gw = gateway_factory(run_leader=False, max_stores_per_user=10, max_stores_total=10, **PROVISIONING)
    rt = gw.app.state.runtime
    fake = fake_jobs
    pool = await create_pool(database_url, 1, 4)
    recon = StoreReconciler(pool, fake, rt.settings, rt.cipher)

    async def row(name: str) -> Any:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT *, extract(epoch FROM now() - updated_at) AS age_s FROM stores WHERE name = $1", name
            )

    try:
        async with session(gw, ADMIN) as admin:
            # transient API errors leave the store alone; a permanent one fails it with the reason
            assert (await admin.post("/api/stores", json={"name": "rome"})).status_code == 202
            fake.create_error = K8sError(503, "Service Unavailable")
            await recon.tick()
            assert (await row("rome"))["status"] == "provisioning"
            fake.create_error = K8sError(403, "Forbidden")
            await recon.tick()
            rome = await row("rome")
            assert rome["status"] == "failed"
            assert rome["status_reason"] == "could not start provisioning: Kubernetes API 403 Forbidden"
            fake.create_error = None

            # a Job that never finishes is stopped after PROVISION_TIMEOUT_S + 120 s
            assert (await admin.post("/api/stores", json={"name": "oslo"})).status_code == 202
            await recon.tick()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE stores SET updated_at = now() - interval '2 hours' WHERE name = 'oslo'"
                )
            await recon.tick()
            oslo = await row("oslo")
            assert oslo["status"] == "failed" and oslo["status_reason"].startswith("provisioning timeout")
            assert await job(pool, "prov", "oslo", 1) in fake.deleted

            # fencing: a row read before the user deleted the store is never turned 'ready' afterwards
            assert (await admin.post("/api/stores", json={"name": "lima"})).status_code == 202
            await recon.tick()
            stale = await row("lima")
            assert (await admin.delete("/api/stores/lima")).status_code == 202
            await recon._mark_ready(stale)
            lima = await row("lima")
            assert lima["status"] == "deleting" and lima["server_id"] is None

            # a manual server took the slug after the store was requested → failed with a clear reason
            assert (await admin.post("/api/stores", json={"name": "kyiv"})).status_code == 202
            await recon.tick()
            server = {"slug": "woo-kyiv", "name": "Manual", "url": "http://x.mcp-upstreams.svc/mcp"}
            assert (await admin.post("/api/servers", json=server)).status_code == 201
            fake.finish(await job(pool, "prov", "kyiv", 1), "succeeded")
            await recon.tick()
            kyiv = await row("kyiv")
            assert kyiv["status"] == "failed" and "is used by another server" in kyiv["status_reason"]
    finally:
        await pool.close()

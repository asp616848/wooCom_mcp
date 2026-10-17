"""SO-5 (DESIGN §14, D94): the hosted-servers API rules and the reconciler state machine, on real Postgres + a
fake JobApi. The cluster side (deployer, mcp-server chart, quota, isolation) is in tests/e2e/test_hosted.py.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest

from wmg_gateway.db.pool import create_pool
from wmg_gateway.provisioner.hosted import HostedReconciler
from wmg_gateway.provisioner.k8s import JobInfo, host_job_name, unhost_job_name

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

CSRF = {"X-WMG-CSRF": "1"}
ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
PROVISIONING = {"deployer_image": "wmg/deployer:t", "woo_mcp_image": "wmg/woo-mcp:t"}
PINCODE = {
    "slug": "pincode-hosted",
    "name": "Pincode (hosted)",
    "image": "wmg/mcp-pincode:t1",
    "authType": "header",
    "authHeaderName": "X-Api-Key",
    "env": {"LOG_LEVEL": "info"},
    "secretEnv": {"UPSTREAM_TOKEN": "s3cr3t-value"},
}


@asynccontextmanager
async def session(gw: Any) -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        assert (await http.post("/api/auth/login", json=ADMIN)).status_code == 200
        yield http


async def test_hosted_disabled_without_provisioning(gateway_factory: Any) -> None:
    gw = gateway_factory()
    async with session(gw) as admin:
        r = await admin.post("/api/hosted-servers", json=PINCODE)
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROVISIONING_DISABLED"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"image": "quay.io/x/y:1"}, "must start with one of wmg/, ghcr.io/, docker.io/"),
        ({"image": "wmg/mcp-pincode"}, "explicit tag"),
        ({"image": "wmg/mcp-pincode@sha256:" + "a" * 64}, "explicit tag"),
        ({"env": {"PORT": "1"}}, "reserved"),
        ({"secretEnv": {"MCP_AUTH_TOKEN": "x"}}, "reserved"),
        ({"env": {"1BAD": "x"}}, "not a valid variable name"),
        ({"env": {"A": "1"}, "secretEnv": {"A": "2"}}, "not both"),
        ({"env": {f"V{i}": "x" for i in range(21)}}, "at most 20"),
        ({"slug": "bad--slug"}, "no '--'"),
        ({"authType": "header", "authHeaderName": None}, "authHeaderName is required"),
        ({"authHeaderName": "Host"}, "not an allowed header name"),
        ({"port": 80}, "greater than or equal to 1024"),
        ({"size": "large"}, "small"),
    ],
)
async def test_hosted_validation(gateway_factory: Any, change: dict[str, Any], message: str) -> None:
    gw = gateway_factory(run_leader=False, **PROVISIONING)
    async with session(gw) as admin:
        r = await admin.post("/api/hosted-servers", json={**PINCODE, **change})
    assert r.status_code == 422, r.text
    assert message in r.json()["error"]["message"], r.json()


async def test_hosted_lifecycle(gateway_factory: Any, database_url: str, fake_jobs: Any) -> None:
    """Create (disabled, secrets encrypted) → queue → deploy → enabled; fail + retry; delete (tools first)."""
    gw = gateway_factory(run_leader=False, provision_concurrency=1, **PROVISIONING)
    rt = gw.app.state.runtime
    fake = fake_jobs
    ready: list[uuid.UUID] = []
    removed: list[uuid.UUID] = []

    async def on_ready(server_id: uuid.UUID) -> None:
        ready.append(server_id)

    async def on_removed(server_id: uuid.UUID) -> None:
        removed.append(server_id)

    pool = await create_pool(database_url, 1, 4)
    recon = HostedReconciler(pool, fake, rt.settings, rt.cipher, on_ready=on_ready, on_removed=on_removed)

    async def row(server_id: str) -> Any:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT h.*, s.enabled, s.url, s.credential_enc, s.source FROM hosted_deployments h "
                "JOIN mcp_servers s ON s.id = h.server_id WHERE h.server_id = $1",
                uuid.UUID(server_id),
            )

    try:
        async with session(gw) as admin:
            r = await admin.post("/api/hosted-servers", json=PINCODE)
            assert r.status_code == 202, r.text
            hosted = r.json()["hosted"]
            sid = hosted["serverId"]
            assert hosted["deployStatus"] == "deploying" and hosted["deployReason"] == "queued"
            assert hosted["serverStatus"] == "disabled" and hosted["secretEnvKeys"] == ["UPSTREAM_TOKEN"]
            assert hosted["url"] == "http://hosted-pincode-hosted.mcp-hosted.svc.cluster.local:8080/mcp"
            assert "s3cr3t-value" not in r.text
            dup = await admin.post("/api/hosted-servers", json=PINCODE)
            assert dup.status_code == 409
            # hosted rows are managed here, not on the Servers API
            assert (await admin.patch(f"/api/servers/{sid}", json={"enabled": True})).status_code == 409
            assert (await admin.delete(f"/api/servers/{sid}")).status_code == 409
            db_row = await row(sid)
            assert db_row["source"] == "hosted" and db_row["enabled"] is False
            assert "s3cr3t-value" not in (db_row["secret_env_enc"] or "")

            # a store's provision Job holds the only slot → queued
            fake.jobs["wmg-prov-paris-x-1"] = fake_running("wmg-prov-paris-x-1", "provision")
            await recon.tick()
            assert fake.created == [] and (await row(sid))["deploy_reason"] == "queued"
            fake.jobs.pop("wmg-prov-paris-x-1")

            # deploy: input Secret (values + token) and Job
            await recon.tick()
            assert fake.created == [host_job_name("pincode-hosted", sid, 1)]
            inputs = fake.secrets["wmg-hosted-pincode-hosted"]
            values = json.loads(inputs["values.json"])
            token = rt.cipher.decrypt(db_row["credential_enc"], sid)
            assert inputs["token"] == token and len(token) == 48
            assert values == {
                "image": {"repository": "wmg/mcp-pincode", "tag": "t1"},
                "port": 8080,
                "path": "/mcp",
                "auth": {
                    "type": "header",
                    "existingSecret": "hosted-pincode-hosted-mcp-auth",
                    "headerName": "X-Api-Key",
                },
                "env": {"LOG_LEVEL": "info"},
                "secretEnv": {"UPSTREAM_TOKEN": "s3cr3t-value"},
                "size": "small",
                "networkPolicy": {"allowInternet": False, "gatewayNamespace": "mcp-platform"},
            }
            assert (await row(sid))["deploy_reason"] == "deploying"

            # Job fails → reason with log tail; retry → attempt 2, new Job name
            fake.logs[host_job_name("pincode-hosted", sid, 1)] = "Error: ImagePullBackOff"
            fake.finish(host_job_name("pincode-hosted", sid, 1), "failed", "BackoffLimitExceeded: limit")
            await recon.tick()
            failed = await row(sid)
            assert failed["deploy_status"] == "failed" and "ImagePullBackOff" in failed["deploy_reason"]
            assert "wmg-hosted-pincode-hosted" not in fake.secrets
            retried = await admin.post(f"/api/hosted-servers/{sid}/retry")
            assert retried.status_code == 202 and retried.json()["hosted"]["attempt"] == 2
            assert (await admin.post(f"/api/hosted-servers/{sid}/retry")).status_code == 409
            await recon.tick()
            assert fake.created[-1] == host_job_name("pincode-hosted", sid, 2)

            # success → enabled, deployed, probed
            fake.finish(host_job_name("pincode-hosted", sid, 2), "succeeded")
            await recon.tick()
            done = await row(sid)
            assert done["deploy_status"] == "deployed" and done["enabled"] is True
            assert done["deployed_at"] is not None and ready == [uuid.UUID(sid)]
            listed = (await admin.get("/api/hosted-servers")).json()["hosted"]
            assert [h["slug"] for h in listed] == ["pincode-hosted"]

            # delete: disabled at once (tools vanish), remove Job, rows gone on success
            r = await admin.delete(f"/api/hosted-servers/{sid}")
            assert r.status_code == 202 and r.json()["hosted"]["deployStatus"] == "removing"
            assert (await row(sid))["enabled"] is False
            assert (await admin.delete(f"/api/hosted-servers/{sid}")).json()["hosted"]["attempt"] == 3
            await recon.tick()
            assert fake.created[-1] == unhost_job_name("pincode-hosted", sid, 3)
            fake.finish(unhost_job_name("pincode-hosted", sid, 3), "succeeded")
            await recon.tick()
            assert await row(sid) is None and removed == [uuid.UUID(sid)]
            assert (await admin.get("/api/hosted-servers")).json()["hosted"] == []
            r = await admin.get("/api/servers")
            assert all(s["slug"] != "pincode-hosted" for s in r.json()["servers"])
    finally:
        await pool.close()


async def test_removal_failure_and_delete_during_deploy(
    gateway_factory: Any, database_url: str, fake_jobs: Any
) -> None:
    gw = gateway_factory(run_leader=False, **PROVISIONING)
    rt = gw.app.state.runtime
    fake = fake_jobs
    pool = await create_pool(database_url, 1, 4)
    recon = HostedReconciler(pool, fake, rt.settings, rt.cipher)
    try:
        async with session(gw) as admin:
            body = {**PINCODE, "authType": "none", "authHeaderName": None, "secretEnv": {}}
            sid = (await admin.post("/api/hosted-servers", json=body)).json()["hosted"]["serverId"]
            await recon.tick()
            deploy = host_job_name("pincode-hosted", sid, 1)
            assert "token" not in fake.secrets["wmg-hosted-pincode-hosted"]  # authType none
            # delete while deploying → the running deploy Job is stopped first
            await admin.delete(f"/api/hosted-servers/{sid}")
            await recon.tick()
            assert deploy in fake.deleted and fake.created[-1] == unhost_job_name("pincode-hosted", sid, 2)
            fake.finish(unhost_job_name("pincode-hosted", sid, 2), "failed", "DeadlineExceeded: too slow")
            await recon.tick()
            hosted = (await admin.get("/api/hosted-servers")).json()["hosted"][0]
            assert hosted["deployStatus"] == "failed" and hosted["deployReason"].startswith("removal failed")
            assert (await admin.post(f"/api/hosted-servers/{sid}/retry")).status_code == 409
            assert (await admin.delete(f"/api/hosted-servers/{sid}")).status_code == 202  # delete again
    finally:
        await pool.close()


def fake_running(name: str, action: str) -> JobInfo:
    return JobInfo(name, "paris", action, "running")

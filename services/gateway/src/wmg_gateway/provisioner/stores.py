"""Store reconciler (DESIGN §13.2): runs on the leader every `RECONCILE_INTERVAL_S`.

State lives in Postgres (`stores`) and in Kubernetes (deployer Jobs with deterministic names), so a restarted
or new leader resumes exactly where the old one stopped (BR-7). Every DB transition is conditional on the
row's (status, attempt) that this tick read, so a concurrent API change (e.g. delete while provisioning) is
never overwritten.

| state        | Job                    | action                                                           |
|--------------|------------------------|------------------------------------------------------------------|
| provisioning | none                   | < concurrency running → input Secret + Job, else reason 'queued' |
| provisioning | succeeded              | register MCP server (+ write-tool policies) → ready, probe it    |
| provisioning | failed / deadline      | failed with the Job reason + last log lines                      |
| provisioning | running > timeout+120s | delete Job → failed 'provisioning timeout'                       |
| deleting     | any                    | MCP registration removed first → teardown Job → row deleted      |
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from wmg_common.logging import get_logger
from wmg_gateway.bootstrap import WOO_ADMIN_ONLY_TOOLS
from wmg_gateway.events.bus import notify
from wmg_gateway.provisioner.k8s import (
    COMPONENT_LABEL,
    INSTALL_ACTIONS,
    STORE_LABEL,
    JobApi,
    JobInfo,
    K8sError,
    del_job_name,
    deployer_job,
    input_secret_name,
    prov_job_name,
)
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.settings import Settings

log = get_logger(__name__)

GRACE_S = 120  # reconciler-level timeout on top of the Job's activeDeadlineSeconds
REASON_MAX = 1000
REASON_QUEUED = "queued"
REASON_INSTALLING = "installing"
REASON_REMOVING = "removing"


def server_slug(store: str) -> str:
    return f"woo-{store}"


def woo_mcp_url(store: str) -> str:
    """Canonical in-cluster URL (DESIGN §2.3)."""
    return f"http://{store}-woo-mcp.store-{store}.svc.cluster.local:8080/mcp"


def store_values(settings: Settings, store: str, host: str) -> dict[str, Any]:
    """Per-store Helm values (`/input/values.json`), layered over values-<env>.yaml and values-bonus.yaml."""
    repository, _, tag = settings.woo_mcp_image.rpartition(":")
    return {
        "store": {"host": host, "scheme": settings.store_scheme, "title": f"{store.capitalize()} Store"},
        "existingSecret": f"{store}-store-credentials",
        "ingress": {"tls": {"secretName": f"{store}-shop-tls"}},
        "wooMcp": {"image": {"repository": repository, "tag": tag}},
    }


def failure_reason(prefix: str, logs: str) -> str:
    tail = logs.strip()
    text = f"{prefix}\n{tail}" if tail else prefix
    return text if len(text) <= REASON_MAX else text[: REASON_MAX - 1] + "…"


def _transient(exc: K8sError) -> bool:
    return exc.status == 0 or exc.status == 429 or exc.status >= 500


class StoreReconciler:
    def __init__(
        self,
        pool: Any,
        k8s: JobApi,
        settings: Settings,
        cipher: CredentialCipher,
        on_ready: Callable[[uuid.UUID], Awaitable[None]] | None = None,
        on_removed: Callable[[uuid.UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._pool = pool
        self._k8s = k8s
        self._settings = settings
        self._cipher = cipher
        self._on_ready = on_ready
        self._on_removed = on_removed

    async def run_forever(self) -> None:
        interval = self._settings.reconcile_interval_s
        log.info("store reconciler started", interval_s=interval)
        while True:
            started = anyio.current_time()
            try:
                await self.tick()
            except Exception as exc:  # one bad tick must not stop the loop
                log.error("store reconcile tick failed", error=type(exc).__name__, exc_info=exc)
            await anyio.sleep(max(0.0, interval - (anyio.current_time() - started)))

    async def tick(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT *, extract(epoch FROM now() - updated_at) AS age_s FROM stores "
                "WHERE status IN ('provisioning','deleting') ORDER BY created_at"
            )
        if not rows:
            return  # idle: no Kubernetes calls
        jobs = {j.name: j for j in await self._k8s.list_jobs()}
        # stores and hosted servers share the concurrency limit (D94)
        running = sum(1 for j in jobs.values() if j.action in INSTALL_ACTIONS and j.state == "running")
        for row in rows:
            try:
                if row["status"] == "provisioning":
                    running = await self._provisioning(row, jobs, running)
                else:
                    await self._deleting(row, jobs)
            except K8sError as exc:
                if _transient(exc):
                    log.warning("kubernetes unavailable, retrying", store=row["name"], status=exc.status)
                    continue
                verb = "start provisioning" if row["status"] == "provisioning" else "start teardown"
                await self._fail(row, f"could not {verb}: Kubernetes API {exc.status} {exc.message}")

    # ------------------------------------------------------------------------------------------ provisioning

    async def _provisioning(self, row: Any, jobs: dict[str, JobInfo], running: int) -> int:
        name, attempt = row["name"], row["attempt"]
        job = jobs.get(prov_job_name(name, str(row["id"]), attempt))
        if job is None:
            if running >= self._settings.provision_concurrency:
                if row["status_reason"] != REASON_QUEUED:
                    await self._set_reason(row, REASON_QUEUED, touch=False)
                return running
            await self._start_provisioning(row)
            return running + 1
        if job.state == "succeeded":
            await self._mark_ready(row)
        elif job.state == "failed":
            logs = await self._k8s.job_logs(job.name)
            await self._fail(row, failure_reason(f"provisioning failed ({job.reason})", logs))
        elif row["age_s"] > self._settings.provision_timeout_s + GRACE_S:
            await self._k8s.delete_job(job.name)
            await self._fail(row, f"provisioning timeout (no result after {int(row['age_s'])} s)")
        return running

    async def _start_provisioning(self, row: Any) -> None:
        name = row["name"]
        token = self._cipher.decrypt(row["mcp_token_enc"], str(row["id"]))
        values = store_values(self._settings, name, row["host"])
        labels = {**COMPONENT_LABEL, STORE_LABEL: name}
        await self._k8s.put_secret(
            input_secret_name(name), {"values.json": json.dumps(values), "mcp-token": token}, labels
        )
        await self._k8s.create_job(
            deployer_job(
                name=prov_job_name(name, str(row["id"]), row["attempt"]),
                namespace=self._settings.k8s_namespace,
                image=self._settings.deployer_image,
                target=name,
                action="provision",
                values_env=self._settings.store_values_env,
                deadline_s=self._settings.provision_timeout_s,
            )
        )
        await self._set_reason(row, REASON_INSTALLING, touch=True)  # the timeout counts from here
        log.info("store provisioning started", store=name, attempt=row["attempt"])

    async def _mark_ready(self, row: Any) -> None:
        name, slug = row["name"], server_slug(row["name"])
        token = self._cipher.decrypt(row["mcp_token_enc"], str(row["id"]))
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if not await self._still(conn, row):
                    return
                existing = await conn.fetchrow(
                    "SELECT id, source FROM mcp_servers WHERE slug = $1 FOR UPDATE", slug
                )
                if existing is not None and existing["source"] != "provisioned":
                    reason = f"server slug '{slug}' is used by another server"
                    await self._update_status(conn, row, "failed", reason)
                    return
                server_id = existing["id"] if existing else uuid.uuid4()
                credential = self._cipher.encrypt(token, str(server_id))
                if existing:
                    await conn.execute(
                        "UPDATE mcp_servers SET url = $2, auth_type = 'bearer', auth_header_name = NULL, "
                        "credential_enc = $3, enabled = true, updated_at = now() WHERE id = $1",
                        server_id,
                        woo_mcp_url(name),
                        credential,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO mcp_servers (id, slug, name, url, auth_type, credential_enc, "
                        "timeout_ms, source, created_by) "
                        "VALUES ($1, $2, $3, $4, 'bearer', $5, $6, 'provisioned', $7)",
                        server_id,
                        slug,
                        f"WooCommerce ({name} store)",
                        woo_mcp_url(name),
                        credential,
                        self._settings.default_tool_timeout_ms,
                        row["owner_id"],
                    )
                await conn.executemany(  # D57: Woo write tools are admin-only, like the main store
                    "INSERT INTO tool_policies (server_id, tool_name, admin_only, updated_by) "
                    "VALUES ($1, $2, true, $3) ON CONFLICT DO NOTHING",
                    [(server_id, tool, row["owner_id"]) for tool in WOO_ADMIN_ONLY_TOOLS],
                )
                await conn.execute(
                    "UPDATE stores SET status = 'ready', status_reason = NULL, server_id = $2, "
                    "ready_at = now(), updated_at = now() WHERE id = $1",
                    row["id"],
                    server_id,
                )
                await notify(conn, "catalog_changed", str(server_id))
        log.info("store ready", store=name, server=slug)
        await self._drop_input(name)
        if self._on_ready is not None:
            await self._on_ready(server_id)

    # -------------------------------------------------------------------------------------------- deleting

    async def _deleting(self, row: Any, jobs: dict[str, JobInfo]) -> None:
        name = row["name"]
        await self._unregister(name)  # tools vanish before anything is torn down
        job = jobs.get(del_job_name(name, str(row["id"]), row["attempt"]))
        if job is None:
            for stale in jobs.values():  # a provisioning run still in flight is stopped first
                if stale.store == name and stale.action == "provision" and stale.state == "running":
                    await self._k8s.delete_job(stale.name)
            await self._drop_input(name)
            await self._k8s.create_job(
                deployer_job(
                    name=del_job_name(name, str(row["id"]), row["attempt"]),
                    namespace=self._settings.k8s_namespace,
                    image=self._settings.deployer_image,
                    target=name,
                    action="teardown",
                    values_env=self._settings.store_values_env,
                    deadline_s=self._settings.teardown_timeout_s,
                )
            )
            await self._set_reason(row, REASON_REMOVING, touch=True)
            log.info("store teardown started", store=name, attempt=row["attempt"])
        elif job.state == "succeeded":
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM stores WHERE id = $1 AND status = 'deleting' AND attempt = $2",
                    row["id"],
                    row["attempt"],
                )
            log.info("store deleted", store=name)
        elif job.state == "failed":
            logs = await self._k8s.job_logs(job.name)
            await self._fail(row, failure_reason(f"teardown failed ({job.reason})", logs))
        elif row["age_s"] > self._settings.teardown_timeout_s + GRACE_S:
            await self._k8s.delete_job(job.name)
            await self._fail(row, f"teardown failed: timeout (no result after {int(row['age_s'])} s)")

    async def _unregister(self, name: str) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            server_id = await conn.fetchval(
                "DELETE FROM mcp_servers WHERE slug = $1 AND source = 'provisioned' RETURNING id",
                server_slug(name),
            )
            if server_id is None:
                return
            await notify(conn, "catalog_changed", str(server_id))
        log.info("store server unregistered", store=name)
        if self._on_removed is not None:
            await self._on_removed(server_id)

    # ------------------------------------------------------------------------------------------- helpers

    async def _still(self, conn: Any, row: Any) -> bool:
        """Lock the row and confirm nobody changed its status/attempt since this tick read it."""
        current = await conn.fetchrow(
            "SELECT status, attempt FROM stores WHERE id = $1 FOR UPDATE", row["id"]
        )
        return current is not None and (current["status"], current["attempt"]) == (
            row["status"],
            row["attempt"],
        )

    async def _update_status(self, conn: Any, row: Any, status: str, reason: str | None) -> None:
        await conn.execute(
            "UPDATE stores SET status = $4, status_reason = $5, updated_at = now() "
            "WHERE id = $1 AND status = $2 AND attempt = $3",
            row["id"],
            row["status"],
            row["attempt"],
            status,
            reason,
        )

    async def _set_reason(self, row: Any, reason: str, *, touch: bool) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE stores SET status_reason = $4, "
                "updated_at = CASE WHEN $5 THEN now() ELSE updated_at END "
                "WHERE id = $1 AND status = $2 AND attempt = $3",
                row["id"],
                row["status"],
                row["attempt"],
                reason,
                touch,
            )

    async def _fail(self, row: Any, reason: str) -> None:
        async with self._pool.acquire() as conn:
            await self._update_status(conn, row, "failed", reason[:REASON_MAX])
        log.warning("store failed", store=row["name"], phase=row["status"], reason=reason.splitlines()[0])
        await self._drop_input(row["name"])

    async def _drop_input(self, name: str) -> None:
        try:
            await self._k8s.delete_secret(input_secret_name(name))
        except K8sError as exc:  # leftover input is harmless (overwritten next attempt); never block on it
            log.warning("input secret cleanup failed", store=name, status=exc.status)

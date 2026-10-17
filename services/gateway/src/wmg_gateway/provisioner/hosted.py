"""Hosted MCP server reconciler (DESIGN §14, SO-5, D94): runs on the leader next to the store reconciler.

Same pattern as stores (§13.2): state in Postgres (`hosted_deployments`) + deployer Jobs with deterministic
names (slug + first 8 hex digits of the server id + attempt), transitions conditional on (status, attempt).

| deploy_status | Job                    | action                                                            |
|---------------|------------------------|-------------------------------------------------------------------|
| deploying     | none                   | < concurrency running → input Secret + Job, else reason 'queued'  |
| deploying     | succeeded              | enable the mcp_servers row → deployed, NOTIFY, probe it           |
| deploying     | failed / deadline      | failed with the Job reason + last log lines                       |
| removing      | any                    | server disabled first → remove Job → rows deleted                 |
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import anyio

from wmg_common.logging import get_logger
from wmg_gateway.events.bus import notify
from wmg_gateway.provisioner.k8s import (
    COMPONENT_LABEL,
    HOSTED_LABEL,
    INSTALL_ACTIONS,
    JobApi,
    JobInfo,
    K8sError,
    deployer_job,
    host_job_name,
    hosted_input_secret_name,
    unhost_job_name,
)
from wmg_gateway.provisioner.stores import GRACE_S, REASON_MAX, failure_reason
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.settings import Settings

log = get_logger(__name__)

REASON_QUEUED = "queued"
REASON_DEPLOYING = "deploying"
REASON_REMOVING = "removing"
REMOVAL_FAILED = "removal failed"  # prefix: such a row can only be deleted again, not retried


def release_name(slug: str) -> str:
    return f"hosted-{slug}"


def auth_secret_name(slug: str) -> str:
    return f"hosted-{slug}-mcp-auth"


def hosted_url(slug: str, namespace: str, port: int, path: str) -> str:
    """Canonical in-cluster URL of a hosted server (DESIGN §2.3)."""
    return f"http://{release_name(slug)}.{namespace}.svc.cluster.local:{port}{path}"


def split_image(image: str) -> tuple[str, str]:
    repository, _, tag = image.rpartition(":")
    return repository, tag


def hosted_values(
    settings: Settings,
    row: Any,
    env: dict[str, str],
    secret_env: dict[str, str],
) -> dict[str, Any]:
    """mcp-server chart values (`/input/values.json`) for one hosted server."""
    repository, tag = split_image(row["image"])
    auth_type = row["auth_type"]
    auth: dict[str, Any] = {"type": auth_type}
    if auth_type != "none":
        auth["existingSecret"] = auth_secret_name(row["slug"])
    if auth_type == "header":
        auth["headerName"] = row["auth_header_name"]
    return {
        "image": {"repository": repository, "tag": tag},
        "port": row["port"],
        "path": row["path"],
        "auth": auth,
        "env": env,
        "secretEnv": secret_env,
        "size": row["size"],
        "networkPolicy": {"allowInternet": row["allow_internet"], "gatewayNamespace": settings.k8s_namespace},
    }


def _transient(exc: K8sError) -> bool:
    return exc.status == 0 or exc.status == 429 or exc.status >= 500


_ROWS = (
    "SELECT h.*, s.slug, s.name, s.auth_type, s.auth_header_name, s.credential_enc, "
    "extract(epoch FROM now() - h.updated_at) AS age_s "
    "FROM hosted_deployments h JOIN mcp_servers s ON s.id = h.server_id "
    "WHERE h.deploy_status IN ('deploying','removing') ORDER BY h.created_at"
)


class HostedReconciler:
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
        log.info("hosted reconciler started", interval_s=interval)
        while True:
            started = anyio.current_time()
            try:
                await self.tick()
            except Exception as exc:  # one bad tick must not stop the loop
                log.error("hosted reconcile tick failed", error=type(exc).__name__, exc_info=exc)
            await anyio.sleep(max(0.0, interval - (anyio.current_time() - started)))

    async def tick(self) -> None:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_ROWS)
        if not rows:
            return  # idle: no Kubernetes calls
        jobs = {j.name: j for j in await self._k8s.list_jobs()}
        # stores and hosted servers share the concurrency limit (D94)
        running = sum(1 for j in jobs.values() if j.action in INSTALL_ACTIONS and j.state == "running")
        for row in rows:
            try:
                if row["deploy_status"] == "deploying":
                    running = await self._deploying(row, jobs, running)
                else:
                    await self._removing(row, jobs)
            except K8sError as exc:
                if _transient(exc):
                    log.warning("kubernetes unavailable, retrying", hosted=row["slug"], status=exc.status)
                    continue
                api = f"Kubernetes API {exc.status} {exc.message}"
                if row["deploy_status"] == "deploying":
                    await self._fail(row, f"could not start the deploy: {api}")
                else:
                    await self._fail(row, f"{REMOVAL_FAILED}: could not start: {api}")

    # ---------------------------------------------------------------------------------------------- deploy

    async def _deploying(self, row: Any, jobs: dict[str, JobInfo], running: int) -> int:
        slug, sid = row["slug"], str(row["server_id"])
        job = jobs.get(host_job_name(slug, sid, row["attempt"]))
        if job is None:
            if running >= self._settings.provision_concurrency:
                if row["deploy_reason"] != REASON_QUEUED:
                    await self._set_reason(row, REASON_QUEUED, touch=False)
                return running
            await self._start_deploy(row)
            return running + 1
        if job.state == "succeeded":
            await self._mark_deployed(row)
        elif job.state == "failed":
            logs = await self._k8s.job_logs(job.name)
            await self._fail(row, failure_reason(f"deploy failed ({job.reason})", logs))
        elif row["age_s"] > self._settings.hosted_deploy_timeout_s + GRACE_S:
            await self._k8s.delete_job(job.name)
            await self._fail(row, f"deploy timeout (no result after {int(row['age_s'])} s)")
        return running

    async def _start_deploy(self, row: Any) -> None:
        slug, sid = row["slug"], str(row["server_id"])
        secret_env: dict[str, str] = (
            json.loads(self._cipher.decrypt(row["secret_env_enc"], sid)) if row["secret_env_enc"] else {}
        )
        values = hosted_values(self._settings, row, row["env"] or {}, secret_env)
        data = {"values.json": json.dumps(values)}
        if row["credential_enc"]:
            data["token"] = self._cipher.decrypt(row["credential_enc"], sid)
        labels = {**COMPONENT_LABEL, HOSTED_LABEL: slug}
        await self._k8s.put_secret(hosted_input_secret_name(slug), data, labels)
        await self._k8s.create_job(
            deployer_job(
                name=host_job_name(slug, sid, row["attempt"]),
                namespace=self._settings.k8s_namespace,
                image=self._settings.deployer_image,
                target=slug,
                action="deploy",
                values_env=self._settings.store_values_env,
                deadline_s=self._settings.hosted_deploy_timeout_s,
                hosted_namespace=self._settings.hosted_namespace,
            )
        )
        await self._set_reason(row, REASON_DEPLOYING, touch=True)  # the timeout counts from here
        log.info("hosted deploy started", hosted=slug, image=row["image"], attempt=row["attempt"])

    async def _mark_deployed(self, row: Any) -> None:
        server_id = row["server_id"]
        async with self._pool.acquire() as conn, conn.transaction():
            if not await self._still(conn, row):
                return
            await conn.execute(
                "UPDATE mcp_servers SET enabled = true, updated_at = now() WHERE id = $1", server_id
            )
            await conn.execute(
                "UPDATE hosted_deployments SET deploy_status = 'deployed', deploy_reason = NULL, "
                "deployed_at = now(), updated_at = now() WHERE server_id = $1",
                server_id,
            )
            await notify(conn, "catalog_changed", str(server_id))
        log.info("hosted server deployed", hosted=row["slug"])
        await self._drop_input(row["slug"])
        if self._on_ready is not None:
            await self._on_ready(server_id)

    # ---------------------------------------------------------------------------------------------- remove

    async def _removing(self, row: Any, jobs: dict[str, JobInfo]) -> None:
        slug, sid = row["slug"], str(row["server_id"])
        await self._disable(row)  # tools vanish before anything is torn down
        job = jobs.get(unhost_job_name(slug, sid, row["attempt"]))
        if job is None:
            for stale in jobs.values():  # a deploy still in flight is stopped first
                if stale.hosted == slug and stale.action == "deploy" and stale.state == "running":
                    await self._k8s.delete_job(stale.name)
            await self._drop_input(slug)
            await self._k8s.create_job(
                deployer_job(
                    name=unhost_job_name(slug, sid, row["attempt"]),
                    namespace=self._settings.k8s_namespace,
                    image=self._settings.deployer_image,
                    target=slug,
                    action="remove",
                    values_env=self._settings.store_values_env,
                    deadline_s=self._settings.hosted_remove_timeout_s,
                    hosted_namespace=self._settings.hosted_namespace,
                )
            )
            await self._set_reason(row, REASON_REMOVING, touch=True)
            log.info("hosted removal started", hosted=slug, attempt=row["attempt"])
        elif job.state == "succeeded":
            async with self._pool.acquire() as conn, conn.transaction():
                if not await self._still(conn, row):
                    return
                await conn.execute("DELETE FROM mcp_servers WHERE id = $1", row["server_id"])  # cascades
                await notify(conn, "catalog_changed", sid)
            log.info("hosted server removed", hosted=slug)
            if self._on_removed is not None:
                await self._on_removed(row["server_id"])
        elif job.state == "failed":
            logs = await self._k8s.job_logs(job.name)
            await self._fail(row, failure_reason(f"{REMOVAL_FAILED} ({job.reason})", logs))
        elif row["age_s"] > self._settings.hosted_remove_timeout_s + GRACE_S:
            await self._k8s.delete_job(job.name)
            await self._fail(row, f"{REMOVAL_FAILED}: timeout (no result after {int(row['age_s'])} s)")

    async def _disable(self, row: Any) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            changed = await conn.fetchval(
                "UPDATE mcp_servers SET enabled = false, updated_at = now() "
                "WHERE id = $1 AND enabled RETURNING id",
                row["server_id"],
            )
            if changed is None:
                return
            await notify(conn, "catalog_changed", str(row["server_id"]))
        if self._on_removed is not None:
            await self._on_removed(row["server_id"])

    # --------------------------------------------------------------------------------------------- helpers

    async def _still(self, conn: Any, row: Any) -> bool:
        """Lock the row and confirm nobody changed its status/attempt since this tick read it."""
        current = await conn.fetchrow(
            "SELECT deploy_status, attempt FROM hosted_deployments WHERE server_id = $1 FOR UPDATE",
            row["server_id"],
        )
        return current is not None and (current["deploy_status"], current["attempt"]) == (
            row["deploy_status"],
            row["attempt"],
        )

    async def _set_reason(self, row: Any, reason: str, *, touch: bool) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE hosted_deployments SET deploy_reason = $4, "
                "updated_at = CASE WHEN $5 THEN now() ELSE updated_at END "
                "WHERE server_id = $1 AND deploy_status = $2 AND attempt = $3",
                row["server_id"],
                row["deploy_status"],
                row["attempt"],
                reason,
                touch,
            )

    async def _fail(self, row: Any, reason: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE hosted_deployments SET deploy_status = 'failed', deploy_reason = $4, "
                "updated_at = now() WHERE server_id = $1 AND deploy_status = $2 AND attempt = $3",
                row["server_id"],
                row["deploy_status"],
                row["attempt"],
                reason[:REASON_MAX],
            )
        first = reason.splitlines()[0]
        log.warning("hosted server failed", hosted=row["slug"], phase=row["deploy_status"], reason=first)
        await self._drop_input(row["slug"])

    async def _drop_input(self, slug: str) -> None:
        try:
            await self._k8s.delete_secret(hosted_input_secret_name(slug))
        except K8sError as exc:  # leftover input is harmless (overwritten next attempt); never block on it
            log.warning("input secret cleanup failed", hosted=slug, status=exc.status)

"""Kubernetes access for the provisioner (D77, DESIGN §8.6, §13.2).

The gateway only touches its own namespace: it creates deployer Jobs and their input Secrets, reads Job
status and the last log lines of a failed Job. `JobApi` is the seam the reconciler depends on; `KubeJobApi`
implements it with the official (sync) client in worker threads, tests use an in-memory fake.
`deployer_job()` builds the Job manifest (pure, unit-tested).
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anyio

from wmg_common.logging import get_logger

log = get_logger(__name__)

Action = Literal["provision", "teardown"]
JobState = Literal["running", "succeeded", "failed"]

DEPLOYER_SA = "wmg-deployer"
DEPLOYER_UID = 65532
COMPONENT_LABEL = {"app.kubernetes.io/name": "deployer", "app.kubernetes.io/part-of": "wmg"}
STORE_LABEL = "wmg.dev/store"
ACTION_LABEL = "wmg.dev/action"
JOB_NAME_LABEL = "batch.kubernetes.io/job-name"
JOB_TTL_S = 3600
LOG_LINES = 20


@dataclass(frozen=True)
class JobInfo:
    name: str
    store: str
    action: str
    state: JobState
    reason: str | None = None  # Failed condition: "<reason>: <message>"


class K8sError(Exception):
    """A Kubernetes API call failed (status 0 = no HTTP answer)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class JobApi(Protocol):
    async def list_jobs(self) -> list[JobInfo]: ...
    async def create_job(self, manifest: dict[str, Any]) -> None: ...
    async def delete_job(self, name: str) -> None: ...
    async def put_secret(self, name: str, data: dict[str, str], labels: dict[str, str]) -> None: ...
    async def delete_secret(self, name: str) -> None: ...
    async def job_logs(self, name: str, lines: int = LOG_LINES) -> str: ...


# Job names are deterministic per store row (name + first 8 hex digits of its id) and attempt: a retried tick
# finds its Job again, and a recreated store never sees the old store's finished Jobs (kept 1 h by TTL).
def prov_job_name(store: str, store_id: str, attempt: int) -> str:
    return f"wmg-prov-{store}-{store_id.replace('-', '')[:8]}-{attempt}"


def del_job_name(store: str, store_id: str, attempt: int) -> str:
    return f"wmg-del-{store}-{store_id.replace('-', '')[:8]}-{attempt}"


def input_secret_name(store: str) -> str:
    return f"wmg-prov-{store}"


def deployer_job(
    *,
    name: str,
    namespace: str,
    image: str,
    store: str,
    action: Action,
    values_env: str,
    deadline_s: int,
) -> dict[str, Any]:
    """Job running `provision-store.sh <store>` / `teardown-store.sh <store>` as `wmg-deployer` (§13.2)."""
    labels = {**COMPONENT_LABEL, STORE_LABEL: store, ACTION_LABEL: action}
    script = "provision-store.sh" if action == "provision" else "teardown-store.sh"
    mounts: list[dict[str, Any]] = [{"name": "tmp", "mountPath": "/tmp"}]  # noqa: S108
    volumes: list[dict[str, Any]] = [{"name": "tmp", "emptyDir": {"sizeLimit": "256Mi"}}]
    if action == "provision":
        mounts.append({"name": "input", "mountPath": "/input", "readOnly": True})
        volumes.append({"name": "input", "secret": {"secretName": input_secret_name(store)}})
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "backoffLimit": 1,
            # a retry pod starts only after the failed one is gone: never two Helm runs on one release
            "podReplacementPolicy": "Failed",
            "activeDeadlineSeconds": deadline_s,
            "ttlSecondsAfterFinished": JOB_TTL_S,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "serviceAccountName": DEPLOYER_SA,
                    "restartPolicy": "Never",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": DEPLOYER_UID,
                        "runAsGroup": DEPLOYER_UID,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "deployer",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "args": [script, store],
                            "env": [{"name": "VALUES_ENV", "value": values_env}],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": mounts,
                        }
                    ],
                    "volumes": volumes,
                },
            },
        },
    }


def job_state(status: Any) -> tuple[JobState, str | None]:
    """Final state from the Job conditions (Complete / Failed); anything else is still running."""
    for cond in (status.conditions if status else None) or []:
        if cond.status != "True":
            continue
        if cond.type == "Complete":
            return "succeeded", None
        if cond.type == "Failed":
            return "failed", f"{cond.reason or 'Failed'}: {cond.message or 'job failed'}"
    return "running", None


class KubeJobApi:
    """The official client is synchronous: every call runs in a worker thread (D77)."""

    def __init__(self, namespace: str) -> None:
        self._ns = namespace
        self._batch: Any = None
        self._core: Any = None

    def _clients(self) -> tuple[Any, Any]:
        if self._batch is None:
            from kubernetes import client, config

            config.load_incluster_config()
            self._batch, self._core = client.BatchV1Api(), client.CoreV1Api()
        return self._batch, self._core

    async def _run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        from kubernetes.client.exceptions import ApiException

        def call() -> Any:
            return fn(*args, **kwargs)

        try:
            return await anyio.to_thread.run_sync(call)
        except ApiException as exc:
            raise K8sError(exc.status or 0, str(exc.reason or "Kubernetes API error")) from None
        except OSError as exc:
            raise K8sError(0, type(exc).__name__) from None

    async def list_jobs(self) -> list[JobInfo]:
        batch, _ = self._clients()
        selector = ",".join(f"{k}={v}" for k, v in COMPONENT_LABEL.items())
        result = await self._run(batch.list_namespaced_job, self._ns, label_selector=selector)
        jobs: list[JobInfo] = []
        for job in result.items:
            labels = job.metadata.labels or {}
            state, reason = job_state(job.status)
            jobs.append(
                JobInfo(
                    job.metadata.name,
                    labels.get(STORE_LABEL, ""),
                    labels.get(ACTION_LABEL, ""),
                    state,
                    reason,
                )
            )
        return jobs

    async def create_job(self, manifest: dict[str, Any]) -> None:
        batch, _ = self._clients()
        try:
            await self._run(batch.create_namespaced_job, self._ns, manifest)
        except K8sError as exc:
            if exc.status != 409:  # already created (e.g. by a previous leader): fine
                raise

    async def delete_job(self, name: str) -> None:
        batch, _ = self._clients()
        try:
            await self._run(batch.delete_namespaced_job, name, self._ns, propagation_policy="Background")
        except K8sError as exc:
            if exc.status != 404:
                raise

    async def put_secret(self, name: str, data: dict[str, str], labels: dict[str, str]) -> None:
        _, core = self._clients()
        body = {"metadata": {"name": name, "labels": labels}, "type": "Opaque", "stringData": data}
        try:
            await self._run(core.create_namespaced_secret, self._ns, body)
        except K8sError as exc:
            if exc.status != 409:
                raise
            await self._run(core.replace_namespaced_secret, name, self._ns, body)

    async def delete_secret(self, name: str) -> None:
        _, core = self._clients()
        try:
            await self._run(core.delete_namespaced_secret, name, self._ns)
        except K8sError as exc:
            if exc.status != 404:
                raise

    async def job_logs(self, name: str, lines: int = LOG_LINES) -> str:
        """Last log lines of the Job's newest pod (best effort: '' if none)."""
        _, core = self._clients()
        try:
            pods = await self._run(
                core.list_namespaced_pod, self._ns, label_selector=f"{JOB_NAME_LABEL}={name}"
            )
            if not pods.items:
                return ""
            newest = max(pods.items, key=lambda p: p.metadata.creation_timestamp)
            text = await self._run(
                core.read_namespaced_pod_log, newest.metadata.name, self._ns, tail_lines=lines
            )
            return str(text or "")
        except K8sError as exc:
            log.warning("job logs unavailable", job=name, status=exc.status)
            return ""

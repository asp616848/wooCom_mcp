"""Shared per-process objects, created by `create_app` and completed (DB pool) by its lifespan.

`check_url` (SSRF policy for the API) and the manager's transport factory are fields so integration tests can
reach fixture servers on loopback; production always uses the defaults. `k8s` is the provisioner's Kubernetes
seam (None when provisioning is disabled; tests set a fake).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.subscriptions import InMemorySubscriptionBus

from wmg_gateway.audit.audit import AuditWriter
from wmg_gateway.catalog.catalog import CatalogHolder
from wmg_gateway.provisioner.k8s import JobApi, KubeJobApi
from wmg_gateway.security import ssrf
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.settings import Settings
from wmg_gateway.upstream.manager import UpstreamManager


@dataclass
class Runtime:
    settings: Settings
    cipher: CredentialCipher
    manager: UpstreamManager
    catalog: CatalogHolder = field(default_factory=CatalogHolder)
    bus: InMemorySubscriptionBus = field(default_factory=InMemorySubscriptionBus)
    audit: AuditWriter = field(default_factory=lambda: AuditWriter(None))
    pool: Any = None
    check_url: Callable[[str], Awaitable[object]] = ssrf.check_url
    k8s: JobApi | None = None

    @classmethod
    def create(cls, settings: Settings) -> Runtime:
        cipher = CredentialCipher(settings.encryption_key())
        return cls(
            settings=settings,
            cipher=cipher,
            manager=UpstreamManager(cipher, settings.upstream_max_inflight),
            k8s=KubeJobApi(settings.k8s_namespace) if settings.provisioning_enabled() else None,
        )

    def attach_pool(self, pool: Any) -> None:
        self.pool = pool
        self.audit.pool = pool

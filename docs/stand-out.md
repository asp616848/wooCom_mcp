# Bonus and stand-out items

> **Built in depth: Security hardening (SO-6), Scaling (SO-7) and OAuth (SO-3)**, each with attack and failure cases
> tested on a live cluster. The multi-store bonus and the other six items are complete and tested too.

`GW` = `services/gateway/src/wmg_gateway`. Every item runs locally (k3d) and on the VPS (k3s, HTTPS).

| Item | Status | Tested by |
|---|---|---|
| Bonus: multi-store provisioning | ✅ all 7 requirements | `make e2e-stores` (21 checks, incl. a pod killed mid-install) |
| SO-1 Production-like VPS | ✅ Oracle A1 arm64, k3s, cert-manager | `make e2e` over HTTPS on the VPS (55/55) |
| SO-2 Tool permissions | ✅ | `make e2e`, `tests/integration/test_api_permissions.py` |
| **SO-3 OAuth on the gateway** | ✅ **depth** | `make e2e-oauth`, 22 integration tests with the SDK's own OAuth client |
| SO-4 Many tools | ✅ | `unit/test_search.py`, 60-tool fixture |
| SO-5 Hosted MCP servers | ✅ | `make e2e-hosted` (11 checks) |
| **SO-6 Security hardening** | ✅ **depth** | `make test-k8s`, `unit/test_ssrf.py`, admission dry-runs in `e2e-stores` / `e2e-hosted` |
| **SO-7 Scaling** | ✅ **depth** | `make e2e-scale` (leader SIGKILL under load), failover integration test (18 clean runs) |
| SO-8 Observability | ✅ | `integration/test_mcp_gateway.py` (SO8-01: metrics and Insights match the audit log) |
| SO-9 Upgrades and rollback | ✅ | `make e2e-upgrades` (gateway, pincode, store, with restore) |

---

## In depth

### SO-6 Security hardening
- **SSRF guard on every request, not just at registration** (`GW/security/ssrf.py`, `GW/upstream/transport.py`).
  In-cluster URLs must be in an upstream namespace; anything else must be public HTTPS. The hostname is resolved,
  every address must be global, and the connection goes to the **validated IP** with the original `Host` and SNI. A
  DNS-rebinding second lookup can't redirect it. Blocks decimal and hex IPs (`2130706433`, `0x7f.1`), IPv4-mapped IPv6,
  CGNAT, cloud metadata (`169.254.169.254`), `kubernetes.default`, userinfo and non-HTTP schemes (table:
  `unit/test_ssrf.py`).
- **Default-deny NetworkPolicies in every namespace**, then per-workload allow-lists: woo-mcp accepts only the
  gateway, MariaDB only WordPress, Postgres only the gateway, gst and pincode have no egress. Proven with long-lived
  probe pods and positive controls, because k3s programs a new pod's IP a few seconds late and one-shot pods gave
  false passes (`tests/e2e/test_k8s.py`).
- **Two locks on the deployer** that can create namespaces: RBAC binds it per store namespace only, so it can never
  read `mcp-platform` Secrets. A ValidatingAdmissionPolicy denies any write outside `store-<name>` and any change to
  the `mcp-hosted` guardrails. `kubectl auth can-i` only checks RBAC, so the policy is tested with server-side
  dry-run writes as the deployer (`charts/mcp-platform/templates/admission-policy.yaml`, `deployer-rbac.yaml`).
- **Credentials encrypted at rest**: AES-256-GCM with the server row as associated data, so a ciphertext copied to
  another row fails to decrypt (`GW/security/crypto.py`). API keys are stored as HMAC with a pepper; sessions,
  invites and OAuth tokens only as hashes.
- **Least privilege at runtime**: PSA `restricted` on the platform, upstream and hosted namespaces. Non-root,
  read-only root filesystem, no capabilities, seccomp. MariaDB runs as uid 999. The gateway's Role is namespaced:
  Jobs, Secrets and pod logs only.

### SO-7 Scaling
- **Replicas share nothing in memory that matters** (`GW/leader.py`, `GW/events/bus.py`, `GW/ratelimit/ratelimit.py`).
  The tool catalog reloads on Postgres `LISTEN/NOTIFY`. Rate-limit counters are rows (one per key per minute), so
  120 calls/min holds across replicas. Leadership is a Postgres advisory lock. Postgres is the only stateful
  dependency, with no Redis and no etcd.
- **Failover without a split brain**: a dead leader's lock goes with its connection. A leader that vanishes without
  closing it is dropped by TCP keepalives (~8 s), and followers retry every 2 s. A leader whose connection drops stops
  its duties **the instant** asyncpg reports it (termination listener). The first version only noticed at its next
  ping, which allowed two leaders for up to 2 s; the gate caught that race.
- **Measured**: two replicas behind Traefik at 50 rps, leader killed gracefully and with SIGKILL. Result: 0 errors,
  0 timeouts, 0 body mismatches in 2,000 requests, locally and on the VPS. In-cluster `tools/list` p95 ≈ 10 ms,
  gateway overhead on `tools/call` p95 ≈ 2–3 ms (`tests/perf/incluster.py`). The PodDisruptionBudget refuses a second
  eviction.
- **Edge cases handled**: a client hanging up mid-request logs one info line instead of two tracebacks. The rate-limit
  table also holds login lockouts, so its cleanup keeps one hour, not ten minutes, so attackers aren't unlocked early.

### SO-3 OAuth on the gateway
- **The gateway is its own MCP authorization server** (`GW/oauth/`), alongside API keys, on `/mcp` and `/mcp/search`.
  It has Dynamic Client Registration (public clients only, https or localhost redirects) and PKCE S256 only.
  `/authorize` parks the request in Postgres and redirects to the dashboard's consent page. A signed-in user must
  approve every time.
- **Token lifecycle**: single-use 10-minute codes, 1-hour `mgo_` access tokens and rotating 30-day refresh tokens, all
  stored as hashes. **A replayed code or a reused refresh token revokes everything issued from it.** The verifier
  reads the user's role and status on every request, so disabling a user or revoking the app under *Connected apps*
  ends access on the next call. Calls are audited as `oauth` and rate-limited per user and app.
- **Edge cases a real client run exposed**, each handled in `GW/oauth/server.py`:
  - the SDK's issuer check rejects `http://gateway.localhost`;
  - a trailing-slash issuer mismatch;
  - the metadata didn't advertise public-client auth;
  - `/revoke` demanded a `client_secret` that public clients don't have.
- **Abuse limits**: 200 registered apps, 500 pending requests, unapproved apps deleted after 7 days.

---

## Bonus: multi-store provisioning
- **The API only records intent**: a `stores` row and 202. A **reconciler on the leader** (every 5 s) drives Kubernetes
  Jobs with deterministic names (`wmg-prov-<name>-<id8>-<attempt>`). A gateway restart loses nothing, and any new
  leader resumes from the database and the Job list (`GW/provisioner/`).
- **The deployer Job** (`deploy/deployer/`) runs `helm upgrade --install --wait --rollback-on-failure` of the same
  `woo-store` chart into `store-<name>`, with its own MariaDB, PVCs, Secrets and Ingress host. It repairs a release that
  a killed pod left half-done, then runs `helm test`: a signed WooCommerce call through the store's own woo-mcp.
- **Then it registers `woo-<name>`** as an MCP server automatically, so tools appear as `woo-paris__list_orders`.
  Deleting a store removes the registration first (tools vanish within a second), then the namespace with its volumes
  and Secrets.
- **Guardrails**: ResourceQuota + LimitRange per namespace, 2 stores per user and 4 in total (checked under one DB
  lock), at most 3 concurrent installs, Job and reconciler deadlines. A failed store shows the Job's reason and last
  log lines, and *Retry* starts attempt + 1. Updates are compare-and-set on (status, attempt), so a delete during
  provisioning is never overwritten.
- **Isolation**: per-store NetworkPolicies. `paris` → `tokyo` database probes are blocked in the test.

## The other stand-out items
- **SO-1 VPS**: the same charts with `values-prod.yaml` (hosts, TLS, `existingSecret`, storage, 2 replicas). The
  host is Oracle Always Free A1 arm64 behind a cost-guard quota. Images are multi-arch on GHCR from CI. The Kubernetes
  API is reachable only through an SSH tunnel. Runbook: [vps-deploy.md](vps-deploy.md).
- **SO-2 Tool permissions**: `tool_policies` rows in every replica's in-memory catalog. Admin-only tools are hidden from
  members' `tools/list` and rejected with `-32010` if called by name. Changes reach every replica within a second via
  NOTIFY. WooCommerce write tools start admin-only (`GW/catalog/catalog.py`, `GW/mcp/call_tool.py`).
- **SO-4 Many tools**: `/mcp/search` is a second MCP endpoint with three tools. `search_tools` ranks only the tools
  the caller may use, by name, description and input schema. `call_tool` runs one through the normal pipeline,
  audited as the real tool. The chat agent switches to it above 40 tools (`GW/mcp/search.py`).
- **SO-5 Hosted servers**: reuses the store machinery. An image (allow-listed registry, explicit tag) becomes a
  disabled server row plus a deployer Job that installs `charts/mcp-server` into `mcp-hosted` with a generated
  credential. The namespace is PSA restricted, with no service-account token, ingress only from the gateway, no egress
  unless allowed, and a quota (`GW/provisioner/hosted.py`).
- **SO-8 Observability**: Prometheus counters and histograms per server and tool at `/metrics`. The **Insights** page
  shows calls, error rate, p50/p95 and top failure reasons, computed from the audit log so it always matches the
  trail. Every upstream failure gets a code (`TIMEOUT`, `CONNECTION_REFUSED`, `DNS_FAILURE`, `TLS_ERROR`,
  `AUTH_REJECTED`, `HTTP_<status>`, `PROTOCOL_ERROR`, `SSRF_BLOCKED`) and a plain message, shown on the server's page
  (`GW/upstream/manager.py`).
- **SO-9 Upgrades and rollback**: every component is a Helm release and images are tagged by git sha. Gateway
  migrations are additive only, so an older image runs on a newer schema. The store's setup Job also runs
  `post-rollback`. `make store-upgrade` backs up first and `make restore-store` restores. Runbook:
  [upgrades-rollback.md](upgrades-rollback.md).

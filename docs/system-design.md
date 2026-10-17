# System design and tradeoffs

Draft (core complete). Covers the architecture, how tools are aggregated and routed, how failures are contained, and
what changes between the laptop (k3d) and a VPS (k3s).

## 1. Goals that shaped the design
1. **One door.** Every MCP call, from an external client or from the chat agent, passes the same pipeline:
   authenticate → check permission → route → call upstream → audit. Nothing else can reach an upstream server; the
   network enforces that, not just the code.
2. **Credentials flow inward only.** Upstream credentials are decrypted inside the gateway process at call time and
   never leave it: not to browsers, MCP clients, logs or audit rows.
3. **A dead or slow upstream degrades only itself.** Listing tools never waits on an upstream; each call has its own
   timeout and concurrency cap.
4. **Same charts locally and in production**; only Helm values differ.
5. **Simple enough to explain.** Postgres is the only stateful dependency of the platform: no Redis, no message
   broker, no service mesh, no operator framework.

## 2. Components
| Component | Namespace | What it does |
|---|---|---|
| WordPress + WooCommerce, MariaDB | `store-main` | the store; public storefront via Ingress `shop.localhost` |
| setup Job (Helm hook) | `store-main` | idempotent WP-CLI script: WooCommerce, INR, COD, free shipping, sample products, REST key |
| `woo-mcp` | `store-main` | MCP server over the WooCommerce REST API (OAuth 1.0a signed); internal only |
| `gst`, `pincode` | `mcp-upstreams` | stateless MCP servers with bundled data; bearer token / `X-Api-Key` auth; no egress at all |
| gateway | `mcp-platform` | `/mcp` for MCP clients, `/api` for the dashboard, health checks, chat agent |
| dashboard | `mcp-platform` | static React app (nginx) |
| Postgres | `mcp-platform` | users, sessions, keys, servers, tool catalog, policies, audit log |

All Python services are built from one uv workspace and share `wmg_common` (structured logging with secret redaction,
settings that fail fast, a static-credential auth middleware for the upstream servers).

## 3. How a tool call travels
Example: Ritika's MCP client calls `woo-main__list_orders {"period": "today"}`.
1. **Ingress.** `gateway.localhost/mcp` → Traefik → gateway pod (Streamable HTTP, JSON responses, stateless).
2. **Authentication.** `Authorization: Bearer mgw_…`: the 8-character prefix finds the key row, an HMAC-SHA256
   (server-side pepper) of the whole key is compared in constant time, and the key and its user must be active. The
   check runs on every request, so revoking a key or disabling a user takes effect on the next call.
3. **Lookup.** The exposed name is resolved in the in-memory catalog → server `woo-main`, original tool `list_orders`.
   Unknown, disabled or hidden tools → JSON-RPC `-32602`; an admin-only tool called by a member → `-32010`.
4. **Upstream call.** The gateway keeps one HTTP client per upstream with that server's credential as a default header
   (decrypted from AES-256-GCM at rest) and calls the original tool name with the server's timeout (default 20 s)
   under a per-upstream concurrency cap (16 per replica). The client's own token is never forwarded.
5. **woo-mcp → WordPress.** woo-mcp signs a REST request with the store's consumer key and secret, resolves "today"
   in the store's timezone, and returns structured content.
6. **Back.** The gateway caps the result size (1 MB), returns it unchanged, and writes an audit row: user, credential
   type and id, server, tool, outcome, duration, a redacted argument summary, client name.

## 4. Aggregation and routing
- **Naming: always prefixed.** Every tool is exposed as `<serverSlug>__<toolName>` (sanitised to `[A-Za-z0-9_-]`,
  max 64 characters, with a short hash suffix only if sanitising or truncating makes two names collide). Slugs can't
  contain `_` or change after creation, so `__` is an unambiguous separator and names never shift when another server
  is added. Two servers can both have an `echo` tool. Descriptions are prefixed with `[slug]`, stripped of control
  characters and capped at 1024 characters.
- **Catalog in memory.** `tools/list` is answered from an immutable in-memory snapshot of the tools of every
  enabled, connected server, filtered by the caller's role. It never calls an upstream, so a slow server can't slow
  listing down. The response carries a 10-second private cache hint.
- **Keeping it fresh.** The leader replica health-checks every enabled upstream every 10 s (`tools/list`, 3 s
  timeout). When a server's tools or status change, it writes Postgres and sends `NOTIFY wmg_events`; every replica
  `LISTEN`s, reloads its snapshot and, if the visible tool set changed, sends `notifications/tools/list_changed` to
  subscribed clients. Admin actions (add, edit, disable, delete) use the same path, so a disabled server's tools vanish
  for everyone within about a second.
- **Leader election.** A Postgres advisory lock (`pg_try_advisory_lock`) on a dedicated connection; whichever replica
  holds it runs the health loop. If it dies, the lock is released with its connection and another replica takes over.
- **Two protocol eras.** MCP clients and servers may speak the 2026-07-28 protocol (`server/discover`, stateless) or
  the older initialise-handshake protocol. The gateway serves both from one endpoint, probes each upstream once to learn
  its era, and talks to it in that era. A legacy-only upstream works behind a modern client and the other way round.

## 5. Failure handling
| Failure | Behaviour |
|---|---|
| Upstream down / connection refused | health check marks it `unreachable` within one tick (≤ ~13 s); its tools leave the catalog; other servers unaffected; recovers on the next successful check |
| Upstream slow | the call fails after that server's `timeout_ms` with a clear tool error (`isError`); the slot is freed; other servers unaffected |
| Upstream overloaded | at most 16 in-flight calls per upstream per replica; extra calls wait and still respect the timeout |
| Connection reset before any response | retried once, only for `tools/list` and tools annotated read-only or idempotent; writes are never retried |
| Oversized result | replaced by an error (> 1 MB) instead of exhausting gateway memory |
| Gateway pod dies | stateless; Kubernetes replaces it; in production a second replica keeps serving and takes over leadership |
| Postgres down | readiness fails (`/readyz`), so Traefik stops sending traffic; liveness stays green so the pod isn't restarted in a loop |
| Store down | woo-mcp's probes don't touch the store, so woo-mcp stays up and returns a tool error; the gateway marks nothing dead that isn't |
| LLM rate limit | the chat shows "free-tier rate limit reached"; MCP clients are unaffected |

## 6. Chat agent
The agent runs inside the gateway. For each chat turn it mints a 5-minute JWT for the signed-in user (HS256,
audience `wmg-mcp`, `via=chat-agent`) and connects to the gateway's own `/mcp` over loopback with an ordinary MCP
client. It therefore sees exactly the user's tools and goes through the same permission checks and audit log, so
there is no second, privileged path to the upstreams. Tool schemas are trimmed to the JSON-Schema subset Gemini
accepts. The loop is bounded at 8 LLM calls and 12 tool calls per turn, and progress is streamed to the browser as
Server-Sent Events. The LLM is any OpenAI-compatible endpoint (Gemini by default) chosen through Helm values.

## 7. Data
Postgres tables: `users`, `sessions` (only SHA-256 of the cookie), `invites` (only SHA-256 of the token), `api_keys`
(prefix + HMAC), `mcp_servers` (URL, auth type, encrypted credential, status, protocol era), `server_tools`
(catalog), `tool_policies` (admin-only flags), `audit_log`. Migrations are plain SQL run at startup under an advisory
lock. WordPress keeps its own data in MariaDB and on a PVC; both survive pod restarts and `helm upgrade`.

## 8. Kubernetes layout
- **Namespaces as trust zones**: `store-main` (PSA baseline: Apache needs root to bind :80), `mcp-upstreams` and
  `mcp-platform` (PSA restricted).
- **Ingress**: three hosts only (shop, dashboard, gateway). woo-mcp, gst, pincode, Postgres and MariaDB have no route.
- **NetworkPolicies**: default-deny in every namespace, then explicit allow-lists, e.g. woo-mcp accepts only the
  gateway; MariaDB only WordPress and the setup Job; Postgres only the gateway; gst and pincode have no egress.
- **Every workload** has requests, limits, liveness and readiness probes (`make verify-k8s`). Python images run as uid
  10001 with a read-only root filesystem and no capabilities.
- **Secrets** are generated by the charts on first install and kept on upgrade (`lookup` + `resource-policy: keep`);
  in production they are created beforehand and referenced by name.

## 9. Local vs production
| Concern | Local (k3d) | Production-like (VPS, k3s) |
|---|---|---|
| DNS | `*.localhost`, resolved by the OS/browser | real names (e.g. `dashboard.<ip>.sslip.io`) set in values |
| Ingress / TLS | Traefik, HTTP | Traefik + cert-manager (Let's Encrypt), HTTPS, `Secure` cookies |
| Storage | k3d `local-path` default class | `local-path` on the VPS disk (explicit `storageClass`); backups by script |
| Secrets | generated by the charts | created beforehand by a script, referenced via `existingSecret` |
| Replicas | 1 gateway | 2 gateway replicas + PodDisruptionBudget |
| Images | built locally and imported into k3d | pulled from a registry, multi-arch |
| Kubernetes API egress | looked up by `make platform` on every install | the same lookup, over the SSH tunnel |
| Bonus stores | `<name>.shop.localhost`, HTTP | `<name>.shop.<ip>.sslip.io`, one Let's Encrypt certificate each |
Upgrades are `helm upgrade` with a new image tag; rollback is `helm rollback <release> <revision>`.

## 10. Tradeoffs
| Choice | Why | Cost |
|---|---|---|
| Postgres LISTEN/NOTIFY + advisory lock instead of Redis/etcd | one stateful dependency; enough for a few replicas | not built for high fan-out; reconnect logic needed (done: backoff + reload on reconnect) |
| Catalog served from memory, refreshed by polling | listing is fast and immune to slow upstreams | up to one health interval (10 s) before a silently broken upstream is noticed |
| Always-prefixed tool names | stable, collision-free, self-describing | longer names than the originals |
| Stateless Streamable HTTP on `/mcp` | any replica can serve any request; easy scaling | no server-initiated requests outside a live subscription |
| Shared upstream credentials held by the gateway | members never handle upstream secrets; one place to rotate | upstreams see the gateway, not the person; attribution lives in the gateway's audit log |
| One process for API, MCP and chat | fewer moving parts to deploy and explain | a heavy chat load shares CPU with MCP routing (scale replicas) |
| WordPress via the official Apache image | well known, simple | runs as root (namespace PSA is baseline instead of restricted) |

## 11. Bonus: store provisioning
Anyone signed in can create a store (2 per user, 4 in total). The API only records the request (`stores` row,
status `provisioning`, a fresh MCP token encrypted at rest) and answers 202; the work happens in Kubernetes:

1. **Reconciler** (leader replica, every 5 s): for each store in flight it compares the database row with the
   Job of the same deterministic name (`wmg-prov-<name>-<id8>-<attempt>`). No Job yet → create an input Secret
   (per-store values + MCP token) and the Job (at most 3 at once, the rest wait as `queued`). Job succeeded →
   register `woo-<name>` as an MCP server (bearer token, admin-only write tools like the main store), mark `ready`,
   probe it so its tools appear at once. Job failed or past its deadline → `failed` with the Job's reason and the
   last log lines; *Retry* starts attempt + 1.
2. **Deployer Job** (`wmg/deployer`: helm + kubectl + the chart, checksummed): creates the namespace, binds itself
   to the store role there, creates the store's credentials once, repairs a release a killed pod left half-done,
   then `helm upgrade --install … --wait --rollback-on-failure` and `helm test` (a signed WooCommerce call through
   the store's own woo-mcp).
3. **Delete**: the MCP registration goes first (tools vanish within a second), then a teardown Job uninstalls the
   release and deletes the namespace (volumes and Secrets with it), then the row.

Why Jobs and not the gateway itself: a Job keeps running if the gateway restarts, Kubernetes retries a killed pod,
and the gateway needs no rights outside its own namespace. Any replica that becomes leader resumes from the database
and the Job list. Every database update checks the (status, attempt) it read, so a delete during provisioning is
never overwritten.

Guardrails: per-namespace ResourceQuota + LimitRange (BR-6; bonus stores use a smaller profile so four fit next to
the main store), provisioning timeout at Job and reconciler level, per-user and total limits enforced under one
database lock. Isolation: each store's NetworkPolicies only let its woo-mcp reach its own WordPress, so `paris` can't
reach `tokyo`'s database (tested). The deployer is confined twice: RBAC gives it no namespaced rights outside the
store namespaces it binds itself into, and a ValidatingAdmissionPolicy rejects any write it makes outside
`store-<name>` (never `store-main`).

## 12. Stand-out features
- **Tool permissions (SO-2)**: `tool_policies` rows (admin-only on/off per tool) live in the in-memory catalog of
  every replica; a change NOTIFYs all of them, so `tools/list` and `tools/call` enforce it within a second. Woo write
  tools start admin-only.
- **Observability (SO-8)**: Prometheus counters and histograms per server (`wmg_tool_calls_total`,
  `wmg_tool_call_duration_seconds`, `wmg_upstream_up`, `wmg_upstream_tools`, `wmg_tools_list_duration_seconds`) and an
  Insights page computed from the audit log, so its numbers always match the audit trail.
- **Many tools (SO-4)**: `/mcp/search` is a second MCP endpoint with three fixed tools. `search_tools` ranks the tools
  the caller may use by the words of the query (name, title, description, input schema); `call_tool` runs one through
  the normal path, audited as the real tool. The chat agent uses it when a user can see more than 40 tools.
- **Hosted servers (SO-5)**: the store machinery reused for any MCP server image: an admin request becomes a disabled
  server row plus a deploy request; the leader's reconciler runs a deployer Job that installs the generic
  `mcp-server` chart into `mcp-hosted` with a credential the gateway generated, then enables the server. The
  namespace has its own quota and limits, and the admission policy keeps the deployer from touching them.


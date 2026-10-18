# Woo MCP Gateway

One authenticated MCP endpoint for a team, in front of many MCP servers, running on Kubernetes.

- A **WooCommerce store** (WordPress + MariaDB) with its own MCP server (`woo-mcp`) that can list and create products
  and read orders.
- Two more MCP servers: **gst** (Indian GST calculator, bearer auth) and **pincode** (delivery checker, API-key header).
- A **gateway** that aggregates every server's tools behind `http://gateway.localhost/mcp`, authenticates people with
  their own API keys, keeps upstream credentials to itself, and audits every call.
- A **dashboard** (`http://dashboard.localhost`) to register servers, invite teammates, issue keys, read the audit log
  and **chat** with an LLM agent that uses the same tools as the signed-in user.

Design notes: [docs/system-design.md](docs/system-design.md) · Threat model: [docs/threat-model.md](docs/threat-model.md)

```
 browser ──► dashboard.localhost ──► dashboard (React)            ┌──► store-main / woo-mcp ──► WordPress ──► MariaDB
                                 └─► /api ─┐                       │
 MCP client ──► gateway.localhost/mcp ─────┴─► gateway ────────────┼──► mcp-upstreams / gst
                                                 │  ▲ chat agent    └──► mcp-upstreams / pincode
 shopper ──► shop.localhost ──► WordPress        ▼  (loopback, as the user)
                                              Postgres
```

## Local setup (k3d)

### Prerequisites (macOS or Linux)
| Tool | Version used |
|---|---|
| Docker (Desktop or Engine) | 29.8 |
| k3d | 5.9.0 |
| kubectl | 1.37 |
| Helm | 4.3.0 |
| uv (Python 3.14 is fetched by uv) | 0.11+ |
| Node.js (dashboard toolchain, tests) | 24+ |
| jq (`make verify-k8s`) | 1.7 |

Give Docker at least 4 CPUs and 6 GB of memory.

### Start everything
```bash
git clone https://github.com/asp616848/wooCom_mcp.git && cd wooCom_mcp
cp .env.example .env          # optional: put a Gemini API key in GEMINI_API_KEY for the chat agent
make deps                     # Python workspace + dashboard packages
make up                       # cluster, images, store, MCP servers, platform (about 10 minutes the first time)
```
`make up` creates the k3d cluster `woo-mcp` (ports 80/443 on your machine), builds the images, imports them into the
cluster and installs four Helm releases: `main` (store), `gst`, `pincode` and `platform`.

### URLs and first sign-in
| What | URL | Sign in |
|---|---|---|
| Storefront | http://shop.localhost | — |
| WordPress admin | http://shop.localhost/wp-admin | user `admin`, password: `make store-admin-password` |
| Dashboard | http://dashboard.localhost | `admin@example.com`, password: `make admin-password` |
| MCP endpoint | http://gateway.localhost/mcp | `Authorization: Bearer <API key from the dashboard>` |

The passwords are generated on first install and kept in Kubernetes Secrets; the commands print them to your terminal.

### Local domains
All three hosts are subdomains of `localhost`. Browsers, curl and macOS resolve `*.localhost` to 127.0.0.1 by
themselves (RFC 6761), so no `/etc/hosts` edits are needed; k3d forwards ports 80/443 to the cluster's Traefik
Ingress, which routes by host name. If your system resolver doesn't (some Linux setups without systemd-resolved),
add one line to `/etc/hosts`:
```
127.0.0.1 shop.localhost dashboard.localhost gateway.localhost
```
Inside the cluster nothing depends on these names: services talk to each other through cluster DNS
(`main-woo-mcp.store-main.svc`), and WordPress is told its public address through Helm values.

## Try it

### Place an order
1. Open http://shop.localhost, add a product to the cart, go to checkout.
2. Use any address (shipping is free), choose **Cash on delivery**, place the order.
3. The order appears in WordPress admin → WooCommerce → Orders, and to MCP clients through `woo-main__list_orders`.

### Invite a teammate and connect an MCP client
1. Dashboard → **Members** → *Invite*: copy the one-time link and open it in a private window to set a name and
   password.
2. As that user: **API keys** → *Create key*. The key is shown once, together with ready-to-paste snippets.
3. Connect a client with only the URL and the key, for example Claude Code:
   ```bash
   claude mcp add --transport http woo-gateway http://gateway.localhost/mcp --header "Authorization: Bearer <YOUR_API_KEY>"
   ```
   or the MCP Inspector: `npx @modelcontextprotocol/inspector@2.10.1` → Streamable HTTP → the URL + the header.
4. The client sees every enabled server's tools, prefixed by server: `woo-main__list_orders`, `gst__calculate_gst`,
   `pincode__check_delivery`, … Members don't see admin-only write tools such as `woo-main__create_product`.

### Chat
Needs `GEMINI_API_KEY` in `.env` (then `make secrets-llm` and `kubectl rollout restart deploy/platform-gateway -n
mcp-platform` if the platform is already installed). Dashboard → **Chat**, then for example:
- "Create a product named 'Brass Desk Lamp' priced 1299" (admins)
- "What orders came in today?"
- "Where is today's latest order going, and when will it be delivered?"

Each tool call shows as a card (name, arguments, result, duration) before the answer, and is recorded in the audit log
as the signed-in user with credential type `chat_agent`.

### Things to try as an admin
- **Servers** → disable `gst`: its tools disappear from every client within a second.
- `kubectl scale deploy/pincode -n mcp-upstreams --replicas=0`: pincode turns *Unreachable* within ~10 s; the other
  servers keep working. Scale back to 1 to recover.
- Add a server with URL `http://169.254.169.254/`: rejected by the SSRF guard.
- **Audit log**: every call with user, tool, outcome and duration.

### Create more stores (bonus)
**Stores** → *Create store* → e.g. `paris`. The gateway runs a deployer Job that installs the same `woo-store` chart into
namespace `store-paris` (own database, volumes, Secrets, NetworkPolicies, quota) and registers its MCP server as
`woo-paris`; the store opens at http://paris.shop.localhost and its tools appear as `woo-paris__*` in every client
(about 3–6 minutes). Each user may have 2 stores, 4 in total. Deleting a store removes its tools first, then the
namespace and its data. Ask the chat to *"Compare today's orders across paris and tokyo"* once both have an order.
How it works: [docs/system-design.md](docs/system-design.md#11-bonus-store-provisioning).

### Stand-out features (admin)
- **Tool permissions**: on a server's page, switch any tool to *Admins only*; members stop seeing it and their calls are
  rejected, on every gateway replica within a second.
- **Insights**: calls, error rate, p50/p95 latency per server and tool, and the top failure reasons (from the audit
  log); raw Prometheus counters at the gateway's `/metrics`.
- **Search mode**: `http://gateway.localhost/mcp/search` offers three tools (`search_tools`, `call_tool`,
  `list_servers`) instead of every tool; the chat switches to it by itself above 40 tools.
- **Hosted servers**: **Hosted** → *Deploy server* runs an MCP server from a container image (`wmg/`, `ghcr.io/` or
  `docker.io/`) in the `mcp-hosted` namespace with its own credential, quota and NetworkPolicy, and registers it.
- **Rate limits**: every API key may make 120 tool calls a minute, counted across all gateway replicas; over it,
  calls are refused with "retry in N s". Admins can give a key its own limit on the **API keys** page.
- **Two gateways**: `make platform SCALE=1` runs two replicas, as in production. Kill either one, the leader too,
  during a load test (`make e2e-scale`) and no request fails; the other takes over leadership within seconds.
- **Upgrades and rollback**: `make store-upgrade` backs up the store first; any release goes back with `helm
  rollback`, and `make restore-store` brings store data back. Runbook: [docs/upgrades-rollback.md](docs/upgrades-rollback.md).

- **Browser sign-in (OAuth)**: add the gateway URL to Claude Code *without* a key
  (`claude mcp add --transport http woo-gateway <gateway>/mcp`), then `/mcp` → *Authenticate*. The browser opens a
  consent page; approve it and the app works with your permissions (access tokens last an hour and renew on their
  own). Approved apps are listed, and revocable at once, under **API keys → Connected apps**; the audit log marks
  their calls `oauth`. API keys keep working side by side.

## Make targets
| Target | What it does |
|---|---|
| `make up` / `make down` / `make reset` | install everything / uninstall releases (data kept) / delete the cluster and reinstall |
| `make images` | build all images (tag from git) and import them into k3d |
| `make store`, `make upstreams`, `make platform` | install or upgrade one part |
| `make secrets-llm` | create the `platform-llm` Secret from `.env` |
| `make status`, `make admin-password`, `make store-admin-password` | inspect |
| `make lint`, `make typecheck`, `make test`, `make test-integration` | checks (integration tests start Postgres in Docker) |
| `make e2e` | acceptance + isolation tests against the running cluster |
| `make e2e-stores` | bonus store provisioning tests (creates/deletes `paris` and `tokyo`, ~15 min) |
| `make e2e-hosted` | hosted MCP server test (deploys and removes `pincode-hosted`, ~1 min) |
| `make e2e-scale` | two replicas: latency in-cluster, leader killed under load, PDB, rate limit (`make platform SCALE=1` first, ~3 min) |
| `make e2e-oauth` | MCP OAuth flow through the ingress: sign-in, tool call, audit, revoke (~30 s) |
| `make e2e-upgrades` | upgrade + rollback of gateway, pincode and store with backup/restore (~2 min) |
| `make backup-store`, `make restore-store TS=…`, `make store-upgrade` | store backup to `backups/`, restore, backup-then-upgrade |
| `make vps-bootstrap`, `make prod-up`, `make vps-tunnel` | VPS: k3s + cert-manager, deploy everything with prod values, reopen the API tunnel |
| `make test-k8s`, `make verify-k8s` | network/PSA/RBAC/SSRF checks; every workload has requests, limits and probes |

## Production-like setup (VPS, k3s)
Same charts, different values (`values-prod.yaml`): real domains with TLS from cert-manager, `local-path` storage,
pre-created Secrets, two gateway replicas, multi-arch images from GHCR. Step by step: [docs/vps-deploy.md](docs/vps-deploy.md).

## Repository layout
| Path | Contents |
|---|---|
| `charts/woo-store` | WordPress + MariaDB + setup Job + woo-mcp for one store |
| `charts/mcp-server` | generic chart for a containerised MCP server (gst, pincode) |
| `charts/mcp-platform` | gateway, dashboard, Postgres, Ingress, NetworkPolicies, RBAC |
| `services/gateway` | FastAPI + MCP SDK gateway, REST API, chat agent (Python) |
| `services/dashboard` | React dashboard |
| `services/woo-mcp`, `services/mcp-gst`, `services/mcp-pincode` | the MCP servers |
| `packages/wmg-common` | shared Python code (logging, settings, auth middleware) |
| `tests/e2e` | live-cluster acceptance and isolation tests |
| `scripts` | `mcp_cli.py` (command-line MCP client), `llm_check.py` (model selection) |

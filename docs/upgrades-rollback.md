# Upgrades and rollback

Everything runs as a Helm release, so every upgrade is a numbered revision and every revision can be rolled
back with one command. The one thing Helm can't roll back is data written by the new version, so the store gets a
backup first and a restore if needed.

| Release | Namespace | Chart | Upgrade | Rollback |
|---|---|---|---|---|
| `platform` (gateway, dashboard, Postgres) | `mcp-platform` | `charts/mcp-platform` | `make platform` | `helm rollback platform <rev>` |
| `gst`, `pincode` (MCP servers) | `mcp-upstreams` | `charts/mcp-server` | `make upstreams` | `helm rollback <name> <rev>` |
| `main` (WordPress, WooCommerce, MariaDB, woo-mcp) | `store-main` | `charts/woo-store` | `make store-upgrade` | `helm rollback main <rev>` + `make restore-store` |

On the VPS, add `ENV=prod` to every `make` command (it points `kubectl` and `helm` at the VPS) and
`IMAGE_TAG=<git sha>` for a new release; for plain `helm`, use `KUBECONFIG=~/.kube/wmg-vps.yaml`.

## Rules that make rollback safe
- **Gateway database migrations are expand-only**: a new release may add tables and nullable columns, never drop
  or rename what the previous release reads. So the previous image runs fine on the newer schema, and rolling back
  the gateway never touches Postgres. Example: `004_key_limits.sql` (P16) adds `api_keys.rate_limit_per_min`; the
  P15 gateway never reads it. Removing something takes two releases: stop using it, then drop it.
- **Images are immutable**: tagged with the git sha, never `latest`, so a revision always means the same code.
- **Stores converge to their values**: the setup Job runs after install, upgrade **and rollback**, so WordPress
  settings (title, currency, plugin version) always match the release's values.
- **Two gateway replicas + a PodDisruptionBudget** (production): a rolling upgrade or rollback replaces one pod
  at a time and MCP clients see no failed requests (`make e2e-scale` kills the leader under load: 0 failures).

## Gateway and dashboard
```bash
make ENV=prod platform IMAGE_TAG=<new sha>          # upgrade: rolling, one pod at a time
helm history platform -n mcp-platform               # the revisions and what each one was
helm rollback platform <previous rev> -n mcp-platform --wait
```
Check: `kubectl get pods -n mcp-platform` all Ready, the dashboard loads, and an MCP client lists tools.

## MCP servers
```bash
make ENV=prod upstreams IMAGE_TAG=<new sha>
helm rollback pincode <previous rev> -n mcp-upstreams --wait
```
The gateway health check reconnects within 10 s; calls in that window answer `[gateway] … unreachable` and
succeed again once it is back.

## Store
```bash
make store-upgrade STORE_SET='--set-string store.title=My Shop'   # 1. backup → 2. helm upgrade
```
`make store-upgrade` always runs `make backup-store` first. A backup is
`backups/<store>/<UTC timestamp>/` (git-ignored): `db.sql.gz` (a full `mariadb-dump`, replaces the whole database
on restore), `wp-content.tgz` (plugins, themes, uploads), `manifest.json` (Helm revision and images) and
`SHA256SUMS`. Passwords stay inside the pods: the scripts read them from the containers' own environment.

Rolling back:
```bash
helm history main -n store-main
helm rollback main <previous rev> -n store-main --wait     # config and images come back
make restore-store TS=<timestamp>                          # data comes back (asks before replacing)
```
A config-only change (title, resources) needs just the `helm rollback`. After a WooCommerce or WordPress
version bump, restore as well: their database migrations run on upgrade and are not reversible.

### WooCommerce version bump
WooCommerce is pinned (`woocommerce.version`, now 11.1.2). To move to a new version:
1. Try it on the local cluster first: `make store-upgrade STORE_SET='--set-string woocommerce.version=<new>'`.
2. Run `make store-test` and `make e2e` (checkout, orders, the woo-mcp tools).
3. Then the same on the VPS with `ENV=prod`. If anything fails: `helm rollback` + `make ENV=prod restore-store`.

## Tested
`make e2e-upgrades` (`tests/e2e/test_upgrades.py`, ~2 min on the local cluster) runs all of it on every change:
- **Gateway**: upgrade to a new image, which serves the same keys and data; roll back to the previous image,
  with the same row counts, and Helm records "Rollback to <rev>".
- **pincode**: the same, with the gateway still calling it after each step.
- **Store**: back up, then upgrade with a new title and add a product and an upload. Roll back: the old title
  returns, the product stays. Restore: the product and upload are gone, the product count matches the backup,
  and the storefront and woo-mcp tools still work.

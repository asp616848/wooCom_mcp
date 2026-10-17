#!/usr/bin/env bash
# Provision one bonus store (DESIGN §13.3). Idempotent: every step converges, so a retried Job just continues.
# Usage: provision-store.sh <name>   (in a deployer Job; env VALUES_ENV=local|prod)
# Input: Secret wmg-prov-<name> mounted at /input: values.json (host, scheme, image, TLS secret), mcp-token.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ ${1:-} == --help ]] && { sed -n '2,4p' "$0"; exit 0; }
NAME=$(store_name "${1:-}")
NS=store-$NAME
ENV=${VALUES_ENV:-local}
CHART=/charts/woo-store
[[ -f /input/values.json && -f /input/mcp-token ]] || { echo "missing /input/values.json or /input/mcp-token" >&2; exit 2; }

log "1/6 namespace $NS"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS" >/dev/null
# D22: the apache WordPress image binds :80 as root, so stores run under PSA baseline (as store-main)
kubectl label --overwrite namespace "$NS" wmg.dev/role=store "wmg.dev/store=$NAME" \
  pod-security.kubernetes.io/enforce=baseline >/dev/null

log "2/6 access"
ensure_access "$NS"

log "3/6 credentials"
if kubectl get secret "$NAME-store-credentials" -n "$NS" >/dev/null 2>&1; then
  echo "kept $NAME-store-credentials (the data volumes only know the first passwords)"
else
  rnd() { openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
  hex40() { openssl rand -hex 20; }
  # values via files (process substitution): nothing on the command line or in the log
  kubectl create secret generic "$NAME-store-credentials" -n "$NS" \
    --from-file=mariadb-root-password=<(rnd 32) --from-file=mariadb-password=<(rnd 32) \
    --from-file=wordpress-admin-password=<(rnd 24) \
    --from-file=wc-consumer-key=<(printf 'ck_%s' "$(hex40)") --from-file=wc-consumer-secret=<(printf 'cs_%s' "$(hex40)") \
    --from-file=mcp-token=/input/mcp-token >/dev/null
  echo "created $NAME-store-credentials"
fi

log "4/6 release state"
recover_release "$NAME" "$NS"

log "5/6 helm upgrade --install $NAME ($ENV)"
helm upgrade --install "$NAME" "$CHART" -n "$NS" -f "$CHART/values-$ENV.yaml" -f "$CHART/values-bonus.yaml" \
  -f /input/values.json --wait --timeout 12m --rollback-on-failure

log "6/6 helm test"
helm test "$NAME" -n "$NS" --timeout 3m --logs
log "STORE READY: $NAME"

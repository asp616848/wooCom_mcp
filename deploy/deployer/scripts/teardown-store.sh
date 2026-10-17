#!/usr/bin/env bash
# Remove one bonus store (DESIGN §13.3): Helm release, then the namespace (PVCs → PVs with reclaim Delete, Secrets).
# Idempotent: a store that is already gone succeeds. Usage: teardown-store.sh <name>
set -euo pipefail
source "$(dirname "$0")/lib.sh"

NAME=$(store_name "${1:-}")
NS=store-$NAME

if ! kubectl get namespace "$NS" >/dev/null 2>&1; then
  log "namespace $NS is already gone"
  exit 0
fi
log "1/2 helm uninstall $NAME"
ensure_access "$NS"
helm uninstall "$NAME" -n "$NS" --wait --ignore-not-found --timeout 5m
log "2/2 delete namespace $NS"
kubectl delete namespace "$NS" --wait --timeout=5m --ignore-not-found
log "STORE REMOVED: $NAME"

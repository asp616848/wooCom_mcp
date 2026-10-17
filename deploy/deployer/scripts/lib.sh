#!/usr/bin/env bash
# Shared helpers for the deployer scripts (DESIGN §13.3). Sourced, not executed.
set -euo pipefail

SA_NS=$(cat /var/run/secrets/kubernetes.io/serviceaccount/namespace 2>/dev/null || echo mcp-platform)
STORE_ROLE=wmg-store-deployer   # ClusterRole with the namespaced rights a store release needs (platform chart)

log() { echo "==> $*"; }

store_name() {
  local name=${1:-}
  if [[ ! $name =~ ^[a-z][a-z0-9]{2,15}$ ]] || [[ $name == main ]]; then
    echo "invalid store name '${name}' (^[a-z][a-z0-9]{2,15}$, not 'main')" >&2
    exit 2
  fi
  echo "$name"
}

# The deployer holds no namespaced rights cluster-wide: it binds itself to $STORE_ROLE inside one store namespace
# (RBAC allows only `bind` on that role; the ValidatingAdmissionPolicy limits it to store-* namespaces).
ensure_access() {
  local ns=$1
  kubectl get rolebinding wmg-deployer -n "$ns" >/dev/null 2>&1 || \
    kubectl create rolebinding wmg-deployer -n "$ns" --clusterrole="$STORE_ROLE" \
      --serviceaccount="$SA_NS:wmg-deployer" >/dev/null
  for _ in $(seq 1 30); do  # RBAC caches update within a second or two
    [[ $(kubectl auth can-i create secrets -n "$ns" 2>/dev/null) == yes ]] && return 0
    sleep 1
  done
  echo "RoleBinding in $ns did not take effect within 30 s" >&2
  exit 1
}

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

hosted_slug() {
  local slug=${1:-}
  if [[ ! $slug =~ ^[a-z][a-z0-9-]{1,29}$ ]] || [[ $slug == *--* ]] || [[ $slug == *- ]]; then
    echo "invalid hosted server slug '${slug}' (^[a-z][a-z0-9-]{1,29}$, no '--', no trailing '-')" >&2
    exit 2
  fi
  echo "$slug"
}

# A killed Job pod can leave a release mid-operation; bring it back to a state `upgrade --install` accepts
# (D91). Helm 4 `list` shows releases in every status by default. Usage: recover_release <release> <namespace>
recover_release() {
  local rel=$1 ns=$2 status
  status=$(helm list -n "$ns" -o json | jq -r --arg n "$rel" '.[] | select(.name == $n) | .status')
  echo "release status: ${status:-none}"
  case "$status" in
    pending-install|uninstalling)
      log "previous install was interrupted: uninstalling it"
      helm uninstall "$rel" -n "$ns" --wait --timeout 5m ;;
    pending-upgrade|pending-rollback)
      log "previous upgrade was interrupted: rolling back"
      helm rollback "$rel" 0 -n "$ns" --wait --timeout 5m ;;
    failed)
      if ! helm history "$rel" -n "$ns" -o json | jq -e 'any(.[]; .status == "deployed" or .status == "superseded")' >/dev/null; then
        log "release never deployed: uninstalling it"
        helm uninstall "$rel" -n "$ns" --wait --timeout 5m
      fi ;;
  esac
}

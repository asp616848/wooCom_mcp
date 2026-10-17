#!/usr/bin/env bash
# Deploy one hosted MCP server (DESIGN §14, SO-5) with the mcp-server chart into mcp-hosted. Idempotent.
# Usage: deploy-hosted.sh <slug>   (in a deployer Job)
# Input: Secret wmg-hosted-<slug> mounted at /input: values.json (image, port, env, ...), token (unless auth none).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

[[ ${1:-} == --help ]] && { sed -n '2,4p' "$0"; exit 0; }
SLUG=$(hosted_slug "${1:-}")
NS=${HOSTED_NAMESPACE:-mcp-hosted}
REL=hosted-$SLUG
CHART=/charts/mcp-server
[[ -f /input/values.json ]] || { echo "missing /input/values.json" >&2; exit 2; }
# The namespace, its quota and this deployer's RoleBinding come from the platform chart (D94)
kubectl get namespace "$NS" >/dev/null || { echo "namespace $NS is missing (make platform creates it)" >&2; exit 2; }

log "1/3 credential"
if [[ -f /input/token ]]; then
  # the gateway generated it and holds it encrypted; the file keeps it off the command line and out of the log
  kubectl create secret generic "$REL-mcp-auth" -n "$NS" --from-file=token=/input/token \
    --dry-run=client -o yaml | kubectl label --local -f - -o yaml "wmg.dev/hosted=$SLUG" | kubectl apply -f - >/dev/null
  echo "applied $REL-mcp-auth"
else
  echo "auth none: no credential"
fi

log "2/3 release state"
recover_release "$REL" "$NS"

log "3/3 helm upgrade --install $REL"
helm upgrade --install "$REL" "$CHART" -n "$NS" -f /input/values.json --wait --timeout 5m --rollback-on-failure
log "HOSTED SERVER READY: $REL"

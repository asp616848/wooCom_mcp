#!/usr/bin/env bash
# Remove one hosted MCP server (DESIGN §14, SO-5): its Helm release (Deployment, Service, NetworkPolicy, env
# Secret) and its credential Secret. Idempotent: a server that is already gone succeeds. Usage: remove-hosted.sh <slug>
set -euo pipefail
source "$(dirname "$0")/lib.sh"

SLUG=$(hosted_slug "${1:-}")
NS=${HOSTED_NAMESPACE:-mcp-hosted}
REL=hosted-$SLUG

log "1/2 helm uninstall $REL"
helm uninstall "$REL" -n "$NS" --wait --ignore-not-found --timeout 3m
log "2/2 credential"
kubectl delete secret "$REL-mcp-auth" -n "$NS" --ignore-not-found >/dev/null
log "HOSTED SERVER REMOVED: $REL"

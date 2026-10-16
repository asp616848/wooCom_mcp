#!/usr/bin/env bash
# Production Secrets (DESIGN §17): created once, before the charts, and referenced via existingSecret.
# Random values are generated here and piped straight into kubectl: nothing is printed or written to disk.
# Existing Secrets are never overwritten (data volumes only know the first passwords). Only platform-llm is
# refreshed from .env.prod (GEMINI_API_KEY), so the LLM key can be rotated.
# Usage: scripts/prod-secrets.sh [.env.prod]   (KUBECONFIG must point at the VPS)
set -euo pipefail

ENV_FILE=${1:-.env.prod}
STORE=${STORE:-main}
STORE_NS=store-$STORE
UPSTREAMS_NS=mcp-upstreams
PLATFORM_NS=mcp-platform

envval() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d "\"' \r"; }
rnd() { openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }
hex40() { openssl rand -hex 20; }

ns() {
  kubectl create namespace "$1" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl label --overwrite namespace "$1" "wmg.dev/role=$2" "pod-security.kubernetes.io/enforce=$3" >/dev/null
}

# create_once <namespace> <name> <key>=<value>...   (values via --from-file on process substitution: no argv leak)
create_once() {
  local n=$1 name=$2; shift 2
  if kubectl get secret "$name" -n "$n" >/dev/null 2>&1; then echo "keep    $n/$name"; return; fi
  local args=() kv
  for kv in "$@"; do args+=(--from-file="${kv%%=*}"=<(printf '%s' "${kv#*=}")); done
  kubectl create secret generic "$name" -n "$n" "${args[@]}" >/dev/null
  echo "created $n/$name"
}

ns "$STORE_NS" store baseline
ns "$UPSTREAMS_NS" upstreams restricted
ns "$PLATFORM_NS" platform restricted

create_once "$STORE_NS" "$STORE-store-credentials" \
  mariadb-root-password="$(rnd 32)" mariadb-password="$(rnd 32)" wordpress-admin-password="$(rnd 24)" \
  wc-consumer-key="ck_$(hex40)" wc-consumer-secret="cs_$(hex40)" mcp-token="$(rnd 48)"
create_once "$UPSTREAMS_NS" gst-mcp-auth token="$(rnd 48)"
create_once "$UPSTREAMS_NS" pincode-mcp-auth token="$(rnd 48)"
create_once "$PLATFORM_NS" platform-credentials \
  postgres-password="$(rnd 32)" api-key-pepper="$(rnd 48)" \
  credential-encryption-key="$(openssl rand -base64 32)" jwt-secret="$(rnd 48)" admin-password="$(rnd 20)"

key=$(envval GEMINI_API_KEY)
if [ -n "$key" ]; then
  printf '%s' "$key" | kubectl create secret generic platform-llm -n "$PLATFORM_NS" --from-file=api-key=/dev/stdin \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  echo "applied $PLATFORM_NS/platform-llm"
else
  echo "WARNING: GEMINI_API_KEY not set in $ENV_FILE - chat will be unavailable"
fi

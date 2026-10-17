#!/usr/bin/env bash
# TESTING §7 load run: autocannon 8.0.0 POSTs one raw modern (2026-07-28) JSON-RPC request over and over.
#
#   PERF_AUTH='Authorization=Bearer mgw_…' tests/perf/run.sh <list|call> [duration_s] [rate_rps] [url]
#
# list = tools/list; call = tools/call of PERF_TOOL (default pincode__list_serviceable_cities, cheap and local).
# rate 0 = as fast as PERF_CONNECTIONS (default 20) connections go. Prints autocannon's JSON result on stdout.
# Every response must equal the first one (fetched here with curl): a JSON-RPC error such as a rate limit comes
# back as HTTP 200, so a body mismatch is how it shows up (autocannon counts it in `mismatches`).
# The headers and _meta were copied from a request captured from the MCP Python client (TRACKER "P16 evidence").
set -euo pipefail

what=${1:?usage: run.sh <list|call> [duration_s] [rate_rps] [url]}
duration=${2:-30}
rate=${3:-0}
url=${4:-${PERF_URL:-http://gateway.localhost/mcp}}
: "${PERF_AUTH:?set PERF_AUTH to 'Header-Name=value' (e.g. Authorization=Bearer mgw_...)}"
tool=${PERF_TOOL:-pincode__list_serviceable_cities}

meta='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientInfo":{"name":"wmg-perf","version":"1"},"io.modelcontextprotocol/clientCapabilities":{}}'
headers=(
  "$PERF_AUTH"
  "Content-Type=application/json"
  "Accept=application/json, text/event-stream"
  "MCP-Protocol-Version=2026-07-28"
)
case $what in
  list)
    body="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{$meta}}"
    headers+=("Mcp-Method=tools/list")
    ;;
  call)
    body="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":{},$meta}}"
    headers+=("Mcp-Method=tools/call" "Mcp-Name=$tool")
    ;;
  *) echo "unknown run '$what' (list|call)" >&2; exit 2 ;;
esac

curl_args=(-sS --fail --max-time 10 -X POST --data "$body")
cannon_args=(-j -c "${PERF_CONNECTIONS:-20}" -d "$duration" -t 10 -m POST -b "$body")
for h in "${headers[@]}"; do
  curl_args+=(-H "${h/=/: }")
  cannon_args+=(-H "$h")
done
expected=$(curl "${curl_args[@]}" "$url")
case $expected in
  *'"error"'* | *'"isError":true'*) echo "reference request failed: ${expected:0:300}" >&2; exit 1 ;;
esac
if [ "$rate" -gt 0 ]; then cannon_args+=(-R "$rate"); fi

exec npx --yes autocannon@8.0.0 "${cannon_args[@]}" -E "$expected" "$url"

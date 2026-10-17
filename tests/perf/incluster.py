"""TESTING §7 in-cluster latency (NFR-P1, NFR-P2): runs inside a gateway pod (no laptop, k3d or Traefik hop).

    kubectl exec -i -n mcp-platform <gateway-pod> -- \
        python - <list|call|direct> <rate> <seconds> <credential> < tests/perf/incluster.py

list   = tools/list on the pod's own gateway (127.0.0.1:8080) with an API key
call   = tools/call pincode__list_serviceable_cities on the pod's own gateway
direct = tools/call list_serviceable_cities straight to the pincode Service with its X-Api-Key

Requests start on a fixed schedule (`rate` per second) and latency counts from the scheduled start, so a slow
response is never hidden (no coordinated omission). Prints one JSON line: count, errors, p50/p95/p99 in ms.
Uses only what the gateway image already has (Python 3.14, anyio, httpx2).
"""

import json
import statistics
import sys
import time

import anyio
import httpx2

GATEWAY = "http://127.0.0.1:8080/mcp"
PINCODE = "http://pincode.mcp-upstreams.svc.cluster.local:8080/mcp"
META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "wmg-perf", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
WARMUP = 20


def request(kind: str, credential: str) -> tuple[str, dict[str, str], bytes]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
    }
    params: dict[str, object] = {}
    if kind == "list":
        url, method = GATEWAY, "tools/list"
        headers |= {"Authorization": f"Bearer {credential}", "Mcp-Method": method}
    else:
        name = "pincode__list_serviceable_cities" if kind == "call" else "list_serviceable_cities"
        url, method, params = (
            (GATEWAY if kind == "call" else PINCODE),
            "tools/call",
            {"name": name, "arguments": {}},
        )
        auth = {"Authorization": f"Bearer {credential}"} if kind == "call" else {"X-Api-Key": credential}
        headers |= auth | {"Mcp-Method": method, "Mcp-Name": name}
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": META}}
    return url, headers, json.dumps(body).encode()


async def main(kind: str, rate: float, seconds: float, credential: str) -> dict[str, object]:
    url, headers, body = request(kind, credential)
    latencies: list[float] = []
    errors: list[str] = []
    limits = httpx2.Limits(max_connections=64, max_keepalive_connections=64)
    async with httpx2.AsyncClient(timeout=10, limits=limits) as client:
        for _ in range(WARMUP):
            await client.post(url, content=body, headers=headers)

        async def one(scheduled: float) -> None:
            try:
                r = await client.post(url, content=body, headers=headers)
                ok = r.status_code == 200 and '"error"' not in r.text[:300] and '"isError":true' not in r.text
                if not ok:
                    errors.append(f"{r.status_code} {r.text[:200]}")
            except Exception as exc:  # counted, never raised: one failure must not stop the run
                errors.append(type(exc).__name__)
            latencies.append((time.perf_counter() - scheduled) * 1000)

        start = time.perf_counter()
        async with anyio.create_task_group() as tg:
            for i in range(int(rate * seconds)):
                scheduled = start + i / rate
                await anyio.sleep(max(0.0, scheduled - time.perf_counter()))
                tg.start_soon(one, scheduled)
    q = statistics.quantiles(latencies, n=100)
    return {
        "kind": kind,
        "rate": rate,
        "count": len(latencies),
        "errors": len(errors),
        "firstErrors": errors[:3],
        "p50": round(q[49], 2),
        "p95": round(q[94], 2),
        "p99": round(q[98], 2),
    }


if __name__ == "__main__":
    kind, rate, seconds, credential = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    if kind not in ("list", "call", "direct"):
        sys.exit("kind must be list, call or direct")
    print(json.dumps(anyio.run(main, kind, rate, seconds, credential)))

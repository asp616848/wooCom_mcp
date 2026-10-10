"""`tools/call` (DESIGN §5.7, D37-D39): resolve → authorize → route to the upstream → cap → audit.

Protocol problems (unknown tool, forbidden) are JSON-RPC errors. Upstream problems (unreachable, timeout,
error, oversize) are tool results with `isError` and a `[gateway] …` message, so the model can read them and
move on.
"""

import time
from typing import Any

import anyio
import anyio.lowlevel
import mcp_types as types
from mcp import MCPError
from prometheus_client import Counter, Histogram

from wmg_gateway.audit.audit import AuditRecord, Outcome, summarize_args
from wmg_gateway.catalog.catalog import ToolEntry
from wmg_gateway.mcp.caller import Caller, caller_from, client_name
from wmg_gateway.request_context import request_meta
from wmg_gateway.runtime import Runtime
from wmg_gateway.upstream.health import mark_unreachable
from wmg_gateway.upstream.manager import UpstreamError

FORBIDDEN = -32010
RATE_LIMITED = -32011

TOOL_CALLS = Counter("wmg_tool_calls_total", "tools/call requests", ["server", "outcome"])
TOOL_CALL_SECONDS = Histogram("wmg_tool_call_duration_seconds", "tools/call duration", ["server"])

# Failures where the upstream never saw the request: safe to retry once for read-only/idempotent tools (D39).
RETRYABLE = frozenset({"CONNECTION_REFUSED"})


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


class CallContext:
    """Collects what the audit row needs while the call proceeds."""

    def __init__(
        self, rt: Runtime, ctx: Any, caller: Caller, name: str, args: dict[str, Any], mode: str
    ) -> None:
        self.rt = rt
        self.caller = caller
        self.name = name
        self.args = args
        self.mode = mode
        self.client = client_name(ctx)
        self.started = time.monotonic()

    async def audit(self, outcome: Outcome, entry: ToolEntry | None = None, error: str | None = None) -> None:
        meta = request_meta.get()
        duration = time.monotonic() - self.started
        slug = entry.server.slug if entry else None
        TOOL_CALLS.labels(slug or "-", outcome).inc()
        if entry is not None:
            TOOL_CALL_SECONDS.labels(slug).observe(duration)
        await self.rt.audit.write(
            AuditRecord(
                method="tools/call",
                outcome=outcome,
                duration_ms=int(duration * 1000),
                user_id=self.caller.user_id,
                user_email=self.caller.email,
                credential_type=self.caller.credential_type,
                credential_id=self.caller.credential_id,
                server_id=entry.server.id if entry else None,
                server_slug=slug,
                tool_name=entry.original if entry else None,
                exposed_name=self.name,
                error_message=error,
                args_summary=summarize_args(self.args),
                client_name=self.client,
                client_mode=self.mode,
                request_id=meta.request_id if meta else None,
            )
        )


async def call_tool(rt: Runtime, ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    return await call_exposed(rt, ctx, params.name, params.arguments or {}, mode="full")


async def call_exposed(
    rt: Runtime, ctx: Any, name: str, args: dict[str, Any], mode: str
) -> types.CallToolResult:
    cc = CallContext(rt, ctx, caller_from(ctx), name, args, mode)
    entry = rt.catalog.current.by_exposed(name)
    if entry is None:
        await cc.audit("unknown_tool", error="Unknown tool")
        raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {name}")
    server = entry.server
    if not server.enabled:
        await cc.audit("unknown_tool", entry, "Server disabled")
        raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {name} (server disabled)")
    if entry.admin_only and cc.caller.role != "admin":
        await cc.audit("denied", entry, "Admin-only tool")
        raise MCPError(FORBIDDEN, f"Forbidden: {name} requires admin role")
    # Step 5 (rate limit, -32011) is added with SO-7 in P16.
    if server.status != "connected":
        message = (
            f"[gateway] Server '{server.slug}' is unreachable: {server.last_error or 'not connected yet'}"
        )
        await cc.audit("upstream_error", entry, message)
        return _error_result(message)

    try:
        result = await _call_with_timeout(rt, entry, args)
    except TimeoutError:
        message = f"[gateway] '{server.slug}' timed out after {server.timeout_ms}ms"
        await cc.audit("timeout", entry, message)
        return _error_result(message)
    except UpstreamError as exc:
        if exc.code == "TIMEOUT":  # slow ≠ dead: don't hide the server
            message = f"[gateway] '{server.slug}' timed out after {server.timeout_ms}ms"
            await cc.audit("timeout", entry, message)
            return _error_result(message)
        await mark_unreachable(rt.pool, server, exc)
        message = f"[gateway] '{server.slug}' unreachable: {exc.code}"
        await cc.audit("upstream_error", entry, f"{message} ({exc.message})")
        return _error_result(message)
    except MCPError as exc:
        message = f"[gateway] '{server.slug}' error {exc.error.code}: {exc.error.message}"
        await cc.audit("upstream_error", entry, message)
        return _error_result(message)

    size = len(result.model_dump_json(by_alias=True))
    if size > rt.settings.max_result_bytes:
        message = f"[gateway] result too large ({size} bytes)"
        await cc.audit("upstream_error", entry, message)
        return _error_result(message)
    text_error = None
    if result.is_error:
        first = next((c for c in result.content if isinstance(c, types.TextContent)), None)
        text_error = first.text[:500] if first else "Tool returned an error"
    await cc.audit("tool_error" if result.is_error else "success", entry, text_error)
    return result


async def _call_with_timeout(rt: Runtime, entry: ToolEntry, args: dict[str, Any]) -> types.CallToolResult:
    """Semaphore wait + upstream call share the server's timeout; one retry per D39."""
    server = entry.server
    with anyio.fail_after(server.timeout_s):
        async with rt.manager.semaphore(server):
            # A waiter can get a slot at the very moment its own deadline passes; don't send anything then.
            await anyio.lowlevel.checkpoint_if_cancelled()
            tool = entry.upstream_tool
            try:
                return await rt.manager.call(server, tool, args)
            except UpstreamError as exc:
                if exc.code not in RETRYABLE or not entry.read_only_or_idempotent:
                    raise
            return await rt.manager.call(server, tool, args)

"""Upstream manager (DESIGN §5.6, D42): talks to upstream MCP servers on behalf of every caller.

- One pooled `httpx2.AsyncClient` per server per replica, with the decrypted credential as a default header
  and the SSRF-guarded transport. Rebuilt when the server's config changes. Downstream tokens are never
  forwarded.
- One short-lived MCP `Client` per operation over that pooled client: modern upstreams are called with the
  cached `DiscoverResult` (no negotiation round trip), legacy ones with `mode="legacy"`.
- `probe()` detects the era (`mode="auto"`) and snapshots the tool list; used by the health loop and the API.
"""

import ssl
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import anyio
import httpx2
import mcp_types as types
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from wmg_common.logging import get_logger
from wmg_gateway import APP_VERSION
from wmg_gateway.catalog.catalog import ServerEntry
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.security.ssrf import SsrfBlockedError
from wmg_gateway.upstream.transport import GuardedTransport

log = get_logger(__name__)

MODERN = "2026-07-28"
MAX_TOOLS = 500
CLIENT_INFO = types.Implementation(name="wmg-gateway", version=APP_VERSION)


@dataclass(frozen=True)
class UpstreamConfig:
    """Everything needed to reach one upstream (a saved server, or an unsaved one under test)."""

    url: str
    auth_type: str
    auth_header_name: str | None
    credential: str | None
    timeout_s: float


@dataclass
class ProbeResult:
    era: str  # "modern" | "legacy"
    discover_result: dict[str, Any] | None
    server_info: dict[str, Any] | None
    tools: list[types.Tool] = field(default_factory=list)


class UpstreamError(Exception):
    """A categorized upstream failure (`code` is stored in `mcp_servers.last_error_code`)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# HTTP status of the last >= 300 response seen during the current upstream operation. The SDK turns HTTP
# errors into a generic JSON-RPC error, so the response hook records the status for categorization.
_last_status: ContextVar[list[int] | None] = ContextVar("wmg_upstream_status", default=None)


async def _record_status(response: httpx2.Response) -> None:
    holder = _last_status.get()
    if holder is not None and response.status_code >= 300:
        holder.append(response.status_code)


def credential_headers(config: UpstreamConfig) -> dict[str, str]:
    if not config.credential or config.auth_type == "none":
        return {}
    if config.auth_type == "bearer":
        return {"Authorization": f"Bearer {config.credential}"}
    return {config.auth_header_name or "X-Api-Key": config.credential}


def build_http_client(
    config: UpstreamConfig, max_inflight: int, transport: httpx2.AsyncBaseTransport | None = None
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=transport or GuardedTransport(),
        headers=credential_headers(config),
        timeout=httpx2.Timeout(connect=3.0, read=config.timeout_s, write=10.0, pool=config.timeout_s),
        limits=httpx2.Limits(max_connections=max_inflight, max_keepalive_connections=8),
        event_hooks={"response": [_record_status]},
    )


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def categorize(exc: BaseException, statuses: list[int] | None = None) -> UpstreamError:
    """Map any failure from an upstream operation to (code, human message) — DESIGN §5.6."""
    if isinstance(exc, UpstreamError):
        return exc
    leaf = _leaves(exc)[0]
    status = statuses[-1] if statuses else None
    for e in _chain(leaf):
        if isinstance(e, SsrfBlockedError):
            return UpstreamError("SSRF_BLOCKED", str(e))
        if isinstance(e, httpx2.TimeoutException | TimeoutError):
            return UpstreamError("TIMEOUT", "Timed out")
        if isinstance(e, ssl.SSLError):
            return UpstreamError("TLS_ERROR", f"TLS error: {e}")
        if isinstance(e, httpx2.ConnectError):
            if any("nodename" in str(c) or "Name or service" in str(c) for c in _chain(e)) or any(
                type(c).__name__ == "gaierror" for c in _chain(e)
            ):
                return UpstreamError("DNS_FAILURE", "DNS lookup failed")
            return UpstreamError("CONNECTION_REFUSED", "Connection refused")
        if isinstance(e, httpx2.HTTPStatusError):
            status = e.response.status_code
            break
    if status in (401, 403):
        return UpstreamError("AUTH_REJECTED", f"Upstream rejected the credential (HTTP {status})")
    if status is not None:
        return UpstreamError(f"HTTP_{status}", f"Upstream answered HTTP {status}")
    if isinstance(leaf, MCPError):
        return UpstreamError("PROTOCOL_ERROR", f"MCP error {leaf.error.code}: {leaf.error.message}")
    if isinstance(leaf, httpx2.TransportError | OSError):
        return UpstreamError("CONNECTION_REFUSED", f"{type(leaf).__name__}: {leaf}")
    return UpstreamError("PROTOCOL_ERROR", f"Not an MCP response ({type(leaf).__name__})")


class UpstreamManager:
    def __init__(
        self,
        cipher: CredentialCipher,
        max_inflight: int,
        transport_factory: Callable[[], httpx2.AsyncBaseTransport] = GuardedTransport,
    ) -> None:
        self._cipher = cipher
        self._max_inflight = max_inflight
        self._transport_factory = transport_factory
        self._clients: dict[str, tuple[tuple[Any, ...], httpx2.AsyncClient]] = {}
        self._retired: list[httpx2.AsyncClient] = []
        self._semaphores: dict[str, anyio.Semaphore] = {}

    def config_for(self, server: ServerEntry) -> UpstreamConfig:
        credential = self._cipher.decrypt(server.credential_enc, server.id) if server.credential_enc else None
        return UpstreamConfig(
            url=server.url,
            auth_type=server.auth_type,
            auth_header_name=server.auth_header_name,
            credential=credential,
            timeout_s=server.timeout_s,
        )

    def http_client(self, server: ServerEntry) -> httpx2.AsyncClient:
        key = server.config_key()
        cached = self._clients.get(server.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        if cached is not None:
            # in-flight calls may still use the old client; it is closed at shutdown
            self._retired.append(cached[1])
        client = build_http_client(self.config_for(server), self._max_inflight, self._transport_factory())
        self._clients[server.id] = (key, client)
        return client

    def semaphore(self, server: ServerEntry) -> anyio.Semaphore:
        sem = self._semaphores.get(server.id)
        if sem is None:
            sem = self._semaphores[server.id] = anyio.Semaphore(self._max_inflight)
        return sem

    async def forget(self, server_id: str) -> None:
        """Drop the pooled client of a deleted server."""
        cached = self._clients.pop(server_id, None)
        self._semaphores.pop(server_id, None)
        if cached is not None:
            self._retired.append(cached[1])

    async def aclose(self) -> None:
        for client in [c for _, c in self._clients.values()] + self._retired:
            await client.aclose()
        self._clients.clear()
        self._retired.clear()

    @asynccontextmanager
    async def _session(
        self, url: str, http: httpx2.AsyncClient, mode: str, prior: dict[str, Any] | None
    ) -> AsyncIterator[Client]:
        statuses: list[int] = []
        token = _last_status.set(statuses)
        try:
            prior_discover = (
                types.DiscoverResult.model_validate(prior) if prior and mode != "legacy" else None
            )
            async with Client(
                streamable_http_client(url, http_client=http),
                mode=mode,
                prior_discover=prior_discover,
                cache=None,
                client_info=CLIENT_INFO,
            ) as client:
                yield client
        except UpstreamError:
            raise
        except MCPError as exc:
            # A JSON-RPC error from a reachable upstream (e.g. unknown tool) is passed on as-is; HTTP-level
            # failures also surface as MCPError, but then a >= 300 status was recorded.
            if statuses:
                raise categorize(exc, statuses) from exc
            raise
        except Exception as exc:
            raise categorize(exc, statuses) from exc
        finally:
            _last_status.reset(token)

    async def _probe_with(self, url: str, http: httpx2.AsyncClient) -> ProbeResult:
        try:
            return await self._probe_session(url, http)
        except MCPError as exc:  # a reachable server that fails discovery/initialize or tools/list
            raise UpstreamError(
                "ERA_NEGOTIATION_FAILED", f"MCP error {exc.error.code}: {exc.error.message}"
            ) from exc

    async def _probe_session(self, url: str, http: httpx2.AsyncClient) -> ProbeResult:
        async with self._session(url, http, "auto", None) as client:
            modern = client.protocol_version in MODERN_PROTOCOL_VERSIONS
            discover = client.session.discover_result
            info = client.server_info
            tools: list[types.Tool] = []
            cursor: str | None = None
            while True:
                page = await client.list_tools(cursor=cursor)
                tools.extend(page.tools)
                if len(tools) > MAX_TOOLS:
                    raise UpstreamError("TOO_MANY_TOOLS", f"Upstream lists more than {MAX_TOOLS} tools")
                cursor = page.next_cursor
                if not cursor:
                    break
        return ProbeResult(
            era="modern" if modern else "legacy",
            discover_result=discover.model_dump(mode="json", by_alias=True) if modern and discover else None,
            server_info=info.model_dump(mode="json", by_alias=True, exclude_none=True) if info else None,
            tools=tools,
        )

    async def probe(self, server: ServerEntry) -> ProbeResult:
        """Era detection + full tool list over the server's pooled client (health loop)."""
        return await self._probe_with(server.url, self.http_client(server))

    async def probe_config(self, config: UpstreamConfig) -> ProbeResult:
        """Same as `probe` for an unsaved server (API "test connection"); uses a throwaway client."""
        async with build_http_client(config, 2, self._transport_factory()) as http:
            return await self._probe_with(config.url, http)

    async def call(
        self, server: ServerEntry, tool: types.Tool, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """One `tools/call` upstream. Raises UpstreamError for transport/protocol failures."""
        if server.protocol_era == "modern" and server.discover_result:
            mode, prior = MODERN, server.discover_result
        elif server.protocol_era == "legacy":
            mode, prior = "legacy", None
        else:
            mode, prior = "auto", None
        async with self._session(server.url, self.http_client(server), mode, prior) as client:
            # Prime the fresh session with the catalog's copy of the tool so the call needs no extra
            # tools/list: the SDK reads x-mcp-header maps and output schemas from the session's last listing
            # (P8.1).
            client.session._absorb_tool_listing(types.ListToolsResult(tools=[tool]), complete=False)
            return await client.call_tool(tool.name, arguments, read_timeout_seconds=server.timeout_s)

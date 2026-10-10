"""The gateway's own MCP server (DESIGN §5.4, D30, D33): one stateless, dual-era endpoint at `/mcp`.

A low-level `Server` (it passes upstream JSON Schemas through untouched) wrapped by the SDK's Streamable HTTP
app, which also does bearer auth (our verifier), DNS-rebinding protection and era routing per request.
"""

from typing import Any

import mcp_types as types
from mcp.server import CacheHint, Server
from mcp.server.auth.settings import AuthSettings
from mcp.server.subscriptions import ListenHandler
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from wmg_gateway import APP_VERSION
from wmg_gateway.mcp.call_tool import call_tool
from wmg_gateway.mcp.list_tools import list_tools
from wmg_gateway.mcp.verifier import WmgTokenVerifier
from wmg_gateway.runtime import Runtime

TOOLS_LIST_TTL_MS = 10000


def transport_security(rt: Runtime) -> TransportSecuritySettings:
    hosts = rt.settings.host_list()
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{h}:*" for h in hosts)],
        allowed_origins=[rt.settings.public_dashboard_url, rt.settings.public_gateway_url],
    )


def build_full_server(rt: Runtime) -> tuple[Server[Any], Starlette]:
    async def on_list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return await list_tools(rt, ctx, params)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await call_tool(rt, ctx, params)

    def input_schema(name: str) -> dict[str, Any] | None:
        entry = rt.catalog.current.by_exposed(name)
        return entry.input_schema if entry else None

    server: Server[Any] = Server(
        "wmg-gateway",
        version=APP_VERSION,
        title="Woo MCP Gateway",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_subscriptions_listen=ListenHandler(rt.bus),
        get_tool_input_schema=input_schema,
        cache_hints={"tools/list": CacheHint(ttl_ms=TOOLS_LIST_TTL_MS, scope="private")},
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(rt),
        # resource_server_url is a required field (None until OAuth, P17)
        auth=AuthSettings(
            issuer_url=rt.settings.public_gateway_url,
            required_scopes=["mcp"],
            resource_server_url=None,
        ),
        token_verifier=WmgTokenVerifier(rt),
    )
    return server, app

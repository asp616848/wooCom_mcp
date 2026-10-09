"""ASGI app shape shared by the MCP servers behind the gateway (DESIGN §9.0).

Streamable HTTP at `/mcp` (stateless, plain JSON responses), `/livez` + `/readyz` left open for probes,
and the static credential check in front of `/mcp`.
"""

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp

from wmg_common.auth import StaticCredentialMiddleware
from wmg_common.health import ReadyCheck, livez, make_readyz
from wmg_common.settings import McpServerSettings


def build_upstream_app(
    mcp: MCPServer, settings: McpServerSettings, ready: ReadyCheck | None = None
) -> ASGIApp:
    mcp.custom_route("/livez", methods=["GET"], include_in_schema=False)(livez)
    mcp.custom_route("/readyz", methods=["GET"], include_in_schema=False)(make_readyz(ready))
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=settings.allowed_hosts()),
    )
    return StaticCredentialMiddleware(
        app,
        mode=settings.mcp_auth_mode,
        token=settings.mcp_auth_token.get_secret_value(),
        header_name=settings.mcp_auth_header,
        protected_paths=("/mcp",),
    )

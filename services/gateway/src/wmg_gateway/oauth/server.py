"""Wires the SDK's embedded authorization server onto the gateway's MCP apps (DESIGN §15, D58, D98).

Two adjustments to what `create_auth_routes` gives us, both small and both covered by tests:
- the SDK refuses a plain-http issuer unless the host is exactly localhost/127.0.0.1/[::1]; our local cluster
  uses `http://gateway.localhost` (a loopback name, RFC 6761), so `*.localhost` is accepted too. https is
  unchanged.
- its metadata advertises only client-secret auth at the token endpoint; this server serves public clients
  only (D98 #1), so the metadata says `none`.
"""

from typing import Any

from mcp.server.auth import routes as sdk_routes
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.routing import Route

from wmg_gateway.oauth.provider import SCOPE, PgOAuthProvider
from wmg_gateway.runtime import Runtime

METADATA_PATH = "/.well-known/oauth-authorization-server"
_original_validate = sdk_routes.validate_issuer_url


def _validate_issuer(url: AnyHttpUrl) -> None:
    if url.scheme == "http" and (url.host or "").endswith(".localhost"):
        if url.fragment or url.query:
            raise ValueError("Issuer URL must not have a fragment or a query string")
        return
    _original_validate(url)


def auth_settings(rt: Runtime, resource_url: str) -> AuthSettings:
    """Resource-server settings of one MCP endpoint; `resource_url` is the endpoint's own public URL."""
    return AuthSettings(
        issuer_url=rt.settings.public_gateway_url,
        required_scopes=[SCOPE],
        resource_server_url=resource_url,
        validate_token_resource=False,  # tokens are valid on /mcp and /mcp/search alike (D98 #3)
    )


def auth_server_routes(rt: Runtime) -> list[Route]:
    """/authorize, /token, /register, /revoke and the authorization-server metadata, backed by Postgres."""
    sdk_routes.validate_issuer_url = _validate_issuer
    registration = ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE])
    revocation = RevocationOptions(enabled=True)
    # from AuthSettings, which keeps the issuer exactly as configured: RFC 8414 compares issuers as strings,
    # and AnyHttpUrl(...) would add a trailing slash to a path-less URL
    issuer = auth_settings(rt, rt.settings.public_mcp_url).issuer_url
    routes: list[Route] = sdk_routes.create_auth_routes(
        provider=PgOAuthProvider(rt),
        issuer_url=issuer,
        client_registration_options=registration,
        revocation_options=revocation,
    )
    metadata = sdk_routes.build_metadata(issuer, None, registration, revocation)
    metadata.token_endpoint_auth_methods_supported = ["none"]
    metadata.revocation_endpoint_auth_methods_supported = ["none"]
    handler: Any = MetadataHandler(metadata).handle
    ours = Route(
        METADATA_PATH,
        endpoint=sdk_routes.cors_middleware(handler, ["GET", "OPTIONS"]),
        methods=["GET", "OPTIONS"],
    )
    return [ours, *(r for r in routes if getattr(r, "path", None) != METADATA_PATH)]

"""Who is calling `/mcp`: built from the AccessToken our verifier returned (DESIGN §5.4, §7.1)."""

from dataclasses import dataclass
from typing import Any

import mcp_types as types
from mcp import MCPError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from wmg_gateway.audit.audit import CredentialType
from wmg_gateway.request_context import request_meta


@dataclass(frozen=True)
class Caller:
    user_id: str
    email: str
    role: str
    credential_type: CredentialType
    credential_id: str


def _token(ctx: Any) -> AccessToken | None:
    token = get_access_token()
    if token is None and ctx is not None and getattr(ctx, "request", None) is not None:
        user = ctx.request.scope.get("user")
        token = getattr(user, "access_token", None)
    return token


def caller_from(ctx: Any) -> Caller:
    token = _token(ctx)
    claims = (token.claims or {}) if token else {}
    if token is None or token.subject is None or "role" not in claims:
        raise MCPError(types.INVALID_REQUEST, "Authentication required")
    return Caller(
        user_id=token.subject,
        email=str(claims["email"]),
        role=str(claims["role"]),
        credential_type=claims["credential_type"],
        credential_id=str(claims["credential_id"]),
    )


def client_name(ctx: Any) -> str | None:
    """Best effort: the modern request envelope's clientInfo, the legacy handshake's, or the User-Agent."""
    meta = getattr(ctx, "meta", None)
    info: Any = None
    if isinstance(meta, dict):
        info = meta.get(types.CLIENT_INFO_META_KEY)
    elif meta is not None:
        info = (getattr(meta, "model_extra", None) or {}).get(types.CLIENT_INFO_META_KEY)
    name = info.get("name") if isinstance(info, dict) else None
    if name is None:
        params = getattr(getattr(ctx, "session", None), "client_params", None)
        name = params.client_info.name if params is not None else None
    if name is None and (rm := request_meta.get()) is not None:
        name = rm.user_agent
    return str(name)[:100] if name else None

"""The consent step of the OAuth flow (D98 #2): what the dashboard's consent page reads and what
Approve/Deny do.

The pending request is consumed atomically (`DELETE … RETURNING`), so a request can be answered exactly
once, by whichever signed-in user answers first.
"""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri

from wmg_common.errors import AppError
from wmg_gateway.oauth.provider import CODE_TTL, clean_name, new_code, new_family
from wmg_gateway.runtime import Runtime
from wmg_gateway.security import api_keys

NOT_FOUND = "Authorization request not found or expired: start again from the app"


async def describe(conn: Any, request_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT r.params, r.expires_at, c.metadata FROM oauth_requests r "
        "JOIN oauth_clients c ON c.client_id = r.client_id WHERE r.id = $1 AND r.expires_at > now()",
        request_id,
    )
    if row is None:
        raise AppError(404, "NOT_FOUND", NOT_FOUND)
    params = AuthorizationParams.model_validate(row["params"])
    redirect = str(params.redirect_uri)
    return {
        "client_name": clean_name(row["metadata"].get("client_name")),
        "redirect_host": urlparse(redirect).netloc,
        "redirect_uri": redirect,
        "scopes": params.scopes or ["mcp"],
        "expires_at": row["expires_at"],
    }


async def _take(conn: Any, request_id: str) -> tuple[str, AuthorizationParams]:
    row = await conn.fetchrow(
        "DELETE FROM oauth_requests WHERE id = $1 AND expires_at > now() RETURNING client_id, params",
        request_id,
    )
    if row is None:
        raise AppError(404, "NOT_FOUND", NOT_FOUND)
    return row["client_id"], AuthorizationParams.model_validate(row["params"])


async def approve(rt: Runtime, conn: Any, request_id: str, user_id: Any) -> str:
    """Issues a single-use code for the signed-in user; returns the URL back to the app."""
    pepper = rt.settings.api_key_pepper.get_secret_value()
    async with conn.transaction():
        client_id, params = await _take(conn, request_id)
        code = new_code()
        await conn.execute(
            "INSERT INTO oauth_codes (code_hash, client_id, user_id, family_id, scopes, code_challenge, "
            "redirect_uri, redirect_uri_provided_explicitly, resource, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            api_keys.hash_key(code, pepper), client_id, user_id, new_family(), params.scopes or ["mcp"],
            params.code_challenge, str(params.redirect_uri), params.redirect_uri_provided_explicitly,
            params.resource, datetime.now(UTC) + CODE_TTL,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO oauth_consents (user_id, client_id) VALUES ($1, $2) "
            "ON CONFLICT (user_id, client_id) DO UPDATE SET last_approved_at = now()",
            user_id,
            client_id,
        )
    return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)


async def deny(conn: Any, request_id: str) -> str:
    _client_id, params = await _take(conn, request_id)
    return construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)

"""SO-3 OAuth authorization server provider for the SDK's embedded AS (DESIGN §15, D58, D98).

Everything lives in Postgres (`005_oauth.sql`), so any replica can serve any step of the flow. Codes and
tokens are random, shown once, and stored as HMAC-SHA-256 hashes with the API-key pepper. Public clients only
(PKCE S256, no client secret). The consent step is ours: `authorize()` parks the request and sends the browser
to the dashboard (`oauth/consent.py` finishes it after the user approves).
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from wmg_common.logging import get_logger
from wmg_gateway.runtime import Runtime
from wmg_gateway.security import api_keys

log = get_logger(__name__)

ACCESS_PREFIX = "mgo_"
REFRESH_PREFIX = "mgr_"
CODE_PREFIX = "mgc_"
SCOPE = "mcp"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=10)
REQUEST_TTL = timedelta(minutes=10)
MAX_CLIENTS = 200
MAX_PENDING_REQUESTS = 500
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
NAME_MAX = 100


def credential_id(client_id: str, user_id: Any) -> str:
    """Audit credential id and rate-limit key of one (app, user) pair (D98 #4)."""
    return f"oauth:{client_id}:{user_id}"


def clean_name(name: str | None) -> str:
    """Shown on the consent page: printable characters only, bounded."""
    cleaned = "".join(ch for ch in (name or "") if ch.isprintable()).strip()
    return cleaned[:NAME_MAX] or "Unnamed app"


def redirect_problem(uri: str) -> str | None:
    """Only https, or http to this machine, may receive a code (D98 #1)."""
    parsed = urlparse(uri)
    if parsed.fragment:
        return "redirect_uri must not have a fragment"
    if parsed.scheme == "https" and parsed.hostname:
        return None
    if parsed.scheme == "http" and (parsed.hostname or "") in LOOPBACK_HOSTS:
        return None
    return "redirect_uri must be https, or http on localhost"


def _new(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


class PgOAuthProvider:
    """Implements `OAuthAuthorizationServerProvider` over Postgres."""

    def __init__(self, rt: Runtime) -> None:
        self._rt = rt

    # -- helpers --
    def _hash(self, value: str) -> bytes:
        return api_keys.hash_key(value, self._rt.settings.api_key_pepper.get_secret_value())

    # -- clients (DCR) --
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT metadata FROM oauth_clients WHERE client_id = $1", client_id)
        return OAuthClientInformationFull.model_validate(row["metadata"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.token_endpoint_auth_method != "none":  # noqa: S105 (an auth method, not a secret)
            raise RegistrationError(
                "invalid_client_metadata",
                "Only public clients are supported: set token_endpoint_auth_method to 'none' "
                "(PKCE S256 is required)",
            )
        for uri in client_info.redirect_uris or []:
            if problem := redirect_problem(str(uri)):
                raise RegistrationError("invalid_redirect_uri", problem)
        client_info = client_info.model_copy(
            update={"client_name": clean_name(client_info.client_name), "client_secret": None}
        )
        async with self._rt.pool.acquire() as conn:
            if await conn.fetchval("SELECT count(*) FROM oauth_clients") >= MAX_CLIENTS:
                raise RegistrationError(
                    "invalid_client_metadata", "Too many registered apps; try again later"
                )
            await conn.execute(
                "INSERT INTO oauth_clients (client_id, metadata) VALUES ($1, $2)",
                client_info.client_id,
                client_info.model_dump(mode="json", exclude_none=True),
            )
        log.info("oauth client registered", client_id=client_info.client_id, name=client_info.client_name)

    # -- authorization: park the request, the consent page finishes it --
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        request_id = secrets.token_urlsafe(32)
        async with self._rt.pool.acquire() as conn:
            pending = await conn.fetchval("SELECT count(*) FROM oauth_requests WHERE expires_at > now()")
            if pending >= MAX_PENDING_REQUESTS:
                raise AuthorizeError(
                    "temporarily_unavailable", "Too many pending authorizations; try again soon"
                )
            await conn.execute(
                "INSERT INTO oauth_requests (id, client_id, params, expires_at) VALUES ($1, $2, $3, $4)",
                request_id,
                client.client_id,
                params.model_dump(mode="json"),
                datetime.now(UTC) + REQUEST_TTL,
            )
        return f"{self._rt.settings.public_dashboard_url.rstrip('/')}/oauth/consent?req={request_id}"

    # -- authorization code → tokens --
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM oauth_codes WHERE code_hash = $1 AND client_id = $2",
                self._hash(authorization_code),
                client.client_id,
            )
            if row is None:
                return None
            if (
                row["used_at"] is not None
            ):  # a replayed code: everything issued from it is revoked (RFC 6749 §4.1.2)
                await conn.execute(
                    "UPDATE oauth_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL",
                    row["family_id"],
                )
                log.warning("oauth code replayed, family revoked", client_id=client.client_id)
                return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=list(row["scopes"]),
            expires_at=row["expires_at"].timestamp(),
            client_id=row["client_id"],
            code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=row["redirect_uri_provided_explicitly"],
            resource=row["resource"],
            subject=str(row["user_id"]),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with self._rt.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE oauth_codes SET used_at = now() WHERE code_hash = $1 AND used_at IS NULL "
                "RETURNING user_id, family_id",
                self._hash(authorization_code.code),
            )
            if row is None:
                raise TokenError("invalid_grant", "authorization code already used")
            return await self._issue(
                conn, client.client_id, row["user_id"], row["family_id"], authorization_code.scopes,
                authorization_code.resource,
            )  # fmt: skip

    # -- refresh (rotating) --
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM oauth_tokens WHERE token_hash = $1 AND kind = 'refresh' AND client_id = $2",
                self._hash(refresh_token),
                client.client_id,
            )
            if row is None:
                return None
            if row["revoked_at"] is not None:  # a rotated-out token used again: treat the family as stolen
                await conn.execute(
                    "UPDATE oauth_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL",
                    row["family_id"],
                )
                log.warning("oauth refresh token reused, family revoked", client_id=client.client_id)
                return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            subject=str(row["user_id"]),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        async with self._rt.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE oauth_tokens SET revoked_at = now() "
                "WHERE token_hash = $1 AND kind = 'refresh' AND revoked_at IS NULL "
                "RETURNING user_id, family_id",
                self._hash(refresh_token.token),
            )
            if row is None:
                raise TokenError("invalid_grant", "refresh token was already used")
            status = await conn.fetchval("SELECT status FROM users WHERE id = $1", row["user_id"])
            if status != "active":
                raise TokenError("invalid_grant", "user is disabled")
            await conn.execute(  # the previous access token goes with the rotated refresh token
                "UPDATE oauth_tokens SET revoked_at = now() WHERE family_id = $1 AND kind = 'access' "
                "AND revoked_at IS NULL",
                row["family_id"],
            )
            return await self._issue(
                conn, client.client_id, row["user_id"], row["family_id"], scopes, refresh_token.resource
            )

    async def _issue(
        self, conn: Any, client_id: str, user_id: Any, family_id: Any, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        access, refresh = _new(ACCESS_PREFIX), _new(REFRESH_PREFIX)
        now = datetime.now(UTC)
        for kind, value, ttl in (("access", access, ACCESS_TTL), ("refresh", refresh, REFRESH_TTL)):
            await conn.execute(
                "INSERT INTO oauth_tokens (token_hash, kind, family_id, client_id, user_id, scopes, "
                "resource, expires_at) VALUES ($1, $2::oauth_token_kind, $3, $4, $5, $6, $7, $8)",
                self._hash(value), kind, family_id, client_id, user_id, scopes, resource, now + ttl,
            )  # fmt: skip
        return OAuthToken(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )

    # -- access tokens --
    async def load_access_token(self, token: str) -> AccessToken | None:
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT client_id, user_id, scopes, resource, expires_at FROM oauth_tokens "
                "WHERE token_hash = $1 AND kind = 'access' AND revoked_at IS NULL AND expires_at > now()",
                self._hash(token),
            )
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=list(row["scopes"]),
            expires_at=int(row["expires_at"].timestamp()),
            resource=row["resource"],
            subject=str(row["user_id"]),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """RFC 7009: revoking either token ends the whole authorization (access + refresh)."""
        async with self._rt.pool.acquire() as conn:
            await conn.execute(
                "UPDATE oauth_tokens SET revoked_at = now() WHERE revoked_at IS NULL AND family_id = "
                "(SELECT family_id FROM oauth_tokens WHERE token_hash = $1)",
                self._hash(token.token),
            )

    async def exchange_identity_assertion(  # pragma: no cover  (disabled: identity_assertion_enabled=False)
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        raise TokenError("unauthorized_client", "The identity-assertion grant is not supported")


def new_family() -> uuid.UUID:
    return uuid.uuid4()


def new_code() -> str:
    return _new(CODE_PREFIX)

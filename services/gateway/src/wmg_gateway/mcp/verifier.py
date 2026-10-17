"""`/mcp` bearer verification (DESIGN §7.1): API keys (`mgw_…`) and delegated chat JWTs. OAuth tokens: P17.

Returning None makes the SDK's RequireAuthMiddleware answer 401 `invalid_token`. When the credential is real
but not usable (revoked key, disabled user) we know who it was, so a `denied` audit row is written first
(§5.10).
"""

import time
import uuid
from typing import Any

from mcp.server.auth.provider import AccessToken

from wmg_gateway.audit.audit import AuditRecord, CredentialType
from wmg_gateway.request_context import request_meta
from wmg_gateway.runtime import Runtime
from wmg_gateway.security import api_keys
from wmg_gateway.security import jwt as delegated_jwt

API_KEY_TOKEN_TTL_S = 60


class WmgTokenVerifier:
    def __init__(self, runtime: Runtime) -> None:
        self._rt = runtime

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.startswith(api_keys.KEY_PREFIX):
            return await self._api_key(token)
        if delegated_jwt.looks_like_jwt(token):
            return await self._delegated(token)
        return None

    async def _api_key(self, key: str) -> AccessToken | None:
        prefix = api_keys.prefix_of(key)
        if prefix is None:
            return None
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT k.id, k.secret_hash, k.revoked_at, k.rate_limit_per_min, "
                "u.id AS user_id, u.email, u.role, u.status "
                "FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.prefix = $1",
                prefix,
            )
            pepper = self._rt.settings.api_key_pepper.get_secret_value()
            if row is None or not api_keys.verify(key, bytes(row["secret_hash"]), pepper):
                return None
            if row["revoked_at"] is not None or row["status"] != "active":
                reason = "API key revoked" if row["revoked_at"] is not None else "User disabled"
                await self._denied(row, "api_key", str(row["id"]), reason)
                return None
            await conn.execute(
                "UPDATE api_keys SET last_used_at = now() WHERE id = $1 "
                "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
                row["id"],
            )
        return self._access_token(key, row, "api_key", str(row["id"]), int(time.time()) + API_KEY_TOKEN_TTL_S)

    async def _delegated(self, token: str) -> AccessToken | None:
        delegation = delegated_jwt.decode(token, self._rt.settings.jwt_secret.get_secret_value())
        if delegation is None:
            return None
        try:
            user_id = uuid.UUID(delegation.user_id)
        except ValueError:
            return None
        async with self._rt.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id AS user_id, email, role, status FROM users WHERE id = $1", user_id
            )
        if row is None:
            return None
        # One chat credential per user (stable id, so per-credential rate limits apply per user).
        credential_id = f"chat:{row['user_id']}"
        if row["status"] != "active":
            await self._denied(row, "chat_agent", credential_id, "User disabled")
            return None
        return self._access_token(token, row, "chat_agent", credential_id, delegation.expires_at)

    @staticmethod
    def _access_token(
        token: str, row: Any, credential_type: CredentialType, credential_id: str, expires_at: int
    ) -> AccessToken:
        return AccessToken(
            token=token,
            client_id=credential_id,
            scopes=["mcp"],
            expires_at=expires_at,
            subject=str(row["user_id"]),
            claims={
                "email": row["email"],
                "role": row["role"],
                "credential_type": credential_type,
                "credential_id": credential_id,
                "rate_limit": row.get("rate_limit_per_min"),
            },
        )

    async def _denied(
        self, row: Any, credential_type: CredentialType, credential_id: str, reason: str
    ) -> None:
        meta = request_meta.get()
        await self._rt.audit.write(
            AuditRecord(
                method=(meta.mcp_method if meta else None) or "unknown",
                outcome="denied",
                duration_ms=0,
                user_id=str(row["user_id"]),
                user_email=row["email"],
                credential_type=credential_type,
                credential_id=credential_id,
                tool_name=None,
                exposed_name=meta.mcp_name if meta else None,
                error_message=reason,
                client_name=meta.user_agent[:100] if meta and meta.user_agent else None,
                request_id=meta.request_id if meta else None,
            )
        )

"""OAuth consent and Connected apps (DESIGN §15, D98): the dashboard's side of the MCP authorization flow.

The flow itself (`/authorize`, `/token`, `/register`, `/revoke`) is served by the SDK's authorization server
on the gateway host (`oauth/server.py`). Here, a signed-in user answers a pending request and manages the
apps they approved; admins can see and revoke everyone's.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request

from wmg_common.errors import AppError
from wmg_common.logging import get_logger
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser, Pool
from wmg_gateway.oauth import consent
from wmg_gateway.oauth.provider import clean_name
from wmg_gateway.runtime import Runtime

log = get_logger(__name__)
router = APIRouter(prefix="/oauth", tags=["oauth"])


class AuthorizationRequest(ApiModel):
    client_name: str
    redirect_host: str
    redirect_uri: str
    scopes: list[str]
    expires_at: datetime


class RequestEnvelope(ApiModel):
    request: AuthorizationRequest


class Redirect(ApiModel):
    redirect_url: str


class ConnectedApp(ApiModel):
    client_id: str
    client_name: str
    user_id: uuid.UUID
    user_email: str
    authorized_at: datetime
    last_authorized_at: datetime
    active_tokens: int


class AppsEnvelope(ApiModel):
    apps: list[ConnectedApp]


@router.get("/requests/{request_id}")
async def get_request(request_id: str, _user: CurrentUser, pool: Pool) -> RequestEnvelope:
    async with pool.acquire() as conn:
        return RequestEnvelope(request=AuthorizationRequest(**await consent.describe(conn, request_id)))


@router.post("/requests/{request_id}/approve")
async def approve_request(request_id: str, user: CurrentUser, request: Request, pool: Pool) -> Redirect:
    rt: Runtime = request.app.state.runtime
    async with pool.acquire() as conn:
        url = await consent.approve(rt, conn, request_id, user.id)
    log.info("oauth authorization approved", user_id=str(user.id))
    return Redirect(redirect_url=url)


@router.post("/requests/{request_id}/deny")
async def deny_request(request_id: str, user: CurrentUser, pool: Pool) -> Redirect:
    async with pool.acquire() as conn:
        url = await consent.deny(conn, request_id)
    log.info("oauth authorization denied", user_id=str(user.id))
    return Redirect(redirect_url=url)


@router.get("/apps")
async def list_apps(
    user: CurrentUser, pool: Pool, all_users: Annotated[int, Query(alias="all", ge=0, le=1)] = 0
) -> AppsEnvelope:
    """Apps with at least one live token: (user, app) pairs, newest first. `all=1` is admin-only."""
    if all_users and user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Admins only")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.client_id, c.metadata, u.id AS user_id, u.email, k.first_approved_at, "
            "k.last_approved_at, count(t.*) AS active FROM oauth_consents k "
            "JOIN oauth_clients c ON c.client_id = k.client_id JOIN users u ON u.id = k.user_id "
            "JOIN oauth_tokens t ON t.user_id = k.user_id AND t.client_id = k.client_id "
            "AND t.revoked_at IS NULL AND t.expires_at > now() "
            "WHERE ($1 OR k.user_id = $2) GROUP BY c.client_id, c.metadata, u.id, u.email, "
            "k.first_approved_at, k.last_approved_at ORDER BY k.last_approved_at DESC",
            bool(all_users),
            user.id,
        )
    return AppsEnvelope(
        apps=[
            ConnectedApp(
                client_id=r["client_id"],
                client_name=clean_name(r["metadata"].get("client_name")),
                user_id=r["user_id"],
                user_email=r["email"],
                authorized_at=r["first_approved_at"],
                last_authorized_at=r["last_approved_at"],
                active_tokens=r["active"],
            )
            for r in rows
        ]
    )


@router.delete("/apps/{client_id}", status_code=204)
async def revoke_app(
    client_id: str,
    user: CurrentUser,
    pool: Pool,
    user_id: Annotated[uuid.UUID | None, Query(alias="userId")] = None,
) -> None:
    """Ends one user's access for one app at once: every access and refresh token of the pair is revoked."""
    owner = user_id or user.id
    if owner != user.id and user.role != "admin":
        raise AppError(404, "NOT_FOUND", "App not found")  # no hint that another user's grant exists
    async with pool.acquire() as conn:
        known = await conn.fetchval(
            "SELECT 1 FROM oauth_consents WHERE user_id = $1 AND client_id = $2", owner, client_id
        )
        if not known:
            raise AppError(404, "NOT_FOUND", "App not found")
        await conn.execute(
            "UPDATE oauth_tokens SET revoked_at = now() "
            "WHERE user_id = $1 AND client_id = $2 AND revoked_at IS NULL",
            owner,
            client_id,
        )
    log.info("oauth app revoked", client_id=client_id, user_id=str(owner), by=str(user.id))

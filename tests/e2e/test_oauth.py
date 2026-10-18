"""TESTING §6 OA-01 (P17, D98): MCP OAuth against the running cluster (`make e2e-oauth`, ~30 s).

The SDK's own OAuth client discovers the authorization server, registers itself and is sent to the consent
page (its redirect handler plays the browser: it follows /authorize, then approves as the admin through the
dashboard API). It then lists and calls tools through the ingress; the audit shows `oauth`; revoking the app
in Connected apps ends access on the next request.
"""

import base64
import os
import subprocess
from urllib.parse import parse_qs, urlparse

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

pytestmark = [pytest.mark.e2e, pytest.mark.bonus]

SCHEME = os.environ.get("E2E_SCHEME", "http")
DOMAIN = os.environ.get("E2E_DOMAIN", "localhost")
DASHBOARD = f"{SCHEME}://dashboard.{DOMAIN}"
MCP_URL = f"{SCHEME}://gateway.{DOMAIN}/mcp"
CSRF = {"X-WMG-CSRF": "1"}
REDIRECT = "http://localhost:8765/callback"
APP = "OA-01 e2e app"


class Storage:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.info = client_info


def admin_password() -> str:
    cmd = ["kubectl", "get", "secret", "platform-credentials", "-n", "mcp-platform"]
    jsonpath = "jsonpath={.data.admin-password}"
    raw = subprocess.run([*cmd, "-o", jsonpath], capture_output=True, text=True, check=True)  # noqa: S603
    return base64.b64decode(raw.stdout).decode()


async def run_flow(admin: httpx2.AsyncClient, storage: Storage) -> list[str]:
    callback: dict[str, str] = {}
    seen: list[dict[str, object]] = []

    async def browser(authorization_url: str) -> None:
        async with httpx2.AsyncClient() as http:
            r = await http.get(authorization_url, follow_redirects=False)
        consent = urlparse(r.headers["location"])
        assert f"{consent.scheme}://{consent.netloc}" == DASHBOARD and consent.path == "/oauth/consent"
        req = parse_qs(consent.query)["req"][0]
        seen.append((await admin.get(f"/api/oauth/requests/{req}")).json()["request"])
        approved = await admin.post(f"/api/oauth/requests/{req}/approve")
        assert approved.status_code == 200, approved.text
        callback.update(
            {k: v[0] for k, v in parse_qs(urlparse(approved.json()["redirectUrl"]).query).items()}
        )

    async def wait_for_code() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=callback["code"], state=callback.get("state"))

    provider = OAuthClientProvider(
        server_url=MCP_URL,
        client_metadata=OAuthClientMetadata(
            client_name=APP,
            redirect_uris=[REDIRECT],  # type: ignore[list-item]
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="mcp",
        ),
        storage=storage,
        redirect_handler=browser,
        callback_handler=wait_for_code,
    )
    async with (
        httpx2.AsyncClient(auth=provider, timeout=60) as http,
        Client(streamable_http_client(MCP_URL, http_client=http), cache=None) as client,
    ):
        names = [t.name for t in (await client.list_tools()).tools]
        result = await client.call_tool("pincode__list_serviceable_cities", {})
        assert not result.is_error
    assert seen and seen[0]["clientName"] == APP
    return names


def test_oa01_oauth_flow_audit_and_revoke() -> None:
    async def scenario() -> None:
        async with httpx2.AsyncClient(base_url=DASHBOARD, headers=CSRF, timeout=60) as admin:
            login = {"email": "admin@example.com", "password": admin_password()}
            assert (await admin.post("/api/auth/login", json=login)).status_code == 200
            for app in (await admin.get("/api/oauth/apps")).json()[
                "apps"
            ]:  # leftovers from an interrupted run
                if app["clientName"] == APP:
                    await admin.delete(f"/api/oauth/apps/{app['clientId']}?userId={app['userId']}")
            storage = Storage()
            names = await run_flow(admin, storage)
            assert any(n.startswith("pincode__") for n in names) and any(
                n.startswith("woo-main__") for n in names
            )
            assert storage.tokens is not None and storage.tokens.access_token.startswith("mgo_")

            apps = [a for a in (await admin.get("/api/oauth/apps")).json()["apps"] if a["clientName"] == APP]
            assert len(apps) == 1 and apps[0]["activeTokens"] == 2
            audit = (await admin.get("/api/audit?limit=20")).json()["items"]
            mine = [
                a
                for a in audit
                if a["toolName"] == "list_serviceable_cities" and a["credentialType"] == "oauth"
            ]
            assert mine and mine[0]["userEmail"] == "admin@example.com" and mine[0]["outcome"] == "success"

            headers = {
                "Authorization": f"Bearer {storage.tokens.access_token}",
                "Accept": "application/json, text/event-stream",
            }
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            async with httpx2.AsyncClient() as http:
                assert (await http.post(MCP_URL, json=body, headers=headers)).status_code == 200
                revoked = await admin.delete(
                    f"/api/oauth/apps/{apps[0]['clientId']}?userId={apps[0]['userId']}"
                )
                assert revoked.status_code == 204
                assert (
                    await http.post(MCP_URL, json=body, headers=headers)
                ).status_code == 401  # the very next request
            assert [
                a for a in (await admin.get("/api/oauth/apps")).json()["apps"] if a["clientName"] == APP
            ] == []

    anyio.run(scenario)

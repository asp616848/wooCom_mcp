"""GET /api/connect-info (DESIGN §7.3): the gateway URL and ready-to-paste client snippets.

Snippets carry the placeholder `<YOUR_API_KEY>`; the dashboard substitutes a freshly created key client-side,
so a key never travels back from the server after creation.
"""

import json

from fastapi import APIRouter, Request

from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser

router = APIRouter(tags=["connect"])

KEY_PLACEHOLDER = "<YOUR_API_KEY>"
SERVER_NAME = "woo-gateway"
INSPECTOR = "npx @modelcontextprotocol/inspector@2.10.1"


class Snippets(ApiModel):
    claude_code: str
    cursor: str
    inspector: str


class ConnectInfo(ApiModel):
    mcp_url: str
    search_mcp_url: str | None  # set once the search endpoint exists (SO-4, P15)
    snippets: Snippets


def build_snippets(mcp_url: str) -> Snippets:
    auth = f"Bearer {KEY_PLACEHOLDER}"
    cursor = {"mcpServers": {SERVER_NAME: {"url": mcp_url, "headers": {"Authorization": auth}}}}
    return Snippets(
        claude_code=(
            f'claude mcp add --transport http {SERVER_NAME} {mcp_url} --header "Authorization: {auth}"'
        ),
        cursor=json.dumps(cursor, indent=2),
        inspector=(
            f"{INSPECTOR}\n"
            "# In the Inspector UI: Transport = Streamable HTTP\n"
            f"# URL = {mcp_url}\n"
            f"# Header: Authorization = {auth}"
        ),
    )


@router.get("/connect-info")
async def connect_info(_user: CurrentUser, request: Request) -> ConnectInfo:
    mcp_url = request.app.state.settings.public_mcp_url
    return ConnectInfo(mcp_url=mcp_url, search_mcp_url=None, snippets=build_snippets(mcp_url))

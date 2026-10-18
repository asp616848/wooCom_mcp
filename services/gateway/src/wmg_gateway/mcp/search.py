"""Search mode (DESIGN §5.9, SO-4, D93): `/mcp/search` exposes three fixed meta-tools instead of every tool.

`search_tools` ranks the caller-visible tools for a query, `call_tool` runs one by its exposed name through
the same path as `/mcp` (auth, policy, routing, audit with `client_mode='search'`), `list_servers` lists the
live servers. Clients with hundreds of tools keep a short tool list and look up what they need.
"""

import json
import re
from typing import Any

import mcp_types as types
from mcp import MCPError
from mcp.server import Server
from starlette.applications import Starlette

from wmg_gateway import APP_VERSION
from wmg_gateway.catalog.catalog import Catalog, ToolEntry
from wmg_gateway.mcp.call_tool import call_exposed
from wmg_gateway.mcp.caller import caller_from
from wmg_gateway.mcp.servers import transport_security
from wmg_gateway.mcp.verifier import WmgTokenVerifier
from wmg_gateway.oauth.server import auth_settings
from wmg_gateway.runtime import Runtime

SEARCH_PATH = "/mcp/search"
DEFAULT_LIMIT = 8
SCHEMA_DEPTH = 4

META_TOOLS = [
    types.Tool(
        name="search_tools",
        title="Search tools",
        description="Find gateway tools for a task. Returns the best matches with their input schemas; "
        "run one with call_tool.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200, "description": "What to do"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": DEFAULT_LIMIT},
            },
            "required": ["query"],
        },
        annotations=types.ToolAnnotations(read_only_hint=True),
    ),
    types.Tool(
        name="call_tool",
        title="Call a tool",
        description="Run a tool found with search_tools, by its exact name, with arguments matching its "
        "schema.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "description": "Exact name from search_tools"},
                "arguments": {"type": "object", "description": "Arguments for that tool"},
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="list_servers",
        title="List servers",
        description="The connected servers you can use, with how many tools each offers.",
        input_schema={"type": "object", "properties": {}},
        annotations=types.ToolAnnotations(read_only_hint=True),
    ),
]
_SCHEMAS = {t.name: t.input_schema for t in META_TOOLS}


def tokens(text: str | None) -> set[str]:
    """Lowercase words split on non-alphanumerics; a trailing `s` is dropped from words longer than 3."""
    words = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w}


def schema_text(schema: Any, depth: int = 0) -> str:
    """Property names, descriptions and string enum values of an input schema (D93)."""
    if depth > SCHEMA_DEPTH or not isinstance(schema, dict):
        return ""
    parts: list[str] = []
    if isinstance(schema.get("description"), str):
        parts.append(schema["description"])
    parts.extend(v for v in schema.get("enum") or [] if isinstance(v, str))
    for name, sub in (schema.get("properties") or {}).items():
        parts.append(name)
        parts.append(schema_text(sub, depth + 1))
    for key in ("items", "anyOf", "oneOf"):
        value = schema.get(key)
        for sub in value if isinstance(value, list) else [value]:
            parts.append(schema_text(sub, depth + 1))
    return " ".join(p for p in parts if p)


def score(entry: ToolEntry, query: set[str]) -> int:
    """3 per query word in the name, 2 in the title, 1 in the description, 1 in the input schema."""
    fields = (
        (3, tokens(entry.exposed)),
        (2, tokens(entry.title)),
        (1, tokens(entry.description)),
        (1, tokens(schema_text(entry.input_schema))),
    )
    return sum(weight * len(query & words) for weight, words in fields)


def search(catalog: Catalog, role: str, query: str, limit: int = DEFAULT_LIMIT) -> list[ToolEntry]:
    wanted = tokens(query)
    scored = [(score(t, wanted), t) for t in catalog.visible_tools(role)]
    ranked = sorted(((s, t) for s, t in scored if s > 0), key=lambda st: (-st[0], st[1].exposed))
    return [t for _, t in ranked[:limit]]


def _json_result(data: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
        structured_content=data,
    )


def _invalid(message: str) -> MCPError:
    return MCPError(types.INVALID_PARAMS, message)


async def call_meta(rt: Runtime, ctx: Any, name: str, args: dict[str, Any]) -> types.CallToolResult:
    role = caller_from(ctx).role
    catalog = rt.catalog.current
    if name == "search_tools":
        query, limit = args.get("query"), args.get("limit", DEFAULT_LIMIT)
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise _invalid("query must be a string of 1-200 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
            raise _invalid("limit must be an integer from 1 to 20")
        found = search(catalog, role, query, limit)
        return _json_result(
            {
                "tools": [
                    {
                        "name": t.exposed,
                        "description": t.exposed_tool.description,
                        "inputSchema": t.input_schema,
                    }
                    for t in found
                ]
            }
        )
    if name == "call_tool":
        tool, arguments = args.get("name"), args.get("arguments") or {}
        if not isinstance(tool, str) or not tool:
            raise _invalid("name must be a tool name from search_tools")
        if not isinstance(arguments, dict):
            raise _invalid("arguments must be an object")
        return await call_exposed(rt, ctx, tool, arguments, mode="search")
    if name == "list_servers":
        counts: dict[str, int] = {}
        servers = {}
        for t in catalog.visible_tools(role):
            counts[t.server.slug] = counts.get(t.server.slug, 0) + 1
            servers[t.server.slug] = t.server.name
        return _json_result(
            {"servers": [{"slug": s, "name": servers[s], "toolCount": counts[s]} for s in sorted(counts)]}
        )
    raise _invalid(f"Unknown tool: {name}")


def build_search_server(rt: Runtime) -> tuple[Server[Any], Starlette]:
    async def on_list_tools(_ctx: Any, _params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=META_TOOLS)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await call_meta(rt, ctx, params.name, params.arguments or {})

    server: Server[Any] = Server(
        "wmg-gateway-search",
        version=APP_VERSION,
        title="Woo MCP Gateway (search mode)",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        get_tool_input_schema=_SCHEMAS.get,
    )
    app = server.streamable_http_app(
        streamable_http_path=SEARCH_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=transport_security(rt),
        auth=auth_settings(rt, rt.settings.public_mcp_url.rstrip("/") + "/search"),
        token_verifier=WmgTokenVerifier(rt),
    )
    return server, app

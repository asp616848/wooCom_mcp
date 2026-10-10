"""`tools/list` (DESIGN §5.4): the caller-visible tools from the in-memory catalog, sorted, one page.

`ttlMs`/`cacheScope` are filled by the Server's `cache_hints`.
"""

from typing import Any

import mcp_types as types

from wmg_gateway.mcp.caller import caller_from
from wmg_gateway.runtime import Runtime


async def list_tools(rt: Runtime, ctx: Any, _params: Any) -> types.ListToolsResult:
    caller = caller_from(ctx)
    return types.ListToolsResult(
        tools=[t.exposed_tool for t in rt.catalog.current.visible_tools(caller.role)]
    )

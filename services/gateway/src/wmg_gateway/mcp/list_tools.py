"""`tools/list` (DESIGN §5.4): the caller-visible tools from the in-memory catalog, sorted, one page.

`ttlMs`/`cacheScope` are filled by the Server's `cache_hints`.
"""

from typing import Any

import mcp_types as types
from prometheus_client import Histogram

from wmg_gateway.mcp.caller import caller_from
from wmg_gateway.runtime import Runtime

TOOLS_LIST_SECONDS = Histogram("wmg_tools_list_duration_seconds", "tools/list duration")


async def list_tools(rt: Runtime, ctx: Any, _params: Any) -> types.ListToolsResult:
    with TOOLS_LIST_SECONDS.time():
        caller = caller_from(ctx)
        return types.ListToolsResult(
            tools=[t.exposed_tool for t in rt.catalog.current.visible_tools(caller.role)]
        )

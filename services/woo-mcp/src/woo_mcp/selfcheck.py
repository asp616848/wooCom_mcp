"""`python -m woo_mcp.selfcheck` — the store's `helm test` (DESIGN §4.4, §9.1).

1. signed `GET /system_status` and `GET /products?per_page=1` straight to the store
2. `get_store_info` through the MCP server in-process
Exit code 0 = store, REST key and woo-mcp all work.
"""

import sys

import anyio
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from wmg_common.logging import configure_logging, get_logger
from wmg_common.settings import load_settings
from woo_mcp.settings import WooMcpSettings
from woo_mcp.tools import build_server
from woo_mcp.wc import WooClient

log = get_logger("woo_mcp.selfcheck")


async def run(client: WooClient) -> bool:
    try:
        status = await client.request("GET", "/system_status", entity="system status")
        env = status.data.get("environment") or {}
        log.info("system_status ok", woocommerce=env.get("version"), wordpress=env.get("wp_version"))
        products = await client.request("GET", "/products", query={"per_page": 1}, entity="products")
        log.info("products ok", total=products.total)
    except ToolError as exc:
        log.error("store check failed", error=str(exc))
        return False

    async with Client(build_server(client)) as mcp:
        result = await mcp.call_tool("get_store_info", {})
    if result.is_error:
        log.error("get_store_info failed", content=[getattr(b, "text", "") for b in result.content])
        return False
    log.info("get_store_info ok", info=result.structured_content)
    return True


async def main() -> int:
    settings = load_settings(WooMcpSettings)
    configure_logging("woo-mcp-selfcheck", settings.log_level)
    client = WooClient(settings)
    try:
        ok = await run(client)
    finally:
        await client.aclose()
    log.info("SELFCHECK PASSED" if ok else "SELFCHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(anyio.run(main))

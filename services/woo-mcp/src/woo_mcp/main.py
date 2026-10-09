"""ASGI entrypoint: `uvicorn woo_mcp.main:app` (DESIGN §9.0-9.1)."""

from wmg_common.logging import configure_logging
from wmg_common.mcp_app import build_upstream_app
from wmg_common.settings import load_settings
from woo_mcp.settings import WooMcpSettings
from woo_mcp.tools import build_server
from woo_mcp.wc import WooClient

settings = load_settings(WooMcpSettings)
configure_logging("woo-mcp", settings.log_level)

# Readiness does not depend on the store (DESIGN §4.4): `helm test` proves the store end to end.
app = build_upstream_app(build_server(WooClient(settings)), settings)

"""ASGI entrypoint: `uvicorn mcp_gst.main:app` (DESIGN §9.0, §9.2)."""

from mcp_gst.settings import GstSettings
from mcp_gst.tools import build_server
from wmg_common.logging import configure_logging
from wmg_common.mcp_app import build_upstream_app
from wmg_common.settings import load_settings

settings = load_settings(GstSettings)
configure_logging("mcp-gst", settings.log_level)

app = build_upstream_app(build_server(settings.seller_state), settings)

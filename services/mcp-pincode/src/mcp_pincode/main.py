"""ASGI entrypoint: `uvicorn mcp_pincode.main:app` (DESIGN §9.0, §9.3)."""

from mcp_pincode.data import load_places
from mcp_pincode.settings import PincodeSettings
from mcp_pincode.tools import build_server
from wmg_common.logging import configure_logging
from wmg_common.mcp_app import build_upstream_app
from wmg_common.settings import load_settings

settings = load_settings(PincodeSettings)
configure_logging("mcp-pincode", settings.log_level)

try:
    server = build_server(load_places(), settings.origin_pincode)
except ValueError as exc:
    raise SystemExit(f"Invalid configuration (PincodeSettings):\n  ORIGIN_PINCODE: {exc}") from None

app = build_upstream_app(server, settings)

"""ASGI entrypoint: `uvicorn wmg_gateway.main:app` (DESIGN §5.1, §5.3)."""

from wmg_common.logging import configure_logging
from wmg_common.settings import load_settings
from wmg_gateway.http.app import create_app
from wmg_gateway.settings import Settings

settings = load_settings(Settings)
configure_logging("gateway", settings.log_level)

app = create_app(settings)

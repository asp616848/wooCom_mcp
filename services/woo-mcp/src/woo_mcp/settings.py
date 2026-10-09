"""woo-mcp configuration from env (DESIGN §9.1). Secrets stay `SecretStr` so they never print."""

from pydantic import Field, SecretStr

from wmg_common.settings import McpServerSettings


class WooMcpSettings(McpServerSettings):
    store_slug: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    # Where we connect (in-cluster Service) vs. the store's public host (WP_HOME): the public host is
    # sent as the Host header and is part of the OAuth signature base string (TRACKER "P3 evidence").
    wc_internal_url: str = Field(pattern=r"^https?://[^/\s]+$")
    wc_public_host: str = Field(pattern=r"^[A-Za-z0-9.-]+(:\d{1,5})?$")
    wc_consumer_key: SecretStr = Field(min_length=10)
    wc_consumer_secret: SecretStr = Field(min_length=10)
    store_timeout_ms: int = Field(default=10000, ge=1000, le=60000)

"""mcp-pincode configuration from env (DESIGN §9.3)."""

from pydantic import Field

from wmg_common.settings import McpServerSettings


class PincodeSettings(McpServerSettings):
    # Where parcels ship from; must be one of the bundled PIN codes (checked at startup).
    origin_pincode: str = Field(default="400001", pattern=r"^[1-9][0-9]{5}$")

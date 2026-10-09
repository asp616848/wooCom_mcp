"""mcp-gst configuration from env (DESIGN §9.2)."""

from pydantic import Field

from wmg_common.settings import McpServerSettings


class GstSettings(McpServerSettings):
    # Two-letter state code of the seller; decides intra-state (CGST+SGST) vs inter-state (IGST).
    seller_state: str = Field(default="MH", pattern=r"^[A-Z]{2}$")

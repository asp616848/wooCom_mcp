"""Environment-driven settings (pydantic-settings). Invalid env stops the process with a readable message
that names the variables but never echoes their values (they may be secrets)."""

from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Settings every service has. Field names map to upper-case env vars (`log_level` → `LOG_LEVEL`)."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    port: int = Field(default=8080, ge=1, le=65535)
    log_level: Literal["debug", "info", "warning", "error"] = "info"


class McpServerSettings(ServiceSettings):
    """Settings shared by the MCP servers behind the gateway (DESIGN §9.0)."""

    mcp_auth_mode: Literal["bearer", "header"] = "bearer"
    mcp_auth_token: SecretStr = Field(min_length=16)
    mcp_auth_header: str = Field(default="X-Api-Key", pattern=r"^[A-Za-z0-9-]{1,64}$")


def load_settings[S: BaseSettings](cls: type[S]) -> S:
    """Build `cls` from the environment; on invalid config exit with one line per problem."""
    try:
        return cls()
    except ValidationError as exc:
        lines = [f"Invalid configuration ({cls.__name__}):"]
        for err in exc.errors(include_input=False):
            name = "_".join(str(part) for part in err["loc"]).upper() or "(settings)"
            lines.append(f"  {name}: {err['msg']}")
        raise SystemExit("\n".join(lines)) from None

"""Gateway configuration from env (DESIGN §5.2). Invalid env → the process exits with a readable message.

Settings for later phases (health/timeouts P8, LLM P10, provisioning P14) are added with those phases.
"""

import base64
import binascii
from pathlib import Path

from pydantic import Field, SecretStr, field_validator

from wmg_common.settings import ServiceSettings

DEFAULT_ALLOWED_HOSTS = (
    "dashboard.localhost,gateway.localhost,localhost,127.0.0.1,"
    "platform-gateway,platform-gateway.mcp-platform.svc.cluster.local"
)


class Settings(ServiceSettings):
    public_dashboard_url: str = "http://dashboard.localhost"
    public_gateway_url: str = "http://gateway.localhost"
    public_mcp_url: str = "http://gateway.localhost/mcp"
    allowed_hosts: str = DEFAULT_ALLOWED_HOSTS

    database_url: SecretStr
    db_pool_min: int = Field(default=1, ge=1, le=20)
    db_pool_max: int = Field(default=10, ge=1, le=50)

    api_key_pepper: SecretStr = Field(min_length=32)
    credential_encryption_key: SecretStr
    jwt_secret: SecretStr = Field(min_length=32)

    bootstrap_admin_email: str = Field(default="admin@example.com", pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    bootstrap_admin_password: SecretStr = Field(min_length=12)
    seed_servers_file: Path = Path("/config/seed-servers.json")
    seed_credentials_dir: Path = Path("/seed-credentials")

    cookie_secure: bool = False

    @field_validator("credential_encryption_key")
    @classmethod
    def _key_is_32_bytes(cls, value: SecretStr) -> SecretStr:
        try:
            raw = base64.b64decode(value.get_secret_value(), validate=True)
        except binascii.Error, ValueError:
            raise ValueError("must be base64") from None
        if len(raw) != 32:
            raise ValueError("must decode to exactly 32 bytes")
        return value

    def encryption_key(self) -> bytes:
        return base64.b64decode(self.credential_encryption_key.get_secret_value())

    def host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

"""Gateway configuration from env (DESIGN §5.2). Invalid env → the process exits with a readable message.

Each phase adds its own settings (P14: store provisioning).
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

    # P8: upstream health, timeouts and limits (DESIGN §5.2, D36, D37)
    health_interval_ms: int = Field(default=10000, ge=1000, le=300000)
    health_timeout_ms: int = Field(default=3000, ge=500, le=60000)
    default_tool_timeout_ms: int = Field(default=20000, ge=1000, le=120000)
    upstream_max_inflight: int = Field(default=16, ge=1, le=256)
    max_result_bytes: int = Field(default=1_000_000, ge=1024)
    rate_limit_per_min: int = Field(default=0, ge=0)

    # P10: chat agent (DESIGN §12, D74). No key → POST /api/chat answers 503 (run `make secrets-llm`).
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_model: str = "gemini-3-flash-preview"
    llm_api_key: SecretStr | None = None
    llm_api_key_fallback: SecretStr | None = None  # D92: one retry on this key when the primary fails
    tool_search_threshold: int = Field(default=40, ge=1)

    # P14: store provisioning (DESIGN §13). Empty images → disabled (POST /api/stores answers 503).
    k8s_namespace: str = "mcp-platform"
    deployer_image: str = ""
    woo_mcp_image: str = ""
    store_host_template: str = Field(default="{name}.shop.localhost", pattern=r"\{name\}")
    store_scheme: str = Field(default="http", pattern=r"^https?$")
    store_values_env: str = Field(default="local", pattern=r"^(local|prod)$")
    max_stores_per_user: int = Field(default=2, ge=0, le=50)
    max_stores_total: int = Field(default=4, ge=0, le=100)
    provision_timeout_s: int = Field(default=900, ge=120, le=3600)
    teardown_timeout_s: int = Field(default=600, ge=60, le=3600)
    provision_concurrency: int = Field(default=3, ge=1, le=10)
    reconcile_interval_s: float = Field(default=5.0, gt=0, le=60)

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

    def llm_key(self) -> str | None:
        key = self.llm_api_key.get_secret_value().strip() if self.llm_api_key else ""
        return key or None

    def llm_keys(self) -> list[str]:
        """Primary key first, then the fallback (D92); empty when chat is not configured."""
        primary = self.llm_key()
        if primary is None:
            return []
        fallback = self.llm_api_key_fallback.get_secret_value().strip() if self.llm_api_key_fallback else ""
        return [primary, fallback] if fallback and fallback != primary else [primary]

    def provisioning_enabled(self) -> bool:
        return bool(self.deployer_image and self.woo_mcp_image)

    def store_host(self, name: str) -> str:
        return self.store_host_template.replace("{name}", name)

    def host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

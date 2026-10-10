"""First-boot data (DESIGN §6 "Bootstrap").

Creates the admin user, the seed servers (credentials encrypted) and the Woo write-tool policies (D57).
Runs inside the migration lock and only once (`app_state['bootstrap_done']`), so later deletions stick.
"""

import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel

from wmg_common.logging import get_logger
from wmg_gateway.security.crypto import CredentialCipher
from wmg_gateway.security.passwords import hash_password
from wmg_gateway.settings import Settings

log = get_logger(__name__)

SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,29}$"
# D57: exactly the PDF example — members read; only admins create products or change order status.
WOO_ADMIN_ONLY_TOOLS = ("create_product", "update_product", "update_order_status")


class SeedServer(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True, validate_by_alias=True)

    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=80)
    url: str
    auth_type: Literal["none", "bearer", "header"] = "none"
    auth_header_name: str | None = None


def load_seed_servers(path: Path) -> list[SeedServer]:
    if not path.exists():
        log.warning("seed servers file missing", path=str(path))
        return []
    return TypeAdapter(list[SeedServer]).validate_json(path.read_text(encoding="utf-8"))


def read_seed_credential(directory: Path, slug: str) -> str | None:
    """`<dir>/<slug>/credential` comes from an optional projected Secret (`make seed-credentials`)."""
    file = directory / slug / "credential"
    if not file.is_file():
        return None
    value = file.read_text(encoding="utf-8").strip()
    return value or None


async def bootstrap(conn: Any, settings: Settings, cipher: CredentialCipher) -> bool:
    """Returns True if it ran, False if bootstrap was already done."""
    if await conn.fetchval("SELECT 1 FROM app_state WHERE key = 'bootstrap_done'"):
        return False
    admin_id = await conn.fetchval(
        "INSERT INTO users (email, name, role, password_hash) VALUES ($1, 'Admin', 'admin', $2) "
        "ON CONFLICT (email) DO UPDATE SET email = EXCLUDED.email RETURNING id",
        settings.bootstrap_admin_email.lower(),
        hash_password(settings.bootstrap_admin_password.get_secret_value()),
    )
    seeded: list[dict[str, Any]] = []
    for server in load_seed_servers(settings.seed_servers_file):
        server_id = str(uuid.uuid4())
        credential = read_seed_credential(settings.seed_credentials_dir, server.slug)
        if server.auth_type != "none" and credential is None:
            log.warning("seed server has no credential yet", slug=server.slug)
        await conn.execute(
            "INSERT INTO mcp_servers "
            "(id, slug, name, url, auth_type, auth_header_name, credential_enc, source, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, 'seed', $8)",
            uuid.UUID(server_id),
            server.slug,
            server.name,
            server.url,
            server.auth_type,
            server.auth_header_name,
            cipher.encrypt(credential, server_id) if credential else None,
            admin_id,
        )
        if server.slug.startswith("woo-"):
            await conn.executemany(
                "INSERT INTO tool_policies (server_id, tool_name, admin_only, updated_by) "
                "VALUES ($1, $2, true, $3)",
                [(uuid.UUID(server_id), tool, admin_id) for tool in WOO_ADMIN_ONLY_TOOLS],
            )
        seeded.append({"slug": server.slug, "has_credential": credential is not None})
    await conn.execute(
        "INSERT INTO app_state (key, value) VALUES ('bootstrap_done', $1)",
        {"admin": settings.bootstrap_admin_email.lower(), "servers": seeded},
    )
    log.info("bootstrap done", admin=settings.bootstrap_admin_email.lower(), servers=seeded)
    return True

"""API keys for MCP clients (D52): `mgw_<8 base62 prefix>_<32 base62 secret>`.

Stored as the visible prefix (`mgw_xxxxxxxx`, unique, used for lookup) plus HMAC-SHA256(pepper, full key).
The plaintext key is shown once at creation and never stored.
"""

import hashlib
import hmac
import re
import secrets
import string

KEY_PREFIX = "mgw_"
_BASE62 = string.ascii_letters + string.digits
KEY_PATTERN = re.compile(r"^mgw_([A-Za-z0-9]{8})_([A-Za-z0-9]{32})$")


def _random(length: int) -> str:
    return "".join(secrets.choice(_BASE62) for _ in range(length))


def generate() -> tuple[str, str]:
    """Returns (full key, stored prefix)."""
    prefix = KEY_PREFIX + _random(8)
    return f"{prefix}_{_random(32)}", prefix


def prefix_of(key: str) -> str | None:
    """The lookup prefix, or None if the string is not a well-formed key."""
    match = KEY_PATTERN.match(key)
    return KEY_PREFIX + match.group(1) if match else None


def hash_key(key: str, pepper: str) -> bytes:
    return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).digest()


def verify(key: str, stored_hash: bytes, pepper: str) -> bool:
    return hmac.compare_digest(hash_key(key, pepper), stored_hash)

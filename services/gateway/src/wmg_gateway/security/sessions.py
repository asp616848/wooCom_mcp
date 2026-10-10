"""Dashboard session tokens (D51): random cookie value, only its SHA-256 is stored."""

import hashlib
import secrets
from datetime import timedelta

COOKIE_NAME = "wmg_session"
SESSION_TTL = timedelta(hours=12)
# Sliding expiry: extend only when less than this much time is left, so most requests don't write.
REFRESH_BELOW = timedelta(hours=11)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()

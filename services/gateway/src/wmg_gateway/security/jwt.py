"""Delegated JWT for the chat agent (D53): HS256, `aud=wmg-mcp`, `iss=wmg-gateway`, `via=chat-agent`, 300 s.

The chat agent calls the gateway's own `/mcp` with it, so chat tool calls take the same auth → policy → audit
path.
"""

import time
import uuid
from dataclasses import dataclass

import jwt

AUDIENCE = "wmg-mcp"
ISSUER = "wmg-gateway"
TTL_S = 300


@dataclass(frozen=True)
class Delegation:
    user_id: str
    expires_at: int
    jti: str


def mint(user_id: str, secret: str, ttl_s: int = TTL_S) -> str:
    now = int(time.time())
    claims = {
        "sub": user_id,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "via": "chat-agent",
        "iat": now,
        "exp": now + ttl_s,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def decode(token: str, secret: str) -> Delegation | None:
    """None for any invalid token (bad signature, expired, wrong audience/issuer, missing claims)."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "exp", "aud", "iss", "jti"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("via") != "chat-agent":
        return None
    return Delegation(user_id=str(claims["sub"]), expires_at=int(claims["exp"]), jti=str(claims["jti"]))


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2

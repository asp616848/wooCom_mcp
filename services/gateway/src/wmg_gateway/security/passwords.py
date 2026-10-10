"""Password hashing with stdlib scrypt (D50): `scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>`."""

import base64
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 16384, 8, 1, 64


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Parameters come from the stored string, so older hashes keep verifying if defaults change."""
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


# Verified against when the email is unknown, so "no such user" costs the same time as "wrong password".
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

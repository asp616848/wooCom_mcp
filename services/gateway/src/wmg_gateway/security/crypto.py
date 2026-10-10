"""Upstream credentials encrypted at rest (D54).

AES-256-GCM; stored as `v1:` + base64(nonce ‖ ciphertext ‖ tag); AAD = server id, so a value copied
onto another row fails to decrypt.
"""

import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "v1:"


class CredentialCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("AES-256 key must be 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, server_id: str) -> str:
        nonce = secrets.token_bytes(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), server_id.encode())
        return PREFIX + base64.b64encode(nonce + sealed).decode()

    def decrypt(self, token: str, server_id: str) -> str:
        """Raises `cryptography.exceptions.InvalidTag` if the value, key or server id don't match."""
        if not token.startswith(PREFIX):
            raise ValueError("unknown credential format")
        raw = base64.b64decode(token[len(PREFIX) :])
        return self._aead.decrypt(raw[:12], raw[12:], server_id.encode()).decode()

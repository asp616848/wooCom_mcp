"""TEST-U-02."""

import base64

import pytest
from cryptography.exceptions import InvalidTag

from wmg_gateway.security.crypto import CredentialCipher

KEY = bytes(range(32))


def test_round_trip_with_prefix() -> None:
    c = CredentialCipher(KEY)
    token = c.encrypt("s3cret-token", "server-1")
    assert token.startswith("v1:") and "s3cret" not in token
    assert c.decrypt(token, "server-1") == "s3cret-token"
    assert c.encrypt("same", "a") != c.encrypt("same", "a")  # random nonce


def test_wrong_aad_or_tamper_or_key_fails() -> None:
    c = CredentialCipher(KEY)
    token = c.encrypt("s3cret-token", "server-1")
    with pytest.raises(InvalidTag):
        c.decrypt(token, "server-2")
    raw = bytearray(base64.b64decode(token[3:]))
    raw[-1] ^= 1
    with pytest.raises(InvalidTag):
        c.decrypt("v1:" + base64.b64encode(bytes(raw)).decode(), "server-1")
    with pytest.raises(InvalidTag):
        CredentialCipher(bytes(32)).decrypt(token, "server-1")
    with pytest.raises(ValueError, match="format"):
        c.decrypt("v0:abc", "server-1")

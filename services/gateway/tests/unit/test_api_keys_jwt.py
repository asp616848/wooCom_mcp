"""TEST-U-03 (api keys) and TEST-U-05 (delegated JWT)."""

import time

import jwt as pyjwt

from wmg_gateway.security import api_keys
from wmg_gateway.security import jwt as djwt

PEPPER = "p" * 40
SECRET = "s" * 40


def test_U03_format_and_prefix() -> None:
    key, prefix = api_keys.generate()
    assert api_keys.KEY_PATTERN.match(key)
    assert prefix.startswith("mgw_") and len(prefix) == 12 and key.startswith(prefix + "_")
    assert api_keys.prefix_of(key) == prefix
    assert api_keys.prefix_of("mgw_short_x") is None
    assert api_keys.prefix_of("Bearer " + key) is None
    assert api_keys.generate()[0] != key


def test_U03_hmac_verify() -> None:
    key, _ = api_keys.generate()
    stored = api_keys.hash_key(key, PEPPER)
    assert len(stored) == 32 and key.encode() not in stored
    assert api_keys.verify(key, stored, PEPPER)
    assert not api_keys.verify(key[:-1] + ("A" if key[-1] != "A" else "B"), stored, PEPPER)
    assert not api_keys.verify(key, stored, "q" * 40)


def test_U05_valid_round_trip() -> None:
    token = djwt.mint("user-1", SECRET)
    assert djwt.looks_like_jwt(token)
    got = djwt.decode(token, SECRET)
    assert got is not None and got.user_id == "user-1" and got.expires_at > time.time() and got.jti


def test_U05_expired_wrong_claims_tampered() -> None:
    assert djwt.decode(djwt.mint("u", SECRET, ttl_s=-5), SECRET) is None
    assert djwt.decode(djwt.mint("u", SECRET), "x" * 40) is None
    now = int(time.time())
    base = {"sub": "u", "iat": now, "exp": now + 60, "jti": "j", "via": "chat-agent"}
    assert djwt.decode(pyjwt.encode({**base, "aud": "other", "iss": djwt.ISSUER}, SECRET), SECRET) is None
    assert djwt.decode(pyjwt.encode({**base, "aud": djwt.AUDIENCE, "iss": "evil"}, SECRET), SECRET) is None
    no_via = {**base, "aud": djwt.AUDIENCE, "iss": djwt.ISSUER, "via": "other"}
    assert djwt.decode(pyjwt.encode(no_via, SECRET), SECRET) is None
    head, body, sig = djwt.mint("u", SECRET).split(".")
    assert djwt.decode(f"{head}.{body}x.{sig}", SECRET) is None
    none_alg = pyjwt.encode({**base, "aud": djwt.AUDIENCE, "iss": djwt.ISSUER}, None, algorithm="none")
    assert djwt.decode(none_alg, SECRET) is None

"""TEST-U-04."""

from wmg_gateway.security.passwords import hash_password, verify_password


def test_hash_verifies_and_encodes_params() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)


def test_salted_and_garbage_safe() -> None:
    assert hash_password("x") != hash_password("x")
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "bcrypt$1$2$3$a$b")

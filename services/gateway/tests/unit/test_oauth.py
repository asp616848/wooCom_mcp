"""SO-3 pure rules (D98): redirect URI policy, issuer validation, display names, credential ids."""

import pytest
from pydantic import AnyHttpUrl

from wmg_gateway.oauth import server
from wmg_gateway.oauth.provider import ACCESS_TTL, REFRESH_TTL, clean_name, credential_id, redirect_problem


@pytest.mark.parametrize(
    "uri",
    [
        "https://app.example.com/callback",
        "https://app.example.com:8443/cb?x=1",
        "http://localhost:8765/callback",
        "http://127.0.0.1:3000/cb",
        "http://[::1]:3000/cb",
    ],
)
def test_redirect_uris_that_may_receive_a_code(uri: str) -> None:
    assert redirect_problem(uri) is None


@pytest.mark.parametrize(
    ("uri", "problem"),
    [
        ("http://example.com/cb", "https, or http on localhost"),
        ("http://localhost.evil.com/cb", "https, or http on localhost"),
        ("http://127.0.0.1.evil.com/cb", "https, or http on localhost"),
        ("ftp://localhost/cb", "https, or http on localhost"),
        ("javascript:alert(1)", "https, or http on localhost"),
        ("myapp://callback", "https, or http on localhost"),
        ("https://app.example.com/cb#frag", "fragment"),
        ("https:///nohost", "https, or http on localhost"),
    ],
)
def test_redirect_uris_that_may_not(uri: str, problem: str) -> None:
    assert problem in (redirect_problem(uri) or "")


def test_names_shown_on_the_consent_page_are_clean() -> None:
    assert clean_name("  Claude Code  ") == "Claude Code"
    assert clean_name("Web\x00App\n\x1b[31m") == "WebApp[31m"
    assert clean_name("x" * 500) == "x" * 100
    assert clean_name("") == clean_name(None) == clean_name("\x00\n") == "Unnamed app"


def test_credential_id_is_per_app_and_user() -> None:
    assert credential_id("c1", "u1") == "oauth:c1:u1"
    assert credential_id("c1", "u1") != credential_id("c1", "u2") != credential_id("c2", "u2")


def test_token_lifetimes_match_d58() -> None:
    assert ACCESS_TTL.total_seconds() == 3600
    assert REFRESH_TTL.days == 30


@pytest.mark.parametrize(
    ("issuer", "ok"),
    [
        ("https://gateway.137-23-40-25.sslip.io", True),
        ("http://gateway.localhost", True),
        ("http://127.0.0.1:8080", True),
        ("http://localhost", True),
        ("http://gateway.example.com", False),  # plain http only to this machine
        ("http://localhost.evil.com", False),
        ("http://evil.localhost.com", False),
        ("http://gateway.localhost/?q=1", False),
        ("https://gateway.example.com/#x", False),
    ],
)
def test_issuer_validation_allows_dot_localhost_but_nothing_else_over_http(issuer: str, ok: bool) -> None:
    if ok:
        server._validate_issuer(AnyHttpUrl(issuer))
    else:
        with pytest.raises(ValueError):
            server._validate_issuer(AnyHttpUrl(issuer))

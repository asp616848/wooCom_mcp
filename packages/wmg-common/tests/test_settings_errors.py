import json

import pytest

from wmg_common.errors import AppError, error_response
from wmg_common.settings import McpServerSettings, load_settings


def test_load_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "x" * 32)
    monkeypatch.setenv("MCP_AUTH_MODE", "header")
    monkeypatch.setenv("PORT", "9000")
    s = load_settings(McpServerSettings)
    assert s.port == 9000
    assert s.mcp_auth_mode == "header"
    assert s.mcp_auth_header == "X-Api-Key"
    assert s.mcp_auth_token.get_secret_value() == "x" * 32
    assert "x" * 32 not in repr(s)  # SecretStr hides the value


def test_invalid_settings_exit_without_echoing_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short-leak")
    monkeypatch.setenv("MCP_AUTH_MODE", "basic")
    with pytest.raises(SystemExit) as exc:
        load_settings(McpServerSettings)
    message = str(exc.value)
    assert "MCP_AUTH_TOKEN" in message
    assert "MCP_AUTH_MODE" in message
    assert "short-leak" not in message


def test_missing_required_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="MCP_AUTH_TOKEN"):
        load_settings(McpServerSettings)


def test_error_response_envelope() -> None:
    resp = error_response(409, "CONFLICT", "slug already exists")
    assert resp.status_code == 409
    assert json.loads(resp.body) == {"error": {"code": "CONFLICT", "message": "slug already exists"}}
    err = AppError(404, "NOT_FOUND", "no such server")
    assert (err.status, err.code, str(err)) == (404, "NOT_FOUND", "no such server")

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from wmg_common.logging import REDACTED, configure_logging, get_logger, redact_secrets


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers.clear()


def test_redact_top_level_and_nested() -> None:
    event = {
        "event": "x",
        "password": "hunter2",
        "Authorization": "Bearer abc",
        "headers": {"cookie": "c=1", "accept": "json", "X-Api-Key": "k"},
        "items": [{"token": "t"}, {"ok": 1}],
        "token_count": 3,
    }
    out = redact_secrets(None, "info", event)
    assert out["password"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["headers"] == {"cookie": REDACTED, "accept": "json", "X-Api-Key": REDACTED}
    assert out["items"] == [{"token": REDACTED}, {"ok": 1}]
    assert out["token_count"] == 3  # exact key match only
    assert out["event"] == "x"


def test_structlog_and_stdlib_lines_are_json_and_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("unit-test", "info")
    get_logger("t").info("hello", credential="secret-value", user="rohan@example.com")
    logging.getLogger("uvicorn.error").info("stdlib says hi")
    get_logger("t").debug("hidden at info level")

    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(lines) == 2
    first, second = lines
    assert first["event"] == "hello"
    assert first["credential"] == REDACTED
    assert first["service"] == "unit-test"
    assert first["level"] == "info"
    assert "timestamp" in first
    assert second["event"] == "stdlib says hi"
    assert second["logger"] == "uvicorn.error"
    assert "secret-value" not in json.dumps(lines)


def test_http_client_request_urls_not_logged(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("unit-test", "info")
    logging.getLogger("httpx2").info("HTTP Request: GET http://store/x?oauth_signature=abc")
    logging.getLogger("httpx2").warning("pool exhausted")
    out = capsys.readouterr().out
    assert "oauth_signature" not in out
    assert "pool exhausted" in out


def test_uvicorn_access_log_stays_off(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("unit-test", "info")
    logging.getLogger("uvicorn.access").info('10.0.0.1:1 - "GET /readyz HTTP/1.1" 200')
    logging.getLogger("uvicorn.error").info("Application startup complete.")
    out = capsys.readouterr().out
    assert "/readyz" not in out
    assert "startup complete" in out

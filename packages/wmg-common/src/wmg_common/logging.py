"""Structured JSON logging with secret redaction (D75).

Every log line is one JSON object on stdout. structlog events and stdlib records (uvicorn, the MCP SDK)
go through the same processors, so values under sensitive keys are always replaced with "[REDACTED]".
"""

import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset({"authorization", "cookie", "credential", "password", "token", "x_api_key"})


def _is_sensitive(key: object) -> bool:
    return isinstance(key, str) and key.lower().replace("-", "_") in SENSITIVE_KEYS


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: REDACTED if _is_sensitive(k) else _redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def redact_secrets(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: redact sensitive keys at any depth of the event."""
    for key, value in event_dict.items():
        event_dict[key] = REDACTED if _is_sensitive(key) else _redact(value)
    return event_dict


def configure_logging(service: str, level: str = "info") -> None:
    """Route structlog and stdlib logging to one JSON-lines handler on stdout."""

    def add_service(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_service,
        redact_secrets,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own plain-text handlers; send its records through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger

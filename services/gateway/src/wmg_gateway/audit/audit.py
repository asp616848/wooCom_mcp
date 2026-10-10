"""Audit log (DESIGN §5.10, D60): one row per `tools/call` (every outcome) and per rejected authenticated
request.

Arguments are summarized, not stored: string values truncated to 100 chars, secret-looking keys redacted. A
failed insert is retried once; after that the error is logged and counted, and the response still goes out.
"""

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Literal

from prometheus_client import Counter

from wmg_common.logging import get_logger

log = get_logger(__name__)

Outcome = Literal[
    "success", "tool_error", "upstream_error", "timeout", "denied", "unknown_tool", "rate_limited"
]
CredentialType = Literal["api_key", "chat_agent", "oauth"]

AUDIT_WRITE_FAILURES = Counter("wmg_audit_write_failures_total", "Audit rows that could not be written")

_SECRET_KEY = re.compile(r"(?i)pass|secret|token|key|auth")
MAX_STRING = 100
MAX_ITEMS = 20
MAX_DEPTH = 4


def summarize_args(value: Any, depth: int = 0) -> Any:
    if depth >= MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= MAX_ITEMS:
                out["…"] = f"{len(value) - MAX_ITEMS} more"
                break
            out[str(k)] = "[redacted]" if _SECRET_KEY.search(str(k)) else summarize_args(v, depth + 1)
        return out
    if isinstance(value, list):
        items = [summarize_args(v, depth + 1) for v in value[:MAX_ITEMS]]
        return [*items, f"… {len(value) - MAX_ITEMS} more"] if len(value) > MAX_ITEMS else items
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…"
    return value


@dataclass
class AuditRecord:
    method: str
    outcome: Outcome
    duration_ms: int
    user_id: str | None = None
    user_email: str | None = None
    credential_type: CredentialType | None = None
    credential_id: str | None = None
    server_id: str | None = None
    server_slug: str | None = None
    tool_name: str | None = None
    exposed_name: str | None = None
    error_message: str | None = None
    args_summary: Any = None
    client_name: str | None = None
    client_mode: str = "full"
    request_id: str | None = None


_COLUMNS = list(AuditRecord.__dataclass_fields__)
_INSERT = (
    f"INSERT INTO audit_log ({', '.join(_COLUMNS)}) "  # noqa: S608 - column names are our own constants
    f"VALUES ({', '.join(f'${i}' for i in range(1, len(_COLUMNS) + 1))})"
)


class AuditWriter:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def write(self, record: AuditRecord) -> None:
        row = asdict(record)
        for key in ("user_id", "server_id"):
            if row[key] is not None:
                row[key] = uuid.UUID(row[key])
        if row["error_message"]:
            row["error_message"] = row["error_message"][:1000]
        values = [row[c] for c in _COLUMNS]
        for attempt in (1, 2):
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute(_INSERT, *values)
                return
            except Exception as exc:
                if attempt == 2:
                    AUDIT_WRITE_FAILURES.inc()
                    log.error("audit write failed", error=type(exc).__name__, outcome=record.outcome)

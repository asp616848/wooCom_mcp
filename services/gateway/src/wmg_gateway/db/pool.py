"""asyncpg pool (D72). JSON/JSONB columns are decoded to Python objects."""

import json
from typing import Any

import anyio
import asyncpg

from wmg_common.logging import get_logger

log = get_logger(__name__)

Pool = asyncpg.Pool
Connection = asyncpg.Connection


async def _init_connection(conn: Any) -> None:
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int, max_size: int, wait_s: float = 60) -> Any:
    """Postgres may still be starting when the gateway boots: retry every 2 s for up to `wait_s`."""
    deadline = anyio.current_time() + wait_s
    while True:
        try:
            return await asyncpg.create_pool(
                dsn, min_size=min_size, max_size=max_size, init=_init_connection, command_timeout=30
            )
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError) as exc:
            if anyio.current_time() > deadline:
                raise
            log.warning("database not reachable yet, retrying", error=type(exc).__name__)
            await anyio.sleep(2)

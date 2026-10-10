"""Leader election (D41): a Postgres session advisory lock on a dedicated connection.

The replica holding `pg_try_advisory_lock(hashtext('wmg-leader'))` runs the leader duties (health loop now;
reconcilers and retention later). If its connection dies, Postgres releases the lock and another replica takes
over within one retry interval. Non-leaders only serve traffic.
"""

from collections.abc import Callable, Coroutine
from typing import Any

import anyio
import asyncpg

from wmg_common.logging import get_logger

log = get_logger(__name__)

LOCK_NAME = "wmg-leader"
RETRY_S = 5.0


class Leader:
    def __init__(
        self, dsn: str, duties: Callable[[], Coroutine[Any, Any, None]], retry_s: float = RETRY_S
    ) -> None:
        self._dsn = dsn
        self._duties = duties
        self._retry_s = retry_s
        self.is_leader = False

    async def run(self) -> None:
        while True:
            conn: Any = None
            try:
                conn = await asyncpg.connect(self._dsn)
                # polling the lock is the point: there is no event to wait for
                while not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME):  # noqa: ASYNC110
                    await anyio.sleep(self._retry_s)
                self.is_leader = True
                log.info("became leader")
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._duties)
                    while True:  # if the lock connection dies, stop the duties and compete again
                        await anyio.sleep(self._retry_s)
                        await conn.fetchval("SELECT 1")
            except Exception as exc:
                log.warning("leader loop error", error=type(exc).__name__, was_leader=self.is_leader)
            finally:
                if self.is_leader:
                    log.info("leadership released")
                self.is_leader = False
                if conn is not None:
                    with anyio.CancelScope(shield=True):
                        await conn.close(timeout=2)
            await anyio.sleep(self._retry_s)

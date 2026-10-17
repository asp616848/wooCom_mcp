"""Leader election (D41, D95): a Postgres session advisory lock on a dedicated connection.

The replica holding `pg_try_advisory_lock(hashtext('wmg-leader'))` runs the leader duties (health loop,
reconcilers, housekeeping). If its connection dies, Postgres releases the lock and another replica takes over
within one retry interval (2 s). A pod that vanishes without closing its socket is caught by Postgres TCP
keepalives (~8 s, chart). The leader stands down the moment its lock connection closes, and pings it with a
timeout, so a leader cut off from Postgres stops its duties instead of running beside the new one.
`wmg_leader` is 1 on the leader.
"""

from collections.abc import Callable, Coroutine
from typing import Any

import anyio
import asyncpg
from prometheus_client import Gauge

from wmg_common.logging import get_logger

log = get_logger(__name__)

LOCK_NAME = "wmg-leader"
RETRY_S = 2.0
LEADER = Gauge("wmg_leader", "1 on the replica holding the leader lock")


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
                conn = await asyncpg.connect(self._dsn, timeout=10)
                # polling the lock is the point: there is no event to wait for
                while not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_NAME):  # noqa: ASYNC110
                    await anyio.sleep(self._retry_s)
                lost = anyio.Event()
                # fires as soon as Postgres closes the connection, before the follower can take the lock
                conn.add_termination_listener(lambda _conn, lost=lost: lost.set())
                self.is_leader = True
                LEADER.set(1)
                log.info("became leader")
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._duties)
                    while True:  # if the lock connection dies, stop the duties and compete again
                        with anyio.move_on_after(self._retry_s):
                            await lost.wait()
                        if lost.is_set():
                            raise ConnectionError("leader lock connection closed")
                        await conn.fetchval("SELECT 1", timeout=self._retry_s)
            except Exception as exc:
                log.warning("leader loop error", error=type(exc).__name__, was_leader=self.is_leader)
            finally:
                if self.is_leader:
                    log.info("leadership released")
                self.is_leader = False
                LEADER.set(0)
                if conn is not None:
                    with anyio.CancelScope(shield=True):
                        await conn.close(timeout=2)
            await anyio.sleep(self._retry_s)

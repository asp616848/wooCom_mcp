"""Cross-replica change events over Postgres LISTEN/NOTIFY (DESIGN §5.8, D40).

Writers call `notify()` inside their transaction (delivered on commit). Every replica runs `EventListener`
on a dedicated connection; any event means "reload the catalog". After a reconnect it reloads once, because
events may have been missed while disconnected.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import anyio
import asyncpg

from wmg_common.logging import get_logger

log = get_logger(__name__)

CHANNEL = "wmg_events"
EventType = Literal["catalog_changed", "server_status"]
KEEPALIVE_S = 30.0
MAX_BACKOFF_S = 30.0


async def notify(conn: Any, event_type: EventType, server_id: str | None = None) -> None:
    payload = json.dumps({"type": event_type, "server_id": server_id})
    await conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)


class EventListener:
    def __init__(self, dsn: str, on_change: Callable[[], Awaitable[None]]) -> None:
        self._dsn = dsn
        self._on_change = on_change

    async def run(self, *, task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
        backoff = 1.0
        started = False
        while True:
            conn: Any = None
            send, recv = anyio.create_memory_object_stream[str](256)
            try:
                conn = await asyncpg.connect(self._dsn)

                def on_notify(_conn: Any, _pid: int, _channel: str, payload: str, send: Any = send) -> None:
                    try:
                        send.send_nowait(payload)
                    except anyio.WouldBlock:
                        pass  # a reload is already pending; it will see the latest state
                    except anyio.ClosedResourceError, anyio.BrokenResourceError:
                        # a NOTIFY landing while this connection is being closed (shutdown or reconnect);
                        # a reconnect reloads anyway, so the event is not lost
                        pass

                await conn.add_listener(CHANNEL, on_notify)
                if started:
                    log.info("event listener reconnected, reloading")
                    await self._on_change()
                else:
                    started = True
                    task_status.started()
                backoff = 1.0
                while True:
                    with anyio.move_on_after(KEEPALIVE_S) as idle:
                        payload = await recv.receive()
                    if idle.cancelled_caught:
                        await conn.fetchval("SELECT 1")  # raises if the connection died silently
                        continue
                    while True:  # coalesce a burst of events into one reload
                        try:
                            recv.receive_nowait()
                        except anyio.WouldBlock:
                            break
                    log.debug("event received", payload=payload)
                    await self._on_change()
            except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
                log.warning("event listener disconnected", error=type(exc).__name__, retry_in_s=backoff)
                if not started:
                    started = True
                    task_status.started()
                await anyio.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
            finally:
                send.close()
                recv.close()
                if conn is not None:
                    with anyio.CancelScope(shield=True):
                        await conn.close(timeout=2)

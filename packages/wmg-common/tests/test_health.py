import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from starlette.applications import Starlette

from wmg_common.health import health_routes

AsgiCall = Callable[..., Awaitable[Any]]  # the `asgi_call` fixture from conftest.py


async def ready_true() -> bool:
    return True


async def ready_false() -> bool:
    return False


async def ready_raises() -> bool:
    raise ConnectionError("db down")


@pytest.mark.anyio
async def test_livez_always_ok(asgi_call: AsgiCall) -> None:
    resp = await asgi_call(Starlette(routes=health_routes(ready_false)), "/livez")
    assert resp.status == 200
    assert json.loads(resp.body) == {"status": "ok"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("check", "status"), [(None, 200), (ready_true, 200), (ready_false, 503), (ready_raises, 503)]
)
async def test_readyz(check: object, status: int, asgi_call: AsgiCall) -> None:
    resp = await asgi_call(Starlette(routes=health_routes(check)), "/readyz")  # type: ignore[arg-type]
    assert resp.status == status


@pytest.mark.anyio
async def test_health_is_get_only(asgi_call: AsgiCall) -> None:
    resp = await asgi_call(Starlette(routes=health_routes()), "/livez", method="POST")
    assert resp.status == 405

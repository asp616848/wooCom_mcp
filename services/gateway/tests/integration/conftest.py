"""Real Postgres for integration tests (TESTING §3.1): `postgres:17.11-alpine3.24` on localhost:55432.

The container is started once per session (reused if already running) and each test gets a fresh database.
"""

import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import anyio
import asyncpg
import pytest

CONTAINER = "wmg-test-postgres"
PORT = 55432
ADMIN_DSN = f"postgresql://postgres:test@127.0.0.1:{PORT}/postgres"


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607


@pytest.fixture(scope="session")
def postgres() -> Iterator[str]:
    if shutil.which("docker") is None or _docker("info").returncode != 0:
        pytest.skip("Docker is not available")
    if CONTAINER not in _docker("ps", "--format", "{{.Names}}").stdout.split():
        _docker("rm", "-f", CONTAINER)
        started = _docker(
            "run", "-d", "--rm", "--name", CONTAINER, "-e", "POSTGRES_PASSWORD=test",
            "-p", f"127.0.0.1:{PORT}:5432", "postgres:17.11-alpine3.24",
        )  # fmt: skip
        assert started.returncode == 0, started.stderr
    deadline = time.monotonic() + 60
    while _docker("exec", CONTAINER, "pg_isready", "-U", "postgres").returncode != 0:
        assert time.monotonic() < deadline, "postgres did not become ready"
        time.sleep(0.5)
    time.sleep(1)  # the image restarts the server once after init
    yield ADMIN_DSN


@pytest.fixture
async def database_url(postgres: str) -> AsyncIterator[str]:
    name = f"t_{uuid.uuid4().hex[:12]}"
    for _ in range(20):
        try:
            admin: Any = await asyncpg.connect(postgres)
            break
        except OSError, asyncpg.CannotConnectNowError:
            await anyio.sleep(0.5)
    await admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield postgres.rsplit("/", 1)[0] + "/" + name
    finally:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()

"""Integration fixtures (TESTING §3.1).

- Real Postgres `postgres:17.11-alpine3.24` on localhost:55432: started once per session (reused if already
  running); each test gets a fresh database.
- Fixture MCP servers and gateways served by uvicorn in background threads. They live here, not in
  fixtures.py, because pytest's importlib mode can't import sibling test modules.
"""

import json
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import asyncpg
import httpx2
import mcp_types as types
import pytest
import uvicorn
from mcp import MCPError
from mcp.server import MCPServer, Server
from mcp.server.mcpserver.exceptions import ToolError
from starlette.types import ASGIApp, Receive, Scope, Send

from wmg_common.auth import StaticCredentialMiddleware
from wmg_gateway.http.app import create_app
from wmg_gateway.settings import Settings
from wmg_gateway.upstream.manager import UpstreamManager

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


ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin-password-123"  # base_env's bootstrap password
FIXTURE_TOKEN = "fixture-token-123"
CSRF_HEADERS = {"X-WMG-CSRF": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ThreadServer:
    """uvicorn on 127.0.0.1:<port> in a daemon thread (own event loop); start() waits until it serves."""

    def __init__(self, app: ASGIApp, port: int | None = None) -> None:
        self.port = port or free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> ThreadServer:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            assert self.thread.is_alive(), "server thread died during startup"
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)


class Recorder:
    """ASGI wrapper recording request headers and the Mcp-Method header of every request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers: list[dict[str, str]] = []
        self.methods: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
            self.headers.append(headers)
            if "mcp-method" in headers:
                self.methods.append(headers["mcp-method"])
        await self.app(scope, receive, send)


@dataclass
class Concurrency:
    current: int = 0
    peak: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def enter(self) -> None:
        with self.lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    def leave(self) -> None:
        with self.lock:
            self.current -= 1


def modern_app(concurrency: Concurrency) -> Recorder:
    mcp = MCPServer("fixture-modern")

    @mcp.tool(description="Echo the text back")
    def echo(text: str) -> str:
        return text

    @mcp.tool(description="Sleep for ms milliseconds")
    async def slow(ms: int) -> str:
        concurrency.enter()
        try:
            await anyio.sleep(ms / 1000)
        finally:
            concurrency.leave()
        return f"slept {ms}"

    @mcp.tool(description="Always fails")
    def fail() -> str:
        raise ToolError("boom")

    @mcp.tool(description="Returns about 2 MB of text")
    def big() -> str:
        return "x" * 2_000_000

    return Recorder(mcp.streamable_http_app(stateless_http=True, json_response=True))


def legacy_only_app() -> Recorder:
    async def no_discover(ctx: Any, call_next: Any) -> Any:
        if ctx.method == "server/discover":
            raise MCPError(types.METHOD_NOT_FOUND, "Method not found")
        return await call_next(ctx)

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        return types.ListToolsResult(
            tools=[types.Tool(name="echo", description="Legacy echo", input_schema=schema)]
        )

    async def call_tool(_ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        text = (params.arguments or {}).get("text", "")
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"legacy:{text}")])

    server: Server[Any] = Server("fixture-legacy", on_list_tools=list_tools, on_call_tool=call_tool)
    server.middleware.insert(0, no_discover)
    return Recorder(server.streamable_http_app(stateless_http=True, json_response=True))


def secured_app() -> Recorder:
    """Bearer-protected (like gst): checks the gateway injects the upstream credential."""
    mcp = MCPServer("fixture-secured")

    @mcp.tool(description="Ping")
    def ping() -> str:
        return "pong"

    app = mcp.streamable_http_app(stateless_http=True, json_response=True)
    return Recorder(StaticCredentialMiddleware(app, mode="bearer", token=FIXTURE_TOKEN))


@dataclass
class Upstreams:
    modern: ThreadServer
    modern_rec: Recorder
    legacy: ThreadServer
    legacy_rec: Recorder
    secured: ThreadServer
    secured_rec: Recorder
    concurrency: Concurrency
    closed_url: str


@pytest.fixture(scope="session")
def upstreams() -> Iterator[Upstreams]:
    concurrency = Concurrency()
    modern_rec, legacy_rec, secured_rec = modern_app(concurrency), legacy_only_app(), secured_app()
    servers = [ThreadServer(modern_rec), ThreadServer(legacy_rec), ThreadServer(secured_rec)]
    for s in servers:
        s.start()
    yield Upstreams(
        modern=servers[0],
        modern_rec=modern_rec,
        legacy=servers[1],
        legacy_rec=legacy_rec,
        secured=servers[2],
        secured_rec=secured_rec,
        concurrency=concurrency,
        closed_url=f"http://127.0.0.1:{free_port()}/mcp",
    )
    for s in servers:
        s.stop()


@pytest.fixture
def fresh_modern() -> Iterator[Callable[[], tuple[ThreadServer, Concurrency]]]:
    """A private modern fixture server (for tests that stop it or measure its concurrency)."""
    started: list[ThreadServer] = []

    def make() -> tuple[ThreadServer, Concurrency]:
        concurrency = Concurrency()
        server = ThreadServer(modern_app(concurrency)).start()
        started.append(server)
        return server, concurrency

    yield make
    for server in started:
        server.stop()


@dataclass
class Gateway:
    server: ThreadServer
    app: Any

    @property
    def url(self) -> str:
        return self.server.url

    @property
    def mcp_url(self) -> str:
        return self.server.url + "/mcp"


async def _allow_any_url(_url: str) -> None:
    """Loopback fixture servers would be blocked by the SSRF policy (unit-tested separately)."""


@pytest.fixture
def gateway_factory(
    tmp_path: Path, base_env: dict[str, str], database_url: str
) -> Iterator[Callable[..., Gateway]]:
    (tmp_path / "seed.json").write_text(json.dumps([]))
    started: list[ThreadServer] = []

    def make(run_leader: bool = True, **overrides: Any) -> Gateway:
        values: dict[str, Any] = {
            **base_env,
            "database_url": database_url,
            "seed_servers_file": tmp_path / "seed.json",
            "seed_credentials_dir": tmp_path / "creds",
            "health_interval_ms": 1000,
            "health_timeout_ms": 2000,
            **overrides,
        }
        settings = Settings(**values)
        app = create_app(settings, run_leader=run_leader)
        rt = app.state.runtime
        rt.check_url = _allow_any_url
        rt.manager = UpstreamManager(rt.cipher, settings.upstream_max_inflight, httpx2.AsyncHTTPTransport)
        server = ThreadServer(app).start()
        started.append(server)
        return Gateway(server=server, app=app)

    yield make
    for server in started:
        server.stop()

"""TEST-U-07: tools/call algorithm (DESIGN §5.7) with a fake catalog, manager and audit writer."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import anyio
import mcp_types as types
import pytest
from mcp import MCPError
from mcp.server.auth.provider import AccessToken

from wmg_gateway.audit.audit import AuditRecord
from wmg_gateway.catalog.catalog import Catalog, ServerEntry, ToolEntry
from wmg_gateway.mcp import call_tool as ct
from wmg_gateway.runtime import Runtime
from wmg_gateway.settings import Settings
from wmg_gateway.upstream.manager import UpstreamError

SERVER = ServerEntry(
    id="00000000-0000-0000-0000-000000000001",
    slug="gst",
    name="GST",
    url="http://gst.mcp-upstreams.svc:8080/mcp",
    auth_type="none",
    auth_header_name=None,
    credential_enc=None,
    enabled=True,
    timeout_ms=1000,
    source="seed",
    status="connected",
    last_error_code=None,
    last_error=None,
    protocol_era="modern",
    discover_result=None,
    era_verified_at=None,
    updated_at=datetime.now(UTC),
)


def tool(
    server: ServerEntry = SERVER, name: str = "echo", admin_only: bool = False, **hints: Any
) -> ToolEntry:
    return ToolEntry(
        server=server,
        original=name,
        exposed=f"{server.slug}__{name}",
        title=None,
        description="d",
        input_schema={"type": "object"},
        output_schema=None,
        annotations=hints or None,
        admin_only=admin_only,
    )


class FakeManager:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour
        self.calls = 0
        self.sem = anyio.Semaphore(16)

    def semaphore(self, _server: ServerEntry) -> anyio.Semaphore:
        return self.sem

    async def call(
        self, _server: ServerEntry, upstream: types.Tool, args: dict[str, Any]
    ) -> types.CallToolResult:
        self.calls += 1
        return await self.behaviour(self.calls, upstream, args)


class FakeAudit:
    def __init__(self) -> None:
        self.rows: list[AuditRecord] = []

    async def write(self, record: AuditRecord) -> None:
        self.rows.append(record)


def ctx(role: str = "admin") -> Any:
    token = AccessToken(
        token="t",
        client_id="k1",
        scopes=["mcp"],
        subject="00000000-0000-0000-0000-0000000000aa",
        claims={
            "email": "rohan@example.com",
            "role": role,
            "credential_type": "api_key",
            "credential_id": "k1",
        },
    )
    return SimpleNamespace(
        request=SimpleNamespace(scope={"user": SimpleNamespace(access_token=token)}), meta=None
    )


@pytest.fixture
def runtime(base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Runtime:
    settings = Settings(database_url="postgresql://x@localhost/x", max_result_bytes=2048, **base_env)  # type: ignore[arg-type]
    rt = Runtime.create(settings)
    rt.audit = FakeAudit()  # type: ignore[assignment]
    marked: list[str] = []

    async def fake_mark(_pool: Any, server: ServerEntry, error: UpstreamError) -> None:
        marked.append(f"{server.slug}:{error.code}")

    monkeypatch.setattr(ct, "mark_unreachable", fake_mark)
    rt.marked = marked  # type: ignore[attr-defined]
    return rt


def use(rt: Runtime, *entries: ToolEntry, behaviour: Any = None) -> FakeManager:
    rt.catalog.current = Catalog(
        servers={e.server.id: e.server for e in entries}, tools_by_exposed={e.exposed: e for e in entries}
    )

    async def ok(_n: int, upstream: types.Tool, args: dict[str, Any]) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"{upstream.name}:{args}")])

    manager = FakeManager(behaviour or ok)
    rt.manager = manager  # type: ignore[assignment]
    return manager


def outcomes(rt: Runtime) -> list[str]:
    return [r.outcome for r in rt.audit.rows]  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_U07_unknown_disabled_forbidden(runtime: Runtime) -> None:
    use(
        runtime,
        tool(),
        tool(replace(SERVER, enabled=False, id="2", slug="off")),
        tool(name="w", admin_only=True),
    )
    with pytest.raises(MCPError) as e1:
        await ct.call_exposed(runtime, ctx(), "nope__x", {}, "full")
    assert e1.value.error.code == -32602
    with pytest.raises(MCPError) as e2:
        await ct.call_exposed(runtime, ctx(), "off__echo", {}, "full")
    assert e2.value.error.code == -32602 and "disabled" in e2.value.error.message
    with pytest.raises(MCPError) as e3:
        await ct.call_exposed(runtime, ctx("member"), "gst__w", {}, "full")
    assert e3.value.error.code == -32010
    assert outcomes(runtime) == ["unknown_tool", "unknown_tool", "denied"]


@pytest.mark.anyio
async def test_U07_success_routes_original_name_and_audits(runtime: Runtime) -> None:
    manager = use(runtime, tool())
    result = await ct.call_exposed(runtime, ctx(), "gst__echo", {"text": "hi", "api_key": "secret"}, "full")
    assert not result.is_error and result.content[0].text.startswith("echo:")
    row = runtime.audit.rows[0]  # type: ignore[attr-defined]
    assert row.outcome == "success" and row.tool_name == "echo" and row.server_slug == "gst"
    assert row.args_summary == {"text": "hi", "api_key": "[redacted]"}
    assert manager.calls == 1


@pytest.mark.anyio
async def test_U07_unreachable_status_is_tool_error(runtime: Runtime) -> None:
    down = replace(SERVER, status="unreachable", last_error="Connection refused")
    manager = use(runtime, tool(down))
    result = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert result.is_error and result.content[0].text.startswith("[gateway] Server 'gst' is unreachable")
    assert manager.calls == 0 and outcomes(runtime) == ["upstream_error"]


@pytest.mark.anyio
async def test_U07_timeout_does_not_mark_unreachable(runtime: Runtime) -> None:
    async def slow(*_: Any) -> types.CallToolResult:
        await anyio.sleep(5)
        raise AssertionError

    use(runtime, tool(), behaviour=slow)
    started = anyio.current_time()
    result = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert anyio.current_time() - started < 2
    assert result.is_error and "timed out after 1000ms" in result.content[0].text
    assert outcomes(runtime) == ["timeout"] and runtime.marked == []  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_U07_transport_error_marks_unreachable(runtime: Runtime) -> None:
    async def refused(*_: Any) -> types.CallToolResult:
        raise UpstreamError("CONNECTION_REFUSED", "Connection refused")

    manager = use(runtime, tool(), behaviour=refused)
    result = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert result.is_error and result.content[0].text == "[gateway] 'gst' unreachable: CONNECTION_REFUSED"
    assert manager.calls == 1  # not read-only → no retry (D39)
    assert runtime.marked == ["gst:CONNECTION_REFUSED"]  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_U07_retry_once_for_read_only(runtime: Runtime) -> None:
    async def flaky(n: int, *_: Any) -> types.CallToolResult:
        if n == 1:
            raise UpstreamError("CONNECTION_REFUSED", "Connection refused")
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    manager = use(runtime, tool(readOnlyHint=True), behaviour=flaky)
    result = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert not result.is_error and manager.calls == 2


@pytest.mark.anyio
async def test_U07_upstream_mcp_error_and_tool_error_and_oversize(runtime: Runtime) -> None:
    async def mcp_error(*_: Any) -> types.CallToolResult:
        raise MCPError(-32602, "bad args")

    use(runtime, tool(), behaviour=mcp_error)
    r1 = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert r1.is_error and r1.content[0].text == "[gateway] 'gst' error -32602: bad args"

    async def tool_error(*_: Any) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="no such order")], is_error=True
        )

    use(runtime, tool(), behaviour=tool_error)
    r2 = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert r2.is_error and r2.content[0].text == "no such order"  # passed through unchanged

    async def big(*_: Any) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text="x" * 5000)])

    use(runtime, tool(), behaviour=big)
    r3 = await ct.call_exposed(runtime, ctx(), "gst__echo", {}, "full")
    assert r3.is_error and r3.content[0].text.startswith("[gateway] result too large")
    assert outcomes(runtime) == ["upstream_error", "tool_error", "upstream_error"]

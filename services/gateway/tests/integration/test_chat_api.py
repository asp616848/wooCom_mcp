"""P10: POST /api/chat end to end with a scripted OpenAI-compatible LLM (DESIGN §12).

Checks the SSE stream, that the agent's tool calls go through the gateway's own /mcp as the signed-in user
(audit `chat_agent`, credential `chat:<user id>`), and the request guards.
"""

import json
from typing import Any

import asyncpg
import httpx2
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

ADMIN = {"email": "admin@example.com", "password": "admin-password-123"}
CSRF = {"X-WMG-CSRF": "1"}
ECHO = "fx-modern__echo"


class ScriptedLlm:
    """First request → call fx-modern__echo; once a tool result is in the history → answer with it."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.app = Starlette(routes=[Route("/v1/chat/completions", self.completions, methods=["POST"])])

    async def completions(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.requests.append(body)
        last = body["messages"][-1]
        if last["role"] == "tool":
            message: dict[str, Any] = {"role": "assistant", "content": f"Echo said: {last['content']}"}
        else:
            call = {
                "id": "call-1",
                "type": "function",
                "function": {"name": ECHO, "arguments": '{"text": "hi"}'},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
        return JSONResponse(
            {
                "id": "c",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            }
        )


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


async def login(http: httpx2.AsyncClient) -> str:
    r = await http.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    return str(r.json()["user"]["id"])


async def test_chat_streams_tool_loop_through_own_mcp(
    gateway_factory: Any, upstreams: Any, database_url: str, serve: Any
) -> None:
    llm = ScriptedLlm()
    llm_server = serve(llm.app)
    gw = gateway_factory(llm_base_url=llm_server.url + "/v1", llm_api_key="test-key", llm_model="fake-1")
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        user_id = await login(http)
        server = {"slug": "fx-modern", "name": "M", "url": upstreams.modern.url + "/mcp"}
        r = await http.post("/api/servers", json=server)
        assert r.status_code == 201, r.text
        r = await http.post("/api/chat", json={"messages": [{"role": "user", "content": "say hi"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    events = parse_sse(r.text)
    assert [e for e, _ in events] == ["tool_call", "tool_result", "answer", "done"]
    assert events[0][1] == {"id": "call-1", "name": ECHO, "arguments": {"text": "hi"}}
    assert events[1][1]["ok"] is True and events[1][1]["preview"] == "hi"
    assert events[2][1] == {"text": "Echo said: hi"}
    assert events[3][1] == {"llmCalls": 2, "toolCalls": 1}

    first = llm.requests[0]
    assert first["model"] == "fake-1" and first["messages"][0]["role"] == "system"
    assert ECHO in [t["function"]["name"] for t in first["tools"]]

    conn = await asyncpg.connect(database_url)
    try:
        row = await conn.fetchrow("SELECT * FROM audit_log WHERE exposed_name = $1", ECHO)
    finally:
        await conn.close()
    assert row is not None and row["outcome"] == "success"
    assert row["credential_type"] == "chat_agent" and row["credential_id"] == f"chat:{user_id}"
    assert row["user_email"] == ADMIN["email"] and row["client_name"] == "wmg-chat-agent"


async def test_chat_guards(gateway_factory: Any) -> None:
    gw = gateway_factory()  # no LLM key configured
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert (await http.post("/api/chat", json=body)).status_code == 401
        await login(http)
        r = await http.post("/api/chat", json=body)
        assert r.status_code == 503 and r.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
        bad = [
            {"messages": []},
            {"messages": [{"role": "assistant", "content": "hi"}]},
            {"messages": [{"role": "system", "content": "hi"}]},
            {"messages": [{"role": "user", "content": "x" * 8001}]},
            {"messages": [{"role": "user", "content": "hi"}] * 21},
        ]
        for case in bad:
            assert (await http.post("/api/chat", json=case)).status_code == 422, case


class SearchingLlm:
    """Search mode: search_tools → call_tool on the first hit → answer with its result."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.app = Starlette(routes=[Route("/v1/chat/completions", self.completions, methods=["POST"])])

    async def completions(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.requests.append(body)
        last = body["messages"][-1]
        if last["role"] == "user":
            call = ("search_tools", {"query": "orders today"})
        elif last["role"] == "tool" and len(self.requests) == 2:
            name = json.loads(last["content"])["tools"][0]["name"]
            call = ("call_tool", {"name": name, "arguments": {"period": "today"}})
        else:
            return _completion(body, {"role": "assistant", "content": f"Found: {last['content']}"})
        fn = {"name": call[0], "arguments": json.dumps(call[1])}
        tool_call = {"id": f"c{len(self.requests)}", "type": "function", "function": fn}
        message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        return _completion(body, message)


def _completion(body: dict[str, Any], message: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
    )


async def test_SO4_02_chat_switches_to_search_mode(
    gateway_factory: Any, many_tools: Any, database_url: str, serve: Any
) -> None:
    llm = SearchingLlm()
    llm_server = serve(llm.app)
    gw = gateway_factory(llm_base_url=llm_server.url + "/v1", llm_api_key="test-key", llm_model="fake-1")
    async with httpx2.AsyncClient(base_url=gw.url, headers=CSRF, timeout=30) as http:
        await login(http)
        many = {"slug": "fx-many", "name": "M", "url": many_tools.url + "/mcp"}
        r = await http.post("/api/servers", json=many)
        assert r.status_code == 201, r.text
        assert r.json()["server"]["toolCount"] == 60  # over TOOL_SEARCH_THRESHOLD (40)
        r = await http.post("/api/chat", json={"messages": [{"role": "user", "content": "orders today?"}]})
    events = parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds == ["tool_call", "tool_result", "tool_call", "tool_result", "answer", "done"]
    assert events[2][1]["arguments"] == {"name": "fx-many__list_orders", "arguments": {"period": "today"}}
    assert "2 orders today" in events[4][1]["text"]
    first = llm.requests[0]
    declared = sorted(t["function"]["name"] for t in first["tools"])
    assert declared == ["call_tool", "list_servers", "search_tools"]
    assert "Search mode" in first["messages"][0]["content"]
    conn = await asyncpg.connect(database_url)
    try:
        row = await conn.fetchrow("SELECT * FROM audit_log WHERE exposed_name = 'fx-many__list_orders'")
    finally:
        await conn.close()
    assert row["outcome"] == "success" and row["client_mode"] == "search"
    assert row["credential_type"] == "chat_agent"

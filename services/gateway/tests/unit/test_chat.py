"""TEST-U-09 `sanitize_schema` and TEST-U-15 the chat loop (fake LLM, fake MCP session; DESIGN §12)."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx2
import mcp_types as types
import openai
import pytest
from mcp import MCPError
from openai import RateLimitError
from openai.types.chat import ChatCompletion

from wmg_gateway.agent import chat
from wmg_gateway.agent.chat import sanitize_schema

# ---------------------------------------------------------------------------------------------- TEST-U-09


def test_allowlist_drops_unknown_keywords_recursively() -> None:
    schema = {
        "type": "object",
        "title": "Args",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "title": "Name", "minLength": 1, "default": "x", "examples": ["a"]},
            "tags": {"type": "array", "items": {"type": "string", "title": "Tag"}, "maxItems": 5},
        },
        "required": ["name"],
    }
    assert sanitize_schema(schema) == {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        },
        "required": ["name"],
    }


def test_format_kept_only_for_date_time() -> None:
    schema = {
        "type": "object",
        "properties": {
            "at": {"type": "string", "format": "date-time"},
            "email": {"type": "string", "format": "email"},
        },
    }
    out = sanitize_schema(schema)
    assert out["properties"]["at"] == {"type": "string", "format": "date-time"}
    assert out["properties"]["email"] == {"type": "string"}


def test_defs_are_inlined() -> None:
    schema = {
        "type": "object",
        "$defs": {
            "Line": {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}}},
            "Status": {"type": "string", "enum": ["pending", "completed"]},
        },
        "properties": {
            "lines": {"type": "array", "items": {"$ref": "#/$defs/Line"}},
            "status": {"$ref": "#/$defs/Status", "description": "New status"},
        },
    }
    out = sanitize_schema(schema)
    assert "$defs" not in out
    assert out["properties"]["lines"]["items"] == {
        "type": "object",
        "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"}},
    }
    assert out["properties"]["status"] == {
        "type": "string",
        "enum": ["pending", "completed"],
        "description": "New status",
    }


def test_recursive_ref_stops_at_depth_limit() -> None:
    schema = {
        "type": "object",
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}},
        "properties": {"root": {"$ref": "#/$defs/Node"}},
    }
    node = sanitize_schema(schema)["properties"]["root"]
    for _ in range(chat.REF_DEPTH_LIMIT - 1):
        node = node["properties"]["child"]
    assert node["properties"]["child"] == {"type": "object"}
    assert sanitize_schema({"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}}) == {
        "type": "object",
        "properties": {"x": {"type": "object"}},
    }


def test_nullable_from_any_of_and_type_list() -> None:
    schema = {
        "type": "object",
        "properties": {
            "a": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}], "description": "A"},
            "b": {"type": ["string", "null"], "maxLength": 3},
            "c": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }
    out = sanitize_schema(schema)["properties"]
    assert out["a"] == {"type": "integer", "minimum": 1, "description": "A", "nullable": True}
    assert out["b"] == {"type": "string", "maxLength": 3, "nullable": True}
    assert out["c"] == {"type": "string"}


@pytest.mark.parametrize("schema", [None, {}, {"type": "string"}, {"type": "array", "items": {}}])
def test_root_is_always_an_object(schema: dict[str, Any] | None) -> None:
    assert sanitize_schema(schema) == {"type": "object", "properties": {}}


def test_object_root_without_properties_gets_them() -> None:
    assert sanitize_schema({"type": "object", "required": []}) == {
        "type": "object",
        "required": [],
        "properties": {},
    }


# ---------------------------------------------------------------------------------------------- TEST-U-15

TOOL = types.Tool(
    name="woo-main__list_orders",
    description="[woo-main] List orders",
    input_schema={"type": "object", "properties": {"period": {"type": "string", "title": "Period"}}},
)


def completion(content: str | None = None, calls: list[tuple[str, str, str]] | None = None) -> ChatCompletion:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
            for cid, name, args in calls
        ]
    return ChatCompletion.model_validate(
        {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        }
    )


class FakeLlm:
    """`llm.chat.completions.create` returning scripted completions (or raising scripted errors)."""

    def __init__(self, *replies: ChatCompletion | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> ChatCompletion:
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        reply = self.replies.pop(0) if self.replies else completion(calls=[("again", TOOL.name, "{}")])
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeMcp:
    def __init__(self, result: types.CallToolResult | Exception | None = None) -> None:
        self.result = result or types.CallToolResult(
            content=[types.TextContent(type="text", text='{"orders": [{"number": "42"}]}')]
        )
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[TOOL])

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, read_timeout_seconds: float | None = None
    ) -> types.CallToolResult:
        self.calls.append((name, arguments or {}, read_timeout_seconds))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def run(llm: FakeLlm | chat.LlmKeys, mcp: FakeMcp) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def emit(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    history = [{"role": "user", "content": "What orders came in today?"}]
    now = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)
    keys = llm if isinstance(llm, chat.LlmKeys) else chat.LlmKeys(llm)  # type: ignore[arg-type]
    await chat.run_turn(mcp, keys, "gemini-test", history, emit, now=now)  # type: ignore[arg-type]
    return events


@pytest.mark.anyio
async def test_tool_call_then_answer() -> None:
    llm = FakeLlm(
        completion(calls=[("c1", TOOL.name, '{"period": "today"}')]),
        completion("Order #42 came in today (used woo-main__list_orders)."),
    )
    mcp = FakeMcp()
    events = await run(llm, mcp)

    assert [e for e, _ in events] == ["tool_call", "tool_result", "answer", "done"]
    assert events[0][1] == {"id": "c1", "name": TOOL.name, "arguments": {"period": "today"}}
    result = events[1][1]
    assert result["ok"] is True and result["name"] == TOOL.name and "42" in result["preview"]
    assert events[2][1] == {"text": "Order #42 came in today (used woo-main__list_orders)."}
    assert events[3][1] == {"llmCalls": 2, "toolCalls": 1}
    assert mcp.calls == [(TOOL.name, {"period": "today"}, chat.TOOL_CALL_TIMEOUT_S)]

    first, second = llm.requests
    assert first["model"] == "gemini-test" and first["temperature"] == 0.1
    assert first["messages"][0]["role"] == "system"
    assert "Current time (UTC): 2026-10-16T09:30:00+00:00." in first["messages"][0]["content"]
    assert first["tools"][0]["function"] == {
        "name": TOOL.name,
        "description": "[woo-main] List orders",
        "parameters": {"type": "object", "properties": {"period": {"type": "string"}}},
    }
    assert second["messages"][-2]["role"] == "assistant" and second["messages"][-2]["tool_calls"]
    assert second["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"orders": [{"number": "42"}]}',
    }


@pytest.mark.anyio
async def test_tool_call_limit_stops_the_turn() -> None:
    many = [(f"c{i}", TOOL.name, "{}") for i in range(chat.MAX_TOOL_CALLS + 3)]
    mcp = FakeMcp()
    events = await run(FakeLlm(completion(calls=many)), mcp)
    assert len(mcp.calls) == chat.MAX_TOOL_CALLS
    assert events[-2] == (
        "answer",
        {"text": f"I stopped after {chat.MAX_TOOL_CALLS} tool calls; please narrow the request."},
    )
    assert events[-1] == ("done", {"llmCalls": 1, "toolCalls": chat.MAX_TOOL_CALLS})


@pytest.mark.anyio
async def test_llm_call_limit_stops_the_turn() -> None:
    llm = FakeLlm()  # every reply asks for one more tool call
    events = await run(llm, FakeMcp())
    assert len(llm.requests) == chat.MAX_LLM_CALLS
    assert events[-2] == (
        "answer",
        {"text": f"I stopped after {chat.MAX_LLM_CALLS} tool calls; please narrow the request."},
    )
    assert events[-1] == ("done", {"llmCalls": chat.MAX_LLM_CALLS, "toolCalls": chat.MAX_LLM_CALLS})


@pytest.mark.anyio
async def test_rate_limit_becomes_a_friendly_error() -> None:
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://llm.test/chat/completions"))
    events = await run(FakeLlm(RateLimitError("quota", response=response, body=None)), FakeMcp())
    assert events == [("error", {"message": chat.RATE_LIMITED}), ("done", {"llmCalls": 1, "toolCalls": 0})]


@pytest.mark.anyio
async def test_error_results_are_prefixed_and_reported() -> None:
    failing = types.CallToolResult(
        content=[types.TextContent(type="text", text="upstream timed out")], is_error=True
    )
    llm = FakeLlm(completion(calls=[("c1", TOOL.name, "{}")]), completion("The store did not answer."))
    events = await run(llm, FakeMcp(failing))
    assert events[1][1]["ok"] is False and events[1][1]["preview"] == "ERROR: upstream timed out"
    assert llm.requests[1]["messages"][-1]["content"] == "ERROR: upstream timed out"


@pytest.mark.anyio
async def test_mcp_error_is_not_fatal() -> None:
    llm = FakeLlm(
        completion(calls=[("c1", "woo-main__create_product", "{}")]), completion("You can't do that.")
    )
    events = await run(
        llm, FakeMcp(MCPError(-32010, "Forbidden: woo-main__create_product requires admin role"))
    )
    assert llm.requests[1]["messages"][-1]["content"] == (
        "ERROR: Forbidden: woo-main__create_product requires admin role"
    )
    assert [e for e, _ in events] == ["tool_call", "tool_result", "answer", "done"]


@pytest.mark.anyio
async def test_invalid_arguments_are_not_sent_to_the_tool() -> None:
    llm = FakeLlm(completion(calls=[("c1", TOOL.name, "{not json")]), completion("Sorry."))
    mcp = FakeMcp()
    events = await run(llm, mcp)
    assert mcp.calls == []
    assert events[0][1]["arguments"] == "{not json"
    assert events[1][1]["ok"] is False


def test_tool_content_structured_and_truncated() -> None:
    structured = types.CallToolResult(content=[], structured_content={"gst": 180})
    assert chat.tool_content(structured) == '{"gst": 180}'
    long = types.CallToolResult(content=[types.TextContent(type="text", text="x" * 10_000)])
    assert len(chat.tool_content(long)) == chat.TOOL_CONTENT_LIMIT


# ---------------------------------------------------------------------------------- fallback key (D92)

LLM_URL = "https://llm.test/chat/completions"


def status_error(status: int, message: str = "boom") -> openai.APIStatusError:
    response = httpx2.Response(status, request=httpx2.Request("POST", LLM_URL))
    return openai.APIStatusError(message, response=response, body=None)


def rate_limited() -> RateLimitError:
    response = httpx2.Response(429, request=httpx2.Request("POST", LLM_URL))
    return RateLimitError("quota", response=response, body=None)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (rate_limited(), True),
        (status_error(500), True),
        (status_error(503), True),
        (status_error(401), True),
        (status_error(403), True),
        (status_error(400, "Please pass a valid API key"), True),  # Gemini's answer to a bad key
        (status_error(400, "API key expired. Please renew the API key."), True),
        (status_error(400, "Invalid JSON payload"), False),
        (status_error(404, "model not found"), False),
        (openai.APIConnectionError(request=httpx2.Request("POST", LLM_URL)), True),
        (openai.APITimeoutError(request=httpx2.Request("POST", LLM_URL)), True),
    ],
)
def test_switchable(exc: openai.APIError, expected: bool) -> None:
    assert chat.switchable(exc) is expected


@pytest.mark.anyio
async def test_fallback_key_retries_once_and_stays_for_the_turn() -> None:
    primary = FakeLlm(rate_limited())
    fallback = FakeLlm(completion(calls=[("c1", TOOL.name, "{}")]), completion("Two orders today."))
    keys = chat.LlmKeys(primary, fallback)  # type: ignore[arg-type]
    events = await run(keys, FakeMcp())
    assert events[-2:] == [
        ("answer", {"text": "Two orders today."}),
        ("done", {"llmCalls": 2, "toolCalls": 1}),
    ]
    # one failed attempt on the primary; the same request then went to the fallback, which kept the turn
    assert len(primary.requests) == 1 and len(fallback.requests) == 2
    assert primary.requests[0] == fallback.requests[0]
    assert keys.active == 1


@pytest.mark.anyio
async def test_fallback_failing_too_reports_its_error() -> None:
    primary, fallback = FakeLlm(status_error(503)), FakeLlm(rate_limited())
    events = await run(chat.LlmKeys(primary, fallback), FakeMcp())  # type: ignore[arg-type]
    assert events == [("error", {"message": chat.RATE_LIMITED}), ("done", {"llmCalls": 1, "toolCalls": 0})]
    assert len(primary.requests) == len(fallback.requests) == 1


@pytest.mark.anyio
async def test_bad_request_is_not_retried_on_the_fallback() -> None:
    primary, fallback = FakeLlm(status_error(400, "Invalid JSON payload")), FakeLlm(completion("unused"))
    events = await run(chat.LlmKeys(primary, fallback), FakeMcp())  # type: ignore[arg-type]
    assert events[0] == ("error", {"message": "The LLM request failed: Invalid JSON payload"})
    assert fallback.requests == []


@pytest.mark.anyio
async def test_mid_turn_failure_switches_with_the_full_history() -> None:
    primary = FakeLlm(completion(calls=[("c1", TOOL.name, "{}")]), status_error(500))
    fallback = FakeLlm(completion("Done."))
    events = await run(chat.LlmKeys(primary, fallback), FakeMcp())  # type: ignore[arg-type]
    assert events[-2:] == [("answer", {"text": "Done."}), ("done", {"llmCalls": 2, "toolCalls": 1})]
    assert fallback.requests[0] == primary.requests[1]  # tool call + tool result included

"""POST /api/chat (DESIGN §7.3, §12.1): one chat turn streamed as Server-Sent Events.

`SseResponse` writes events straight to the ASGI `send` from a task group (producer, 15 s keep-alive,
disconnect watcher). This avoids yielding inside a task group from an async generator (what StreamingResponse
would need). A client disconnect cancels the turn.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import anyio
from fastapi import APIRouter, Request
from pydantic import Field, model_validator
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from wmg_common.errors import AppError
from wmg_gateway.agent import chat as agent
from wmg_gateway.http.api.models import ApiModel
from wmg_gateway.http.deps import CurrentUser
from wmg_gateway.runtime import Runtime

router = APIRouter(tags=["chat"])

KEEPALIVE_S = 15.0
NOT_CONFIGURED = "Chat is not configured: set GEMINI_API_KEY in .env and run make secrets-llm"
Producer = Callable[[agent.Emit], Awaitable[None]]


class ChatMessage(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(ApiModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _last_is_user(self) -> ChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("the last message must come from the user")
        return self


def sse_event(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class SseResponse(Response):
    media_type = "text/event-stream"

    def __init__(self, produce: Producer, keepalive_s: float = KEEPALIVE_S) -> None:
        super().__init__(headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        # Response.__init__ adds content-length: 0 for the empty body; a stream must not send one
        self.raw_headers = [(k, v) for k, v in self.raw_headers if k != b"content-length"]
        self.produce = produce
        self.keepalive_s = keepalive_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
        lock = anyio.Lock()
        disconnected = False

        async def write(chunk: bytes) -> None:
            async with lock:
                await send({"type": "http.response.body", "body": chunk, "more_body": True})

        async def emit(event: str, data: dict[str, Any]) -> None:
            await write(sse_event(event, data))

        async with anyio.create_task_group() as tg:

            async def keepalive() -> None:
                while True:
                    await anyio.sleep(self.keepalive_s)
                    await write(b": keep-alive\n\n")

            async def watch_disconnect() -> None:
                nonlocal disconnected
                while (await receive())["type"] != "http.disconnect":
                    pass
                disconnected = True
                tg.cancel_scope.cancel()

            tg.start_soon(keepalive)
            tg.start_soon(watch_disconnect)
            try:
                await self.produce(emit)
            finally:
                tg.cancel_scope.cancel()
        if not disconnected:
            await send({"type": "http.response.body", "body": b"", "more_body": False})


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser, request: Request) -> Response:
    rt: Runtime = request.app.state.runtime
    api_keys = rt.settings.llm_keys()
    if not api_keys:
        raise AppError(503, "LLM_NOT_CONFIGURED", NOT_CONFIGURED)
    history = [{"role": m.role, "content": m.content} for m in body.messages]

    async def produce(emit: agent.Emit) -> None:
        await agent.converse(rt.settings, api_keys, str(user.id), history, emit)

    return SseResponse(produce)

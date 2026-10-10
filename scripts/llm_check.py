"""LLM model selection helper (PLAN P10.1). Reads GEMINI_API_KEY from the environment and never prints it.

  python scripts/llm_check.py models             # which candidate models the key can see
  python scripts/llm_check.py smoke [--model M]  # one function-calling round trip (default: first passing)

Run through `make llm-models` / `make llm-smoke`, which load the key from .env.
"""

import argparse
import asyncio
import os
import sys

import httpx2
from openai import APIStatusError, AsyncOpenAI

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
CANDIDATES = ["gemini-3-flash", "gemini-3-flash-preview", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_gst",
        "description": "Calculate GST on an amount in INR.",
        "parameters": {
            "type": "object",
            "properties": {"amount": {"type": "number"}, "rate": {"type": "number", "enum": [5, 12, 18, 28]}},
            "required": ["amount", "rate"],
        },
    },
}


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        sys.exit("GEMINI_API_KEY is not set (put it in .env)")
    return key


def available_models() -> list[str]:
    response = httpx2.get(MODELS_URL, headers={"x-goog-api-key": api_key()}, timeout=20)
    response.raise_for_status()
    body = response.json()
    return [m["name"].removeprefix("models/") for m in body.get("models", [])]


async def smoke(model: str) -> str | None:
    """One round trip: user → tool_call → tool result → answer. Returns None on success, else the reason."""
    llm = AsyncOpenAI(base_url=BASE_URL, api_key=api_key(), timeout=60.0, max_retries=0)
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "Use tools to get facts."},
        {"role": "user", "content": "What is 18% GST on 1000 rupees? Use the tool."},
    ]
    try:
        first = await llm.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=[TOOL],  # type: ignore[list-item]
            temperature=0.1,
        )
        msg = first.choices[0].message
        if not msg.tool_calls:
            return f"no tool call (answered: {(msg.content or '')[:80]!r})"
        call = msg.tool_calls[0]
        print(f"  tool_call {call.function.name}({call.function.arguments})")  # type: ignore[union-attr]
        messages.append(msg.model_dump(exclude_none=True))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": '{"gst": 180, "total": 1180}'})
        second = await llm.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            tools=[TOOL],  # type: ignore[list-item]
            temperature=0.1,
        )
        answer = second.choices[0].message.content or ""
        print(f"  answer    {answer.strip()[:120]!r}")
        return None if "180" in answer else "answer does not use the tool result"
    except APIStatusError as exc:
        return f"HTTP {exc.status_code}: {str(exc.message)[:160]}"
    finally:
        await llm.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["models", "smoke"])
    parser.add_argument("--model")
    args = parser.parse_args()

    if args.command == "models":
        names = set(available_models())
        for candidate in CANDIDATES:
            print(f"  {'yes' if candidate in names else 'no ':3}  {candidate}")
        return

    for model in [args.model] if args.model else CANDIDATES:
        print(f"{model}:")
        reason = asyncio.run(smoke(model))
        if reason is None:
            print(f"OK - use llm.model: {model}")
            return
        print(f"  FAILED: {reason}")
    sys.exit("no candidate model passed the smoke test")


if __name__ == "__main__":
    main()

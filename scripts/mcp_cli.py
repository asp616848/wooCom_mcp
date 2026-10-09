"""Tiny MCP client for manual checks (DESIGN §10.1).

uv run scripts/mcp_cli.py --url URL --token T [--header X-Api-Key] [--mode auto|legacy|2026-07-28] list
uv run scripts/mcp_cli.py --url URL --token T call NAME '{"arg": 1}'

The token is sent as `Authorization: Bearer T`, or as `<header>: T` when --header is given.
Prefer `--token-env VAR` so the token stays out of shell history.
"""

import argparse
import json
import os
import sys
from typing import Any

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    auth = p.add_mutually_exclusive_group(required=True)
    auth.add_argument("--token")
    auth.add_argument("--token-env", help="read the token from this environment variable")
    p.add_argument("--header", help="send the token in this header instead of Authorization: Bearer")
    p.add_argument("--mode", default="auto", help="auto | legacy | 2026-07-28")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    call = sub.add_parser("call")
    call.add_argument("name")
    call.add_argument("arguments", nargs="?", default="{}")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    token = args.token if args.token is not None else os.environ.get(args.token_env, "")
    headers = {args.header: token} if args.header else {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30) as http,
        Client(streamable_http_client(args.url, http_client=http), mode=args.mode) as mcp,
    ):
        if args.cmd == "list":
            for tool in (await mcp.list_tools()).tools:
                print(f"{tool.name:24} {tool.title or ''}")
            return 0
        result = await mcp.call_tool(args.name, json.loads(args.arguments))
        out: Any = result.structured_content
        if out is None:
            out = [getattr(block, "text", str(block)) for block in result.content]
        print(json.dumps({"is_error": result.is_error, "result": out}, indent=2, ensure_ascii=False))
        return 1 if result.is_error else 0


if __name__ == "__main__":
    sys.exit(anyio.run(run, parse()))

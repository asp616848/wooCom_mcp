"""Fake WooCommerce store (httpx2.MockTransport) + woo-mcp wired to it.

Tests run with `--import-mode=importlib`, so they can't import from this file: everything is a fixture,
and tests annotate the fake store as `Any`.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2
import pytest

from woo_mcp.settings import WooMcpSettings
from woo_mcp.wc import WooClient

TOKEN = "test-mcp-token-0123456789"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_settings(**overrides: Any) -> WooMcpSettings:
    values: dict[str, Any] = {
        "store_slug": "main",
        "wc_internal_url": "http://main-wordpress",
        "wc_public_host": "shop.localhost",
        "wc_consumer_key": "ck_test_0123456789",
        "wc_consumer_secret": "cs_test_secret_9876",
        "mcp_auth_token": TOKEN,
        "mcp_allowed_hosts": "main-woo-mcp,main-woo-mcp:*",
    } | overrides
    return WooMcpSettings(**values)


Handler = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class FakeStore:
    """Route table keyed by (METHOD, path); records every request it receives."""

    routes: dict[tuple[str, str], Handler] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)

    def on(
        self,
        method: str,
        path: str,
        body: Any = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.routes[(method, path)] = lambda _req: httpx2.Response(status, json=body, headers=headers)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx2.Response(404, json={"code": "rest_no_route", "message": "No route"})
        return handler(request)

    def last(self, method: str, path: str) -> httpx2.Request:
        return next(r for r in reversed(self.requests) if r.method == method and r.url.path == path)

    def json_body(self, method: str, path: str) -> Any:
        return json.loads(self.last(method, path).content)


SITE = {
    "name": "Main Store",
    "url": "http://shop.localhost",
    "timezone_string": "Asia/Kolkata",
    "gmt_offset": 5.5,
}
GENERAL = [
    {"id": "woocommerce_currency", "value": "INR"},
    {"id": "woocommerce_default_country", "value": "IN:MH"},
]


@pytest.fixture
def store() -> FakeStore:
    s = FakeStore()
    s.on("GET", "/wp-json/", SITE)
    s.on("GET", "/wp-json/wc/v3/settings/general", GENERAL)
    return s


SettingsFactory = Callable[..., WooMcpSettings]


@pytest.fixture
def settings_factory() -> SettingsFactory:
    return make_settings


@pytest.fixture
def client(store: FakeStore) -> WooClient:
    return WooClient(make_settings(), transport=httpx2.MockTransport(store.handle))

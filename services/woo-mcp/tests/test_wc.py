from typing import Any

import httpx2
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from woo_mcp.wc import WooClient, oauth_params

pytestmark = pytest.mark.anyio


def test_signature_matches_reference() -> None:
    # Reference value computed by the P3-verified algorithm in scripts/wc-oauth-check.ts (Node) for the same
    # inputs; the search value exercises RFC 3986 escaping of space, &, %, ( ) and !.
    params = oauth_params(
        "GET",
        "/products",
        {"per_page": "2", "orderby": "title", "search": "Tee & mug (50%)!"},
        consumer_key="ck_test_0123456789",
        consumer_secret="cs_test_secret_9876",
        public_host="shop.localhost",
        nonce="abc123",
        timestamp=1791500000,
    )
    assert params["oauth_signature"] == "/aDm4zgZGzXAfIaQuAqznKjhmmDEbfeKSG5Pg6dyRDk="
    assert params["oauth_signature_method"] == "HMAC-SHA256"
    assert params["per_page"] == "2"


def test_signature_changes_with_method_and_host() -> None:
    common = {
        "consumer_key": "ck_x_0123456789",
        "consumer_secret": "cs_y_0123456789",
        "nonce": "n",
        "timestamp": 1,
    }
    a = oauth_params("GET", "/orders", {}, public_host="shop.localhost", **common)  # type: ignore[arg-type]
    b = oauth_params("POST", "/orders", {}, public_host="shop.localhost", **common)  # type: ignore[arg-type]
    c = oauth_params("GET", "/orders", {}, public_host="shop.example.com", **common)  # type: ignore[arg-type]
    assert len({a["oauth_signature"], b["oauth_signature"], c["oauth_signature"]}) == 3


async def test_request_signs_query_and_sets_host(store: Any, client: WooClient) -> None:
    store.on(
        "GET", "/wp-json/wc/v3/products", [{"id": 1}], headers={"X-WP-Total": "7", "X-WP-TotalPages": "4"}
    )
    res = await client.request("GET", "/products", query={"per_page": 2, "search": None, "on_sale": True})
    req = store.last("GET", "/wp-json/wc/v3/products")
    assert req.headers["host"] == "shop.localhost"
    assert req.url.host == "main-wordpress"
    q = dict(req.url.params)
    assert q["per_page"] == "2" and q["on_sale"] == "true" and "search" not in q
    assert {"oauth_consumer_key", "oauth_nonce", "oauth_timestamp", "oauth_signature"} <= q.keys()
    assert (res.data, res.total, res.total_pages) == ([{"id": 1}], 7, 4)


async def test_json_body_sent_unsigned(store: Any, client: WooClient) -> None:
    store.on("POST", "/wp-json/wc/v3/products", {"id": 9})
    await client.request("POST", "/products", json_body={"name": "Mug"})
    assert store.json_body("POST", "/wp-json/wc/v3/products") == {"name": "Mug"}


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (
            400,
            {"code": "rest_invalid_param", "message": "Invalid parameter(s): status"},
            "Invalid request: Invalid parameter(s): status",
        ),
        (
            401,
            {"code": "woocommerce_rest_cannot_view"},
            "Store rejected the credentials (check setup job / REST key)",
        ),
        (403, None, "Store rejected the credentials (check setup job / REST key)"),
        (404, {"code": "woocommerce_rest_invalid_id"}, "Not found: order 42"),
        (500, {"message": "boom"}, "Store error (HTTP 500): boom"),
    ],
)
async def test_error_mapping(store: Any, client: WooClient, status: int, body: object, message: str) -> None:
    store.on("GET", "/wp-json/wc/v3/orders/42", body, status=status)
    with pytest.raises(ToolError) as exc:
        await client.request("GET", "/orders/42", entity="order", entity_id=42)
    assert str(exc.value) == message
    assert "oauth" not in str(exc.value)


async def test_timeout_and_connection_errors(settings_factory: Any) -> None:
    def timeout(_req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow")

    def refused(_req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    slow = WooClient(settings_factory(), transport=httpx2.MockTransport(timeout))
    with pytest.raises(ToolError, match=r"^Store did not respond within 10s$"):
        await slow.request("GET", "/orders")
    down = WooClient(settings_factory(), transport=httpx2.MockTransport(refused))
    with pytest.raises(ToolError, match=r"^Store unavailable$"):
        await down.request("GET", "/orders")

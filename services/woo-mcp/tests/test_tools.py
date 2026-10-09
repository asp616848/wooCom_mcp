"""Tools through a real MCP client connected in-process (schemas, validation, Woo calls)."""

from typing import Any

import pytest
from mcp import Client

from woo_mcp.tools import build_server
from woo_mcp.wc import WooClient

pytestmark = pytest.mark.anyio

TOOLS = {
    "get_store_info",
    "list_products",
    "get_product",
    "create_product",
    "update_product",
    "list_orders",
    "get_order",
    "update_order_status",
}

PRODUCT = {
    "id": 21,
    "name": "Ceramic Coffee Mug",
    "sku": "",
    "status": "publish",
    "type": "simple",
    "price": "349",
    "regular_price": "349",
    "sale_price": "",
    "stock_status": "instock",
    "stock_quantity": None,
    "permalink": "http://shop.localhost/product/ceramic-coffee-mug/",
    "description": "<p>Holds 350&nbsp;ml.</p>",
    "short_description": "",
    "categories": [{"id": 15, "name": "Uncategorized"}],
    "date_created": "2026-10-09T12:00:00",
}
ORDER = {
    "id": 16,
    "number": "16",
    "status": "processing",
    "date_created": "2026-10-09T18:30:00",
    "total": "349.00",
    "currency": "INR",
    "billing": {
        "first_name": "Test",
        "last_name": "Shopper",
        "email": "shopper@example.com",
        "city": "Mumbai",
    },
    "shipping": {"first_name": "Test", "last_name": "Shopper", "city": "Mumbai"},
    "payment_method_title": "Cash on delivery",
    "line_items": [
        {"product_id": 21, "name": "Ceramic Coffee Mug", "quantity": 1, "price": 349, "total": "349.00"}
    ],
}


async def call(client: WooClient, name: str, args: dict[str, Any]) -> Any:
    async with Client(build_server(client)) as mcp:
        return await mcp.call_tool(name, args)


async def test_lists_eight_tools_with_schemas(client: WooClient) -> None:
    async with Client(build_server(client)) as mcp:
        tools = {t.name: t for t in (await mcp.list_tools()).tools}
    assert set(tools) == TOOLS
    assert all(t.output_schema for t in tools.values())
    assert tools["list_orders"].annotations.read_only_hint is True  # type: ignore[union-attr]
    assert tools["update_order_status"].annotations.destructive_hint is True  # type: ignore[union-attr]
    price = tools["create_product"].input_schema["properties"]["regular_price"]
    assert price["pattern"] == r"^\d{1,9}(\.\d{1,2})?$"


async def test_get_store_info(store: Any, client: WooClient) -> None:
    store.on(
        "GET", "/wp-json/wc/v3/system_status", {"environment": {"version": "11.1.2", "wp_version": "7.1.3"}}
    )
    r = await call(client, "get_store_info", {})
    assert not r.is_error
    assert r.structured_content == {
        "name": "Main Store",
        "url": "http://shop.localhost",
        "currency": "INR",
        "timezone": "Asia/Kolkata",
        "woocommerce_version": "11.1.2",
        "wordpress_version": "7.1.3",
    }


async def test_list_and_get_product(store: Any, client: WooClient) -> None:
    store.on("GET", "/wp-json/wc/v3/products", [PRODUCT], headers={"X-WP-Total": "5", "X-WP-TotalPages": "1"})
    store.on("GET", "/wp-json/wc/v3/products/21", PRODUCT)
    r = await call(client, "list_products", {"search": "mug", "per_page": 5})
    assert r.structured_content["total"] == 5 and r.structured_content["products"][0]["id"] == 21
    q = store.last("GET", "/wp-json/wc/v3/products").url.params
    assert (q["search"], q["per_page"], q["status"]) == ("mug", "5", "any")
    r = await call(client, "get_product", {"id": 21})
    assert r.structured_content["description"] == "Holds 350 ml."
    assert r.structured_content["categories"] == ["Uncategorized"]


async def test_create_product_body(store: Any, client: WooClient) -> None:
    store.on("POST", "/wp-json/wc/v3/products", PRODUCT | {"id": 30, "name": "Jute Bag"})
    r = await call(
        client, "create_product", {"name": "Jute Bag", "regular_price": "299.50", "stock_quantity": 4}
    )
    assert not r.is_error and r.structured_content["id"] == 30
    assert store.json_body("POST", "/wp-json/wc/v3/products") == {
        "name": "Jute Bag",
        "type": "simple",
        "regular_price": "299.50",
        "status": "publish",
        "manage_stock": True,
        "stock_quantity": 4,
    }


@pytest.mark.parametrize("price", ["12.345", "-5", "abc", "1e3", ""])
async def test_create_product_rejects_bad_price(store: Any, client: WooClient, price: str) -> None:
    r = await call(client, "create_product", {"name": "X", "regular_price": price})
    assert r.is_error
    assert not any(req.method == "POST" for req in store.requests)


async def test_update_product_needs_a_field(store: Any, client: WooClient) -> None:
    r = await call(client, "update_product", {"id": 21})
    assert r.is_error and "at least one field" in r.content[0].text
    store.on("PUT", "/wp-json/wc/v3/products/21", PRODUCT | {"sale_price": ""})
    r = await call(client, "update_product", {"id": 21, "sale_price": ""})
    assert not r.is_error
    assert store.json_body("PUT", "/wp-json/wc/v3/products/21") == {"sale_price": ""}


async def test_list_orders_today_in_store_timezone(store: Any, client: WooClient) -> None:
    store.on(
        "GET", "/wp-json/wc/v3/orders", [ORDER, ORDER], headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"}
    )
    r = await call(client, "list_orders", {"period": "today", "status": "processing"})
    sc = r.structured_content
    assert sc["timezone"] == "Asia/Kolkata"
    assert sc["summary"] == {"count_on_page": 2, "total_on_page": "698.00"}
    assert sc["range"]["from"] == sc["range"]["to"]
    q = store.last("GET", "/wp-json/wc/v3/orders").url.params
    assert q["dates_are_gmt"] == "false" and q["status"] == "processing"
    assert q["after"].endswith("T23:59:59") and q["before"].endswith("T00:00:00")


async def test_list_orders_period_xor_dates(client: WooClient) -> None:
    r = await call(client, "list_orders", {"period": "today", "date_from": "2026-10-01"})
    assert r.is_error and "either period" in r.content[0].text


async def test_get_order_not_found(store: Any, client: WooClient) -> None:
    store.on(
        "GET", "/wp-json/wc/v3/orders/999", {"code": "woocommerce_rest_shop_order_invalid_id"}, status=404
    )
    r = await call(client, "get_order", {"id": 999})
    assert r.is_error and "Not found: order 999" in r.content[0].text


async def test_update_order_status_changes_and_notes(store: Any, client: WooClient) -> None:
    store.on("GET", "/wp-json/wc/v3/orders/16", ORDER)
    store.on("PUT", "/wp-json/wc/v3/orders/16", ORDER | {"status": "completed"})
    store.on("POST", "/wp-json/wc/v3/orders/16/notes", {"id": 1})
    r = await call(client, "update_order_status", {"id": 16, "status": "completed", "note": "Delivered"})
    assert r.structured_content == {"id": 16, "previous_status": "processing", "status": "completed"}
    assert store.json_body("PUT", "/wp-json/wc/v3/orders/16") == {"status": "completed"}
    assert store.json_body("POST", "/wp-json/wc/v3/orders/16/notes") == {
        "note": "Status changed to completed via MCP gateway: Delivered",
        "customer_note": False,
    }


async def test_update_order_status_same_status_is_noop(store: Any, client: WooClient) -> None:
    store.on("GET", "/wp-json/wc/v3/orders/16", ORDER)
    r = await call(client, "update_order_status", {"id": 16, "status": "processing"})
    assert r.structured_content["previous_status"] == "processing"
    assert [req.method for req in store.requests if "/orders/16" in req.url.path] == ["GET"]

"""SO-4 search mode scoring (DESIGN §5.9, D93) over a catalog shaped like the real one."""

from dataclasses import replace
from typing import Any

from wmg_gateway.catalog.catalog import Catalog, ServerEntry, ToolEntry
from wmg_gateway.mcp.search import schema_text, score, search, tokens

from .test_call_tool import SERVER

WOO = replace(SERVER, id="00000000-0000-0000-0000-0000000000a1", slug="woo-main", name="Main Store")
GST = replace(SERVER, id="00000000-0000-0000-0000-0000000000a2", slug="gst", name="GST")
DEAD = replace(SERVER, id="00000000-0000-0000-0000-0000000000a3", slug="pincode", status="unreachable")

ID = {"type": "object", "properties": {"id": {"type": "integer", "description": "WooCommerce ID"}}}
ORDERS = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["any", "pending", "processing", "completed"]},
        "period": {
            "type": "string",
            "enum": ["today", "yesterday", "last_7_days", "last_30_days"],
            "description": "In the store's timezone",
        },
        "customer_email": {"type": "string"},
    },
}


def entry(
    server: ServerEntry, name: str, title: str, description: str, schema: Any = None, **kw: Any
) -> ToolEntry:
    return ToolEntry(
        server=server,
        original=name,
        exposed=f"{server.slug}__{name}",
        title=title,
        description=description,
        input_schema=schema or {"type": "object"},
        output_schema=None,
        annotations=None,
        admin_only=kw.get("admin_only", False),
    )


TOOLS = [
    entry(WOO, "get_store_info", "Get store info", "Store name, URL, currency, timezone and versions."),
    entry(WOO, "list_products", "List products", "Search and page through products."),
    entry(WOO, "get_product", "Get product", "One product with its description and categories.", ID),
    entry(WOO, "create_product", "Create product", "Create a simple product.", admin_only=True),
    entry(WOO, "list_orders", "List orders", "Orders, newest first, filtered by status and date.", ORDERS),
    entry(WOO, "get_order", "Get order", "One order with customer, addresses, line items and totals.", ID),
    entry(WOO, "update_order_status", "Update order status", "Change an order status.", admin_only=True),
    entry(GST, "calculate_gst", "Calculate GST", "GST on an amount in INR at 5, 12, 18 or 28 percent."),
    entry(DEAD, "check_pincode", "Check pincode", "Delivery estimate for an Indian pincode and orders."),
]
CATALOG = Catalog(
    servers={s.id: s for s in (WOO, GST, DEAD)},
    tools_by_exposed={t.exposed: t for t in TOOLS},
    tools_by_server={},
)


def names(found: list[ToolEntry]) -> list[str]:
    return [t.exposed for t in found]


def test_tokens_drop_plural_s_on_longer_words() -> None:
    assert tokens("List orders, today's GST!") == {"list", "order", "today", "s", "gst"}
    assert tokens("Bus is gas") == {"bus", "is", "gas"}  # 3 letters or fewer keep their s
    assert tokens(None) == set()


def test_schema_text_reads_names_descriptions_and_enums() -> None:
    words = tokens(schema_text(ORDERS))
    assert {"period", "today", "yesterday", "timezone", "customer", "email", "pending"} <= words


def test_SO4_01_orders_today_ranks_list_orders_first() -> None:
    found = search(CATALOG, "member", "orders today")
    assert names(found)[0] == "woo-main__list_orders"
    # get_order ties on name/title/description; only list_orders' schema mentions "today"
    assert score(TOOLS[4], tokens("orders today")) == score(TOOLS[5], tokens("orders today")) + 1


def test_visibility_follows_role_and_liveness() -> None:
    member = names(search(CATALOG, "member", "order status", limit=20))
    admin = names(search(CATALOG, "admin", "order status", limit=20))
    assert "woo-main__update_order_status" not in member and "woo-main__update_order_status" in admin
    assert not any(n.startswith("pincode__") for n in admin)  # unreachable server


def test_no_match_and_limit() -> None:
    assert search(CATALOG, "admin", "weather forecast") == []
    assert names(search(CATALOG, "admin", "product", limit=2)) == [
        "woo-main__create_product",
        "woo-main__get_product",
    ]
    assert names(search(CATALOG, "member", "gst 18 percent")) == ["gst__calculate_gst"]

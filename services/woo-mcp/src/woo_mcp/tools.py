"""The 8 WooCommerce tools (DESIGN §9.1). `build_server(client)` takes the client so tests can fake it."""

import time
from datetime import date
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from woo_mcp import APP_VERSION
from woo_mcp.models import (
    DateRangeOut,
    OrderDetail,
    OrderList,
    OrderStatusChange,
    PageSummary,
    ProductDetail,
    ProductList,
    ProductSummary,
    StoreInfo,
    order_detail,
    order_summary,
    page_total,
    product_detail,
    product_summary,
)
from woo_mcp.tz import Period, resolve_range, store_tz, woo_date_query
from woo_mcp.wc import WooClient

STORE_INFO_TTL_S = 600
PRICE_PATTERN = r"^\d{1,9}(\.\d{1,2})?$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

Id = Annotated[int, Field(ge=1, description="WooCommerce ID")]
Page = Annotated[int, Field(ge=1, description="Page number (1-based)")]
PerPage = Annotated[int, Field(ge=1, le=50, description="Results per page (max 50)")]
Price = Annotated[
    str, Field(pattern=PRICE_PATTERN, description="Price in store currency, e.g. 799 or 349.50")
]


class StoreInfoCache:
    """Site basics change rarely: cache name/url/timezone/currency for 10 min; versions fetched on demand."""

    def __init__(self, client: WooClient) -> None:
        self._client = client
        self._base: dict[str, Any] | None = None
        self._fetched_at = 0.0

    async def base(self) -> dict[str, Any]:
        if self._base is None or time.monotonic() - self._fetched_at > STORE_INFO_TTL_S:
            site = await self._client.get_public("/wp-json/")
            general = await self._client.request("GET", "/settings/general", entity="settings")
            currency = next(
                (str(s.get("value")) for s in general.data if s.get("id") == "woocommerce_currency"), ""
            )
            tz, tz_name = store_tz(str(site.get("timezone_string") or ""), float(site.get("gmt_offset") or 0))
            self._base = {
                "name": str(site.get("name") or ""),
                "url": str(site.get("url") or site.get("home") or ""),
                "currency": currency,
                "tz": tz,
                "timezone": tz_name,
            }
            self._fetched_at = time.monotonic()
        return self._base


def build_server(client: WooClient) -> MCPServer:
    mcp = MCPServer(
        "woo-mcp",
        version=APP_VERSION,
        instructions=(
            "Tools for one WooCommerce store: products and orders. Prices are strings in the store currency."
        ),
        subscriptions=False,
    )
    info = StoreInfoCache(client)

    @mcp.tool(title="Get store info", annotations=READ_ONLY)
    async def get_store_info() -> StoreInfo:
        """Store name, URL, currency, timezone and WooCommerce/WordPress versions."""
        base = await info.base()
        status = await client.request("GET", "/system_status", entity="system status")
        env = status.data.get("environment") or {}
        return StoreInfo(
            name=base["name"],
            url=base["url"],
            currency=base["currency"],
            timezone=base["timezone"],
            woocommerce_version=str(env.get("version") or ""),
            wordpress_version=str(env.get("wp_version") or ""),
        )

    @mcp.tool(title="List products", annotations=READ_ONLY)
    async def list_products(
        search: Annotated[str | None, Field(max_length=100, description="Text to search in names")] = None,
        sku: Annotated[str | None, Field(max_length=100, description="Exact SKU")] = None,
        status: Literal["any", "publish", "draft", "pending", "private"] = "any",
        page: Page = 1,
        per_page: PerPage = 20,
        orderby: Literal["date", "title", "price", "popularity"] = "date",
        order: Literal["asc", "desc"] = "desc",
    ) -> ProductList:
        """Search and page through products."""
        res = await client.request(
            "GET",
            "/products",
            query={
                "search": search,
                "sku": sku,
                "status": status,
                "page": page,
                "per_page": per_page,
                "orderby": orderby,
                "order": order,
            },
            entity="products",
        )
        products = [product_summary(p) for p in res.data]
        return ProductList(
            products=products,
            page=page,
            per_page=per_page,
            total=res.total if res.total is not None else len(products),
            total_pages=res.total_pages if res.total_pages is not None else 1,
        )

    @mcp.tool(title="Get product", annotations=READ_ONLY)
    async def get_product(id: Id) -> ProductDetail:
        """One product with its description and categories."""
        res = await client.request("GET", f"/products/{id}", entity="product", entity_id=id)
        return product_detail(res.data)

    @mcp.tool(
        title="Create product",
        annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=False, open_world_hint=False),
    )
    async def create_product(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        regular_price: Price,
        description: Annotated[str | None, Field(max_length=5000)] = None,
        short_description: Annotated[str | None, Field(max_length=1000)] = None,
        sku: Annotated[str | None, Field(max_length=100)] = None,
        status: Literal["publish", "draft"] = "publish",
        stock_quantity: Annotated[int | None, Field(ge=0, description="Sets stock tracking on")] = None,
    ) -> ProductSummary:
        """Create a simple product. Returns it with its storefront permalink."""
        body: dict[str, Any] = {
            "name": name,
            "type": "simple",
            "regular_price": regular_price,
            "status": status,
        }
        if description is not None:
            body["description"] = description
        if short_description is not None:
            body["short_description"] = short_description
        if sku is not None:
            body["sku"] = sku
        if stock_quantity is not None:
            body |= {"manage_stock": True, "stock_quantity": stock_quantity}
        res = await client.request("POST", "/products", json_body=body, entity="product")
        return product_summary(res.data)

    @mcp.tool(
        title="Update product",
        annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False),
    )
    async def update_product(
        id: Id,
        name: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        regular_price: Price | None = None,
        sale_price: Annotated[
            str | None, Field(pattern=r"^$|" + PRICE_PATTERN, description='Sale price, or "" to remove it')
        ] = None,
        status: Literal["publish", "draft", "private"] | None = None,
        stock_quantity: Annotated[int | None, Field(ge=0, description="Sets stock tracking on")] = None,
    ) -> ProductSummary:
        """Change a product's name, prices, status or stock. Give at least one field."""
        body: dict[str, Any] = {
            k: v
            for k, v in {
                "name": name,
                "regular_price": regular_price,
                "sale_price": sale_price,
                "status": status,
            }.items()
            if v is not None
        }
        if stock_quantity is not None:
            body |= {"manage_stock": True, "stock_quantity": stock_quantity}
        if not body:
            raise ToolError("Provide at least one field to update")
        res = await client.request("PUT", f"/products/{id}", json_body=body, entity="product", entity_id=id)
        return product_summary(res.data)

    @mcp.tool(title="List orders", annotations=READ_ONLY)
    async def list_orders(
        status: Literal[
            "any", "pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"
        ] = "any",
        period: Annotated[Period | None, Field(description="In the store's timezone")] = None,
        date_from: Annotated[
            date | None, Field(description="YYYY-MM-DD, inclusive (not with period)")
        ] = None,
        date_to: Annotated[date | None, Field(description="YYYY-MM-DD, inclusive (not with period)")] = None,
        customer_email: Annotated[str | None, Field(max_length=254, pattern=EMAIL_PATTERN)] = None,
        page: Page = 1,
        per_page: PerPage = 20,
    ) -> OrderList:
        """Orders, newest first, filtered by status and date (store timezone), with a page total."""
        base = await info.base()
        rng = resolve_range(period, date_from, date_to, base["tz"])
        res = await client.request(
            "GET",
            "/orders",
            query={
                "status": status,
                "search": customer_email,
                "page": page,
                "per_page": per_page,
                "orderby": "date",
                "order": "desc",
                **woo_date_query(rng),
            },
            entity="orders",
        )
        orders = [order_summary(o) for o in res.data]
        return OrderList(
            orders=orders,
            summary=PageSummary(count_on_page=len(orders), total_on_page=page_total(orders)),
            timezone=base["timezone"],
            range=None
            if rng is None
            else DateRangeOut(
                from_=rng.start.isoformat() if rng.start else None,
                to=rng.end.isoformat() if rng.end else None,
            ),
            page=page,
            per_page=per_page,
            total=res.total if res.total is not None else len(orders),
            total_pages=res.total_pages if res.total_pages is not None else 1,
        )

    @mcp.tool(title="Get order", annotations=READ_ONLY)
    async def get_order(id: Id) -> OrderDetail:
        """One order with customer, addresses, line items and totals."""
        res = await client.request("GET", f"/orders/{id}", entity="order", entity_id=id)
        return order_detail(res.data)

    @mcp.tool(
        title="Update order status",
        annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False),
    )
    async def update_order_status(
        id: Id,
        status: Literal["pending", "processing", "on-hold", "completed", "cancelled"],
        note: Annotated[str | None, Field(max_length=500, description="Optional private note")] = None,
    ) -> OrderStatusChange:
        """Change an order's status and record a private order note. No-op if already in that status."""
        current = await client.request("GET", f"/orders/{id}", entity="order", entity_id=id)
        previous = str(current.data.get("status") or "")
        if previous != status:
            await client.request(
                "PUT", f"/orders/{id}", json_body={"status": status}, entity="order", entity_id=id
            )
            text = f"Status changed to {status} via MCP gateway" + (f": {note}" if note else "")
            await client.request(
                "POST",
                f"/orders/{id}/notes",
                json_body={"note": text, "customer_note": False},
                entity="order",
                entity_id=id,
            )
        return OrderStatusChange(id=id, previous_status=previous, status=status)

    return mcp

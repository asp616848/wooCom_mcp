"""Tool output models (become each tool's `outputSchema` and `structuredContent`) + mapping from Woo JSON."""

import html
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def strip_html(value: str | None, limit: int) -> str:
    """Plain text for the model: drop tags, decode entities, collapse whitespace, cap length."""
    text = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", value or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _s(value: Any) -> str:
    return "" if value is None else str(value)


# ---------------------------------------------------------------- store
class StoreInfo(BaseModel):
    name: str
    url: str
    currency: str
    timezone: str
    woocommerce_version: str
    wordpress_version: str


# ---------------------------------------------------------------- products
class ProductSummary(BaseModel):
    id: int
    name: str
    sku: str
    status: str
    type: str
    price: str
    regular_price: str
    sale_price: str
    stock_status: str
    stock_quantity: int | None
    permalink: str


class ProductDetail(ProductSummary):
    description: str
    short_description: str
    categories: list[str]
    date_created: str


class ProductList(BaseModel):
    products: list[ProductSummary]
    page: int
    per_page: int
    total: int
    total_pages: int


def product_summary(p: dict[str, Any]) -> ProductSummary:
    return ProductSummary(
        id=p["id"],
        name=_s(p.get("name")),
        sku=_s(p.get("sku")),
        status=_s(p.get("status")),
        type=_s(p.get("type")),
        price=_s(p.get("price")),
        regular_price=_s(p.get("regular_price")),
        sale_price=_s(p.get("sale_price")),
        stock_status=_s(p.get("stock_status")),
        stock_quantity=p.get("stock_quantity"),
        permalink=_s(p.get("permalink")),
    )


def product_detail(p: dict[str, Any]) -> ProductDetail:
    return ProductDetail(
        **product_summary(p).model_dump(),
        description=strip_html(p.get("description"), 2000),
        short_description=strip_html(p.get("short_description"), 1000),
        categories=[_s(c.get("name")) for c in p.get("categories") or []],
        date_created=_s(p.get("date_created")),
    )


# ---------------------------------------------------------------- orders
class OrderItem(BaseModel):
    name: str
    quantity: int
    total: str


class OrderSummary(BaseModel):
    id: int
    number: str
    status: str
    date_created: str
    total: str
    currency: str
    customer_name: str
    billing_email: str
    payment_method_title: str
    items: list[OrderItem]


class PageSummary(BaseModel):
    count_on_page: int
    total_on_page: str


class DateRangeOut(BaseModel):
    # "from" is a Python keyword: the field is `from_` in code and "from" on the wire.
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    from_: str | None = Field(alias="from")
    to: str | None


class OrderList(BaseModel):
    orders: list[OrderSummary]
    summary: PageSummary
    timezone: str
    range: DateRangeOut | None
    page: int
    per_page: int
    total: int
    total_pages: int


class BillingAddress(BaseModel):
    name: str
    email: str
    phone: str
    city: str
    state: str
    country: str
    postcode: str


class ShippingAddress(BaseModel):
    name: str
    city: str
    state: str
    country: str
    postcode: str


class LineItem(BaseModel):
    product_id: int
    name: str
    sku: str
    quantity: int
    price: str
    total: str


class OrderDetail(BaseModel):
    id: int
    number: str
    status: str
    date_created: str
    date_modified: str
    date_paid: str | None
    date_completed: str | None
    currency: str
    total: str
    total_tax: str
    shipping_total: str
    discount_total: str
    payment_method_title: str
    billing: BillingAddress
    shipping: ShippingAddress
    line_items: list[LineItem]
    customer_note: str


class OrderStatusChange(BaseModel):
    id: int
    previous_status: str
    status: str


def _name(addr: dict[str, Any]) -> str:
    return " ".join(x for x in (_s(addr.get("first_name")), _s(addr.get("last_name"))) if x)


def order_summary(o: dict[str, Any]) -> OrderSummary:
    billing = o.get("billing") or {}
    return OrderSummary(
        id=o["id"],
        number=_s(o.get("number")),
        status=_s(o.get("status")),
        date_created=_s(o.get("date_created")),
        total=_s(o.get("total")),
        currency=_s(o.get("currency")),
        customer_name=_name(billing),
        billing_email=_s(billing.get("email")),
        payment_method_title=_s(o.get("payment_method_title")),
        items=[
            OrderItem(name=_s(i.get("name")), quantity=int(i.get("quantity") or 0), total=_s(i.get("total")))
            for i in o.get("line_items") or []
        ],
    )


def page_total(orders: list[OrderSummary]) -> str:
    total = Decimal(0)
    for o in orders:
        try:
            total += Decimal(o.total or "0")
        except InvalidOperation:
            continue
    return f"{total:.2f}"


def order_detail(o: dict[str, Any]) -> OrderDetail:
    billing = o.get("billing") or {}
    shipping = o.get("shipping") or {}
    return OrderDetail(
        id=o["id"],
        number=_s(o.get("number")),
        status=_s(o.get("status")),
        date_created=_s(o.get("date_created")),
        date_modified=_s(o.get("date_modified")),
        date_paid=o.get("date_paid"),
        date_completed=o.get("date_completed"),
        currency=_s(o.get("currency")),
        total=_s(o.get("total")),
        total_tax=_s(o.get("total_tax")),
        shipping_total=_s(o.get("shipping_total")),
        discount_total=_s(o.get("discount_total")),
        payment_method_title=_s(o.get("payment_method_title")),
        billing=BillingAddress(
            name=_name(billing),
            **{k: _s(billing.get(k)) for k in ("email", "phone", "city", "state", "country", "postcode")},
        ),
        shipping=ShippingAddress(
            name=_name(shipping), **{k: _s(shipping.get(k)) for k in ("city", "state", "country", "postcode")}
        ),
        line_items=[
            LineItem(
                product_id=int(i.get("product_id") or 0),
                name=_s(i.get("name")),
                sku=_s(i.get("sku")),
                quantity=int(i.get("quantity") or 0),
                price=_s(i.get("price")),
                total=_s(i.get("total")),
            )
            for i in o.get("line_items") or []
        ],
        customer_note=_s(o.get("customer_note")),
    )

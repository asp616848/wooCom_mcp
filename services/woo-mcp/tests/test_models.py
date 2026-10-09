from woo_mcp.models import order_summary, page_total, strip_html


def test_strip_html() -> None:
    assert strip_html("<p>Soft &amp; <b>warm</b></p>\n<ul><li>cotton</li></ul>", 100) == "Soft & warm cotton"
    assert strip_html(None, 10) == ""
    long = strip_html("x" * 50, 10)
    assert len(long) == 10 and long.endswith("…")


def test_order_summary_and_page_total() -> None:
    o = order_summary(
        {
            "id": 16,
            "number": "16",
            "status": "processing",
            "total": "349.00",
            "currency": "INR",
            "billing": {"first_name": "Ritika", "last_name": "Sharma", "email": "ritika@example.com"},
            "line_items": [{"name": "Ceramic Coffee Mug", "quantity": 1, "total": "349.00"}],
        }
    )
    assert o.customer_name == "Ritika Sharma" and o.items[0].name == "Ceramic Coffee Mug"
    assert page_total([o, o]) == "698.00"

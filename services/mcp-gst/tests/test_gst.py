"""TEST-U-13: GST math, CGST/SGST vs IGST split, invoice totals, input bounds."""

from decimal import Decimal
from typing import Any

import pytest
from mcp import Client

from mcp_gst.calc import compute
from mcp_gst.tools import build_server

pytestmark = pytest.mark.anyio


async def call(name: str, args: dict[str, Any]) -> Any:
    async with Client(build_server("MH")) as mcp:
        return await mcp.call_tool(name, args)


async def test_exclusive_intra_state() -> None:
    r = await call("calculate_gst", {"amount": 1000, "rate": 18})
    sc = r.structured_content
    assert (sc["taxable_value"], sc["gst_amount"], sc["cgst"], sc["sgst"], sc["igst"], sc["total"]) == (
        "1000.00",
        "180.00",
        "90.00",
        "90.00",
        "0.00",
        "1180.00",
    )
    assert sc["supply_type"] == "intra-state" and sc["currency"] == "INR" and sc["seller_state"] == "MH"


async def test_exclusive_inter_state() -> None:
    sc = (
        await call("calculate_gst", {"amount": 1000, "rate": 18, "customer_state": "KA"})
    ).structured_content
    assert (sc["igst"], sc["cgst"], sc["sgst"], sc["total"], sc["supply_type"]) == (
        "180.00",
        "0.00",
        "0.00",
        "1180.00",
        "inter-state",
    )


async def test_inclusive_reverse_calculation() -> None:
    sc = (
        await call("calculate_gst", {"amount": 1180, "rate": 18, "price_includes_gst": True})
    ).structured_content
    assert (sc["taxable_value"], sc["gst_amount"], sc["total"]) == ("1000.00", "180.00", "1180.00")


def test_odd_paisa_split_goes_to_cgst() -> None:
    g = compute(Decimal("0.05"), Decimal("18"), False, "intra-state")  # 0.009 → 0.01
    assert (g.gst_amount, g.cgst, g.sgst) == (Decimal("0.01"), Decimal("0.01"), Decimal("0.00"))


def test_half_up_rounding() -> None:
    assert compute(Decimal("0.25"), Decimal("18"), False, "inter-state").gst_amount == Decimal(
        "0.05"
    )  # 0.045


@pytest.mark.parametrize(
    "args",
    [
        {"amount": 100, "rate": -1},
        {"amount": 100, "rate": 40.01},
        {"amount": 100, "rate": 12.345},
        {"amount": 0},
        {"amount": 1e11},
        {"amount": 100, "customer_state": "Karnataka"},
    ],
)
async def test_bad_inputs_are_errors(args: dict[str, Any]) -> None:
    assert (await call("calculate_gst", args)).is_error


async def test_rate_bounds_accepted() -> None:
    assert not (await call("calculate_gst", {"amount": 100, "rate": 0})).is_error
    assert not (await call("calculate_gst", {"amount": 100, "rate": 40})).is_error
    assert not (await call("calculate_gst", {"amount": 100, "rate": 2.5})).is_error


async def test_invoice_totals_are_sums_of_lines() -> None:
    lines = [{"description": "Mug", "amount": 349}, {"description": "T-shirt", "amount": 799.99}]
    sc = (
        await call("gst_invoice_lines", {"lines": lines, "rate": 5, "customer_state": "DL"})
    ).structured_content
    assert [line["gst_amount"] for line in sc["lines"]] == ["17.45", "40.00"]
    assert sc["totals"]["gst_amount"] == "57.45" and sc["totals"]["igst"] == "57.45"
    assert sc["totals"]["total"] == "1206.44"


async def test_invoice_needs_one_to_fifty_lines() -> None:
    assert (await call("gst_invoice_lines", {"lines": []})).is_error
    many = [{"description": f"item {i}", "amount": 1} for i in range(51)]
    assert (await call("gst_invoice_lines", {"lines": many})).is_error


async def test_tools_listed_read_only() -> None:
    async with Client(build_server("MH")) as mcp:
        tools = (await mcp.list_tools()).tools
    assert {t.name for t in tools} == {"calculate_gst", "gst_invoice_lines"}
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert all("does not decide which rate" in (t.description or "") for t in tools)

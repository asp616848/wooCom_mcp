"""`calculate_gst` and `gst_invoice_lines` (DESIGN §9.2). Money is returned as strings with 2 decimals."""

from decimal import Decimal
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import AfterValidator, BaseModel, Field

from mcp_gst import APP_VERSION
from mcp_gst.calc import Gst, SupplyType, compute, dec, supply_type


def _max_two_decimals(value: float) -> float:
    if dec(value).as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError("rate allows at most 2 decimals")
    return value


Amount = Annotated[float, Field(gt=0, le=1e10, description="Amount in INR")]
Rate = Annotated[
    float,
    Field(ge=0, le=40, description="GST rate in percent, e.g. 5 or 18"),
    AfterValidator(_max_two_decimals),
]
State = Annotated[str, Field(pattern=r"^[A-Z]{2}$", description="Customer's two-letter state code, e.g. KA")]
IncludesGst = Annotated[bool, Field(description="True if the amount already includes GST")]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
RATE_NOTE = "Rates are inputs (e.g. 5 or 18); this tool does not decide which rate applies to a product."


def _m(value: Decimal) -> str:
    return f"{value:.2f}"


class GstBreakdown(BaseModel):
    taxable_value: str
    rate: float
    gst_amount: str
    cgst: str
    sgst: str
    igst: str
    total: str
    supply_type: SupplyType
    seller_state: str
    customer_state: str
    currency: Literal["INR"] = "INR"


class InvoiceLineIn(BaseModel):
    description: Annotated[str, Field(min_length=1, max_length=200)]
    amount: Amount


class InvoiceLineOut(BaseModel):
    description: str
    taxable_value: str
    gst_amount: str
    cgst: str
    sgst: str
    igst: str
    total: str


class InvoiceTotals(BaseModel):
    taxable_value: str
    gst_amount: str
    cgst: str
    sgst: str
    igst: str
    total: str


class InvoiceBreakdown(BaseModel):
    lines: list[InvoiceLineOut]
    totals: InvoiceTotals
    rate: float
    supply_type: SupplyType
    seller_state: str
    customer_state: str
    currency: Literal["INR"] = "INR"


def _amounts(g: Gst) -> dict[str, str]:
    return {
        "taxable_value": _m(g.taxable_value),
        "gst_amount": _m(g.gst_amount),
        "cgst": _m(g.cgst),
        "sgst": _m(g.sgst),
        "igst": _m(g.igst),
        "total": _m(g.total),
    }


def build_server(seller_state: str) -> MCPServer:
    mcp = MCPServer(
        "gst",
        version=APP_VERSION,
        instructions=f"Indian GST calculator. Seller state is {seller_state}. {RATE_NOTE}",
        subscriptions=False,
    )

    @mcp.tool(
        title="Calculate GST",
        annotations=READ_ONLY,
        description=(
            "GST on one amount, split into CGST+SGST (same state as the seller) or IGST (other state). "
            + RATE_NOTE
        ),
    )
    async def calculate_gst(
        amount: Amount,
        rate: Rate = 18,
        price_includes_gst: IncludesGst = False,
        customer_state: State = "MH",
    ) -> GstBreakdown:
        supply = supply_type(seller_state, customer_state)
        g = compute(dec(amount), dec(rate), price_includes_gst, supply)
        return GstBreakdown(
            **_amounts(g),
            rate=rate,
            supply_type=supply,
            seller_state=seller_state,
            customer_state=customer_state,
        )

    @mcp.tool(
        title="GST for invoice lines",
        annotations=READ_ONLY,
        description=(
            "GST per invoice line plus totals (each line computed like calculate_gst). " + RATE_NOTE
        ),
    )
    async def gst_invoice_lines(
        lines: Annotated[list[InvoiceLineIn], Field(min_length=1, max_length=50)],
        rate: Rate = 18,
        price_includes_gst: IncludesGst = False,
        customer_state: State = "MH",
    ) -> InvoiceBreakdown:
        supply = supply_type(seller_state, customer_state)
        computed = [
            (line, compute(dec(line.amount), dec(rate), price_includes_gst, supply)) for line in lines
        ]

        def total(field: str) -> str:
            return _m(sum((getattr(g, field) for _, g in computed), Decimal(0)))

        return InvoiceBreakdown(
            lines=[InvoiceLineOut(description=line.description, **_amounts(g)) for line, g in computed],
            totals=InvoiceTotals(
                **{f: total(f) for f in ("taxable_value", "gst_amount", "cgst", "sgst", "igst", "total")}
            ),
            rate=rate,
            supply_type=supply,
            seller_state=seller_state,
            customer_state=customer_state,
        )

    return mcp

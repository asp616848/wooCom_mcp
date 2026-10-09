"""GST math with Decimal, rounded half-up to paise (DESIGN §9.2). Pure functions, no I/O."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

PAISA = Decimal("0.01")
SupplyType = Literal["intra-state", "inter-state"]


def q(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def dec(value: float) -> Decimal:
    """float → Decimal via its shortest repr, so 0.1 stays 0.1 (not 0.1000000000000000055…)."""
    return Decimal(str(value))


@dataclass(frozen=True)
class Gst:
    taxable_value: Decimal
    gst_amount: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal


def supply_type(seller_state: str, customer_state: str) -> SupplyType:
    return "intra-state" if customer_state == seller_state else "inter-state"


def compute(amount: Decimal, rate: Decimal, price_includes_gst: bool, supply: SupplyType) -> Gst:
    if price_includes_gst:
        taxable = q(amount * 100 / (100 + rate))
        gst = q(amount) - taxable
        total = q(amount)
    else:
        taxable = q(amount)
        gst = q(amount * rate / 100)
        total = taxable + gst
    zero = Decimal("0.00")
    if supply == "intra-state":
        cgst = q(gst / 2)
        return Gst(taxable, gst, cgst, gst - cgst, zero, total)
    return Gst(taxable, gst, zero, zero, gst, total)

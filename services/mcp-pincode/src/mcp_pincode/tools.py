"""`check_delivery` and `list_serviceable_cities` with demo courier rules (DESIGN §9.3)."""

from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_pincode import APP_VERSION
from mcp_pincode.data import Place

Zone = Literal["same_city", "same_state", "other_state"]
ETA_DAYS: dict[Zone, int] = {"same_city": 1, "same_state": 2, "other_state": 4}
RULES = (
    "Demo courier rules: same city 1 day, same state 2 days, other states 4 days; COD wherever serviceable."
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


class Delivery(BaseModel):
    pincode: str
    serviceable: bool
    city: str | None
    state: str | None
    zone: Zone | None
    eta_days: int | None
    cod_available: bool
    note: str


class Origin(BaseModel):
    pincode: str
    city: str


class City(BaseModel):
    pincode: str
    city: str
    state: str


class ServiceableCities(BaseModel):
    origin: Origin
    cities: list[City]


def zone_for(origin: Place, dest: Place) -> Zone:
    if dest.city == origin.city:
        return "same_city"
    if dest.state_code == origin.state_code:
        return "same_state"
    return "other_state"


def build_server(places: dict[str, Place], origin_pincode: str) -> MCPServer:
    if origin_pincode not in places:
        raise ValueError(f"ORIGIN_PINCODE {origin_pincode} is not in the bundled serviceable list")
    origin = places[origin_pincode]
    mcp = MCPServer(
        "pincode",
        version=APP_VERSION,
        instructions=f"Delivery checks from {origin.city} ({origin.pincode}) to Indian PIN codes. {RULES}",
        subscriptions=False,
    )

    @mcp.tool(
        title="Check delivery",
        annotations=READ_ONLY,
        description=(
            f"Can we deliver to this PIN code from {origin.city}, how fast, and is cash on delivery "
            f"available? {RULES}"
        ),
    )
    async def check_delivery(
        pincode: Annotated[str, Field(pattern=r"^[1-9][0-9]{5}$", description="6-digit Indian PIN code")],
    ) -> Delivery:
        dest = places.get(pincode)
        if dest is None:
            return Delivery(
                pincode=pincode,
                serviceable=False,
                city=None,
                state=None,
                zone=None,
                eta_days=None,
                cod_available=False,
                note="Not in the demo serviceable list",
            )
        zone = zone_for(origin, dest)
        return Delivery(
            pincode=pincode,
            serviceable=True,
            city=dest.city,
            state=dest.state,
            zone=zone,
            eta_days=ETA_DAYS[zone],
            cod_available=True,
            note=RULES,
        )

    @mcp.tool(title="List serviceable cities", annotations=READ_ONLY)
    async def list_serviceable_cities() -> ServiceableCities:
        """The origin warehouse and every city we deliver to, sorted by city."""
        return ServiceableCities(
            origin=Origin(pincode=origin.pincode, city=origin.city),
            cities=[
                City(pincode=p.pincode, city=p.city, state=p.state)
                for p in sorted(places.values(), key=lambda p: p.city)
            ],
        )

    return mcp

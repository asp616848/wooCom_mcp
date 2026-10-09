"""TEST-U-14: zones, ETA, unknown PIN, format, sorted list, origin check."""

from typing import Any

import pytest
from mcp import Client

from mcp_pincode.data import load_places
from mcp_pincode.tools import build_server

pytestmark = pytest.mark.anyio


async def call(name: str, args: dict[str, Any]) -> Any:
    async with Client(build_server(load_places(), "400001")) as mcp:
        return await mcp.call_tool(name, args)


@pytest.mark.parametrize(
    ("pincode", "zone", "eta", "city"),
    [
        ("400001", "same_city", 1, "Mumbai"),
        ("411001", "same_state", 2, "Pune"),
        ("560001", "other_state", 4, "Bengaluru"),
    ],
)
async def test_zones(pincode: str, zone: str, eta: int, city: str) -> None:
    sc = (await call("check_delivery", {"pincode": pincode})).structured_content
    assert (sc["serviceable"], sc["zone"], sc["eta_days"], sc["city"], sc["cod_available"]) == (
        True,
        zone,
        eta,
        city,
        True,
    )


async def test_unknown_pincode_not_serviceable() -> None:
    sc = (await call("check_delivery", {"pincode": "999999"})).structured_content
    assert sc["serviceable"] is False and sc["cod_available"] is False and sc["zone"] is None
    assert sc["note"] == "Not in the demo serviceable list"


@pytest.mark.parametrize("pincode", ["40001", "4000011", "012345", "abcdef"])
async def test_bad_format_rejected(pincode: str) -> None:
    assert (await call("check_delivery", {"pincode": pincode})).is_error


async def test_list_sorted_ten_cities() -> None:
    sc = (await call("list_serviceable_cities", {})).structured_content
    assert sc["origin"] == {"pincode": "400001", "city": "Mumbai"}
    cities = [c["city"] for c in sc["cities"]]
    assert len(cities) == 10 and cities == sorted(cities)


def test_unknown_origin_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="not in the bundled"):
        build_server(load_places(), "999999")

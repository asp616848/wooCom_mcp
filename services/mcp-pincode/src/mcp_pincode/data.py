"""Bundled serviceable PIN codes (`data/pincodes.json`) loaded once at startup."""

import json
from importlib import resources

from pydantic import BaseModel, TypeAdapter


class Place(BaseModel):
    pincode: str
    city: str
    state_code: str
    state: str


def load_places() -> dict[str, Place]:
    raw = resources.files("mcp_pincode").joinpath("data/pincodes.json").read_text(encoding="utf-8")
    places = TypeAdapter(list[Place]).validate_python(json.loads(raw))
    return {p.pincode: p for p in places}

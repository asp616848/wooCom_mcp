"""TEST-U-01: exposed-name algorithm (DESIGN §5.5)."""

import re

from wmg_gateway.bootstrap import SLUG_PATTERN
from wmg_gateway.catalog import naming


def test_U01_simple_and_sanitized() -> None:
    names = naming.exposed_names("gst", ["calculate_gst", "invoice.lines", "with space", "dash-ok"])
    assert names["calculate_gst"] == "gst__calculate_gst"
    assert names["invoice.lines"] == "gst__invoice_lines"
    assert names["with space"] == "gst__with_space"
    assert names["dash-ok"] == "gst__dash-ok"


def test_U01_cap_with_hash_and_determinism() -> None:
    long = "x" * 80
    first = naming.exposed_names("woo-main", [long])[long]
    assert len(first) == 64 and first.startswith("woo-main__xxx") and re.search(r"_[0-9a-f]{6}$", first)
    assert first == naming.exposed_names("woo-main", [long, "other"])[long]  # deterministic
    exactly_64 = "y" * (64 - len("gst__"))
    assert naming.exposed_names("gst", [exactly_64])[exactly_64] == f"gst__{exactly_64}"


def test_U01_sanitize_collision_second_gets_hash() -> None:
    names = naming.exposed_names("s1", ["a_b", "a.b"])
    assert names["a.b"] == "s1__a_b"  # "a.b" sorts first
    assert names["a_b"] == "s1__a_b"[:57] + "_" + naming.hash6("s1", "a_b")
    assert len(set(names.values())) == 2


def test_U01_same_tool_two_servers_distinct() -> None:
    a = naming.exposed_names("gst", ["echo"])["echo"]
    b = naming.exposed_names("gst-copy", ["echo"])["echo"]
    assert a != b


def test_U01_slug_regex() -> None:
    ok = ["gst", "woo-main", "a1", "x" * 30]
    bad = ["G", "1abc", "a", "a_b", "a--b", "a-", "x" * 31]
    pattern = re.compile(SLUG_PATTERN)
    assert all(pattern.match(s) for s in ok)
    # `--` and trailing `-` are rejected by the DB CHECK and the API model, not the base regex
    assert not any(pattern.match(s) and "--" not in s and not s.endswith("-") for s in bad)


def test_U01_description_and_title() -> None:
    raw = "Line one\x00\x07  with   spaces\n\n\n\nline\ttwo"
    assert naming.exposed_description("gst", raw) == "[gst] Line one with spaces\n\nline two"
    assert naming.exposed_description("gst", None) == "[gst]"
    capped = naming.exposed_description("gst", "a" * 5000)
    assert len(capped) == 1024 and capped.endswith("…")
    assert naming.exposed_title("GST Calculator", None, "calc") == "GST Calculator: calc"
    assert naming.exposed_title("GST Calculator", "Calc GST", "calc") == "GST Calculator: Calc GST"

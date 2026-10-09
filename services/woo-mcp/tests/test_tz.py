from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from woo_mcp.tz import DateRange, resolve_range, store_tz, woo_date_query

IST = ZoneInfo("Asia/Kolkata")


def test_today_uses_store_timezone_not_utc() -> None:
    # 20:00 UTC on 10 Oct is already 01:30 on 11 Oct in Kolkata
    now = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)
    assert resolve_range("today", None, None, IST, now) == DateRange(date(2026, 10, 11), date(2026, 10, 11))
    assert resolve_range("today", None, None, UTC, now) == DateRange(date(2026, 10, 10), date(2026, 10, 10))


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("yesterday", date(2026, 10, 9), date(2026, 10, 9)),
        ("last_7_days", date(2026, 10, 4), date(2026, 10, 10)),
        ("last_30_days", date(2026, 9, 11), date(2026, 10, 10)),
    ],
)
def test_periods(period: str, start: date, end: date) -> None:
    now = datetime(2026, 10, 10, 9, 0, tzinfo=IST)
    assert resolve_range(period, None, None, IST, now) == DateRange(start, end)  # type: ignore[arg-type]


def test_woo_query_bounds_are_local_and_exclusive() -> None:
    q = woo_date_query(DateRange(date(2026, 10, 4), date(2026, 10, 10)))
    assert q == {"dates_are_gmt": "false", "after": "2026-10-03T23:59:59", "before": "2026-10-11T00:00:00"}
    assert woo_date_query(None) == {}
    assert woo_date_query(DateRange(date(2026, 10, 4), None)) == {
        "dates_are_gmt": "false",
        "after": "2026-10-03T23:59:59",
    }


def test_explicit_dates_and_validation() -> None:
    assert resolve_range(None, date(2026, 10, 1), date(2026, 10, 2), IST) == DateRange(
        date(2026, 10, 1), date(2026, 10, 2)
    )
    assert resolve_range(None, None, None, IST) is None
    with pytest.raises(ToolError, match="either period"):
        resolve_range("today", date(2026, 10, 1), None, IST)
    with pytest.raises(ToolError, match="on or before"):
        resolve_range(None, date(2026, 10, 3), date(2026, 10, 2), IST)


def test_store_tz_named_and_offset_only() -> None:
    assert store_tz("Asia/Kolkata", 5.5)[1] == "Asia/Kolkata"
    tz, name = store_tz("", 5.5)
    assert name == "UTC+05:30"
    assert datetime(2026, 1, 1, tzinfo=tz).utcoffset().total_seconds() == 19800  # type: ignore[union-attr]
    assert store_tz("Not/AZone", -3)[1] == "UTC-03:00"

"""Prior-session DAY orders are not working after the regular-session close."""

from datetime import datetime
from zoneinfo import ZoneInfo

from utils.working_orders import (
    filter_working_open_orders,
    is_prior_session_day_order,
    working_order_missing_message,
)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 14, 9, 1, tzinfo=ET)


def test_drops_prior_session_day_order():
    assert is_prior_session_day_order(
        {"tif": "DAY"}, "2026-08-13T19:59:51.850279Z", now=NOW,
    )


def test_keeps_today_day_order():
    assert not is_prior_session_day_order(
        {"tif": "DAY"}, "2026-08-14T13:30:00Z", now=NOW,
    )


def test_keeps_prior_session_gtc():
    assert not is_prior_session_day_order(
        {"tif": "GTC"}, "2026-08-13T19:59:51Z", now=NOW,
    )


def test_keeps_day_order_without_snapshot_time():
    assert not is_prior_session_day_order({"tif": "DAY"}, "", now=NOW)


def test_filter_drops_cbrs_day_ghost():
    kept = filter_working_open_orders(
        [
            ({"permId": 1857171561, "tif": "DAY", "symbol": "CBRS P230"},
             "2026-08-13T19:59:51Z"),
            ({"permId": 2128244184, "tif": "GTC", "symbol": "META P560"},
             "2026-08-13T19:59:51Z"),
        ],
        now=NOW,
    )
    assert [row["permId"] for row in kept] == [2128244184]


def test_missing_message_names_day_close():
    assert "DAY orders end" in working_order_missing_message("DAY")
    assert "filled or been cancelled" in working_order_missing_message("GTC")

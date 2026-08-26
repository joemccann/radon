"""Holiday-aware market-open checks (the SoT the FastAPI loop + scans consult).

scripts/api/server.py:_is_market_open_now_et delegates to is_market_open_et, so
these cases pin the behavior that gates the orders-sync / portfolio-sync loop.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from utils.market_calendar import (
    is_market_open_et,
    load_holidays,
    market_state,
    parse_liquid_hours,
    _is_trading_day,
    _resolve_market_state,
)


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_juneteenth_weekday_is_closed():
    # 2026-06-19 Juneteenth (Friday), 13:00 ET == 17:00 UTC.
    assert is_market_open_et(_utc(2026, 6, 19, 17)) is False


def test_ordinary_weekday_rth_is_open():
    # 2026-06-18 Thursday, 13:00 ET == 17:00 UTC.
    assert is_market_open_et(_utc(2026, 6, 18, 17)) is True


def test_weekend_is_closed():
    # 2026-06-20 Saturday.
    assert is_market_open_et(_utc(2026, 6, 20, 17)) is False


def test_before_open_and_after_close_closed():
    # 09:00 ET (13:00 UTC) and 16:30 ET (20:30 UTC) on a normal Thursday.
    assert is_market_open_et(_utc(2026, 6, 18, 13)) is False
    assert is_market_open_et(_utc(2026, 6, 18, 20, 30)) is False


# ── parse_liquid_hours (IBKR liquidHours → per-date status) ──────────────

def test_parse_liquid_hours_live_sample():
    # Real SPY liquidHours pulled live on 2026-06-19 (Juneteenth).
    s = (
        "20260619:CLOSED;20260620:CLOSED;20260621:CLOSED;"
        "20260622:0930-20260622:1600;20260623:0930-20260623:1600;"
        "20260624:0930-20260624:1600"
    )
    parsed = parse_liquid_hours(s)
    assert parsed["2026-06-19"] == {"status": "closed", "open_et": None, "close_et": None}
    assert parsed["2026-06-22"] == {"status": "open", "open_et": "09:30", "close_et": "16:00"}


def test_parse_liquid_hours_early_close():
    # Half-day: shortened close (13:00) → early_close, no special token.
    parsed = parse_liquid_hours("20261127:0930-20261127:1300")
    assert parsed["2026-11-27"] == {"status": "early_close", "open_et": "09:30", "close_et": "13:00"}


def test_parse_liquid_hours_ignores_garbage():
    assert parse_liquid_hours("") == {}
    assert parse_liquid_hours("not-a-segment;;BADDATE:CLOSED") == {}


# ── _resolve_market_state (pure) + IBKR-cache authority ──────────────────

def test_resolve_ibkr_closed_overrides_weekday():
    # Even a weekday in RTH is closed when the IBKR row says so (ad-hoc closure).
    assert _resolve_market_state(12 * 60, 2, {"status": "closed"}, False) == {
        "state": "closed", "is_open": False, "reason": "ibkr:closed",
    }


def test_resolve_ibkr_early_close():
    day = {"status": "early_close", "open_et": "09:30", "close_et": "13:00"}
    assert _resolve_market_state(12 * 60, 2, day, False)["state"] == "early_close"
    assert _resolve_market_state(13 * 60 + 30, 2, day, False)["is_open"] is False


def test_market_state_juneteenth_closed_via_static():
    # No IBKR cache in the test env → falls to the static holiday table.
    st = market_state(_utc(2026, 6, 19, 17))
    assert st["is_open"] is False
    assert st["reason"] == "static:holiday"


# ── static holiday table horizon (T-102) ─────────────────────────────────
# A year missing from scripts/config/market_holidays.json degrades to
# weekday-only, so every holiday silently becomes a trading day. Pin a
# two-year horizon so the next expiry fails here a year early.

def _third_monday_of_january(year: int) -> date:
    first = date(year, 1, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 14)


def test_static_holiday_table_covers_two_years_ahead():
    horizon_year = datetime.now().year + 2
    assert load_holidays(horizon_year), f"market_holidays.json has no {horizon_year} entry"


def test_mlk_monday_two_years_ahead_is_not_a_trading_day():
    mlk = _third_monday_of_january(datetime.now().year + 2)
    assert mlk.weekday() == 0
    assert _is_trading_day(datetime(mlk.year, mlk.month, mlk.day, 12)) is False

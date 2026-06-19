"""Holiday-aware market-open checks (the SoT the FastAPI loop + scans consult).

scripts/api/server.py:_is_market_open_now_et delegates to is_market_open_et, so
these cases pin the behavior that gates the orders-sync / portfolio-sync loop.
"""
from __future__ import annotations

from datetime import datetime, timezone

from utils.market_calendar import is_market_open_et


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

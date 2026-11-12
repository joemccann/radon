"""D3: every Python "is the market open" call site answers from one
holiday-aware source (utils/market_calendar.py).

Each test drives the REAL call site on the same dates: a full holiday
(2026-11-26 Thanksgiving, 2026-12-25 Christmas), a half day (2026-11-27,
13:00 ET close, carried by the IBKR calendar cache), and both DST boundaries
(2026-03-09 first EDT Monday, 2026-11-02 first EST Monday).
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from utils import market_calendar

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
REPO_ROOT = Path(__file__).resolve().parents[2]


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


THANKSGIVING_11 = _et(2026, 11, 26, 11, 0)
CHRISTMAS_11 = _et(2026, 12, 25, 11, 0)
HALF_DAY_11 = _et(2026, 11, 27, 11, 0)
HALF_DAY_14 = _et(2026, 11, 27, 14, 0)
SPRING_DST_0945 = datetime(2026, 3, 9, 13, 45, tzinfo=UTC)   # 09:45 EDT
FALL_DST_0945 = datetime(2026, 11, 2, 14, 45, tzinfo=UTC)    # 09:45 EST
FALL_DST_0845 = datetime(2026, 11, 2, 13, 45, tzinfo=UTC)    # 08:45 EST

HALF_DAY_ROW = {"2026-11-27": {"status": "early_close", "open_et": "09:30", "close_et": "13:00"}}

RTH_CASES = [
    pytest.param(THANKSGIVING_11, False, id="thanksgiving"),
    pytest.param(CHRISTMAS_11, False, id="christmas"),
    pytest.param(HALF_DAY_11, True, id="half-day-morning"),
    pytest.param(HALF_DAY_14, False, id="half-day-after-1pm-close"),
    pytest.param(SPRING_DST_0945, True, id="spring-dst-0945-edt"),
    pytest.param(FALL_DST_0945, True, id="fall-dst-0945-est"),
    pytest.param(FALL_DST_0845, False, id="fall-dst-0845-est"),
]


def _calendar_modules():
    import sys
    return [m for name, m in list(sys.modules.items())
            if name.endswith("utils.market_calendar") and m is not None]


@pytest.fixture(autouse=True)
def _half_day_cache(monkeypatch):
    for module in _calendar_modules():
        monkeypatch.setattr(module, "_load_ibkr_calendar", lambda: dict(HALF_DAY_ROW))


def _frozen_datetime(instant: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return instant.astimezone(ET).replace(tzinfo=None)
            return instant.astimezone(tz)
    return Frozen


def _freeze(monkeypatch, instant, *modules):
    frozen = _frozen_datetime(instant)
    for module in (*_calendar_modules(), *modules):
        monkeypatch.setattr(module, "datetime", frozen)


# ── regular-session (RTH) gates ─────────────────────────────────────────────

@pytest.mark.parametrize("instant,expected", RTH_CASES)
def test_cri_scan_is_market_open(instant, expected):
    import cri_scan
    assert cri_scan.is_market_open(instant) is expected


@pytest.mark.parametrize("module_name", ["gex_scan", "vcg_scan", "fetch_menthorq_cta", "gamma_rotation_gap"])
@pytest.mark.parametrize("instant,expected", RTH_CASES)
def test_scanner_is_market_open(monkeypatch, module_name, instant, expected):
    module = __import__(module_name)
    _freeze(monkeypatch, instant, module)
    assert module.is_market_open() is expected


@pytest.mark.parametrize("instant,expected", RTH_CASES)
def test_market_hours_is_market_open(instant, expected):
    from utils import market_hours
    assert market_hours.is_market_open(instant) is expected


def test_market_hours_status_names_the_holiday():
    from utils import market_hours
    is_open, status = market_hours.get_market_status(THANKSGIVING_11)
    assert is_open is False
    assert "holiday" in status.lower()


@pytest.mark.parametrize("instant,expected", RTH_CASES)
def test_price_cache_is_market_hours(monkeypatch, instant, expected):
    from utils import price_cache
    _freeze(monkeypatch, instant, price_cache)
    assert price_cache.is_market_hours() is expected


@pytest.mark.parametrize("instant,expected", RTH_CASES)
def test_incident_classify_is_market_open(instant, expected):
    from incident_watchdog import classify
    assert classify._is_market_open(instant) is expected


# ── trading-day gates ───────────────────────────────────────────────────────

TRADING_DAY_CASES = [
    pytest.param(THANKSGIVING_11, False, id="thanksgiving"),
    pytest.param(CHRISTMAS_11, False, id="christmas"),
    pytest.param(HALF_DAY_14, True, id="half-day-is-a-trading-day"),
    pytest.param(FALL_DST_0845, True, id="fall-dst-monday"),
]


@pytest.mark.parametrize("module_path", [
    "monitor_daemon.handlers.cash_flow_sync",
    "monitor_daemon.handlers.evening_execution_sweep",
])
@pytest.mark.parametrize("instant,expected", TRADING_DAY_CASES)
def test_handler_trading_day(module_path, instant, expected):
    import importlib
    module = importlib.import_module(module_path)
    assert module._is_trading_day_et(instant.astimezone(UTC)) is expected


@pytest.mark.parametrize("instant,expected", TRADING_DAY_CASES)
def test_data_refresh_trading_day(monkeypatch, instant, expected):
    import data_refresh
    _freeze(monkeypatch, instant, data_refresh)
    assert data_refresh._is_trading_day() is expected


@pytest.mark.parametrize("instant,expected", TRADING_DAY_CASES)
def test_canonical_is_trading_day_et(instant, expected):
    assert market_calendar.is_trading_day_et(instant) is expected


# ── session-date resolution ─────────────────────────────────────────────────

SESSION_DATE_CASES = [
    pytest.param(THANKSGIVING_11, "2026-11-25", id="thanksgiving-resolves-to-wednesday"),
    pytest.param(_et(2026, 11, 27, 8, 0), "2026-11-25", id="half-day-premarket-skips-thanksgiving"),
    pytest.param(_et(2026, 12, 28, 8, 0), "2026-12-24", id="monday-premarket-skips-christmas"),
    pytest.param(FALL_DST_0945, "2026-11-02", id="fall-dst-after-open"),
    pytest.param(FALL_DST_0845, "2026-10-30", id="fall-dst-premarket"),
]


@pytest.mark.parametrize("instant,expected", SESSION_DATE_CASES)
def test_cri_current_session_date(instant, expected):
    import cri_scan
    assert cri_scan.current_session_date_et(instant) == expected


@pytest.mark.parametrize("instant,expected", SESSION_DATE_CASES)
def test_menthorq_resolve_trading_date(monkeypatch, instant, expected):
    import fetch_menthorq_cta
    _freeze(monkeypatch, instant, fetch_menthorq_cta)
    assert fetch_menthorq_cta.resolve_trading_date() == expected


@pytest.mark.parametrize("instant,expected", [
    pytest.param(_et(2026, 11, 26, 17, 0), "2026-11-25", id="thanksgiving-evening"),
    pytest.param(_et(2026, 11, 27, 17, 0), "2026-11-27", id="half-day-evening"),
    pytest.param(_et(2026, 3, 9, 16, 30), "2026-03-09", id="spring-dst-after-close"),
])
def test_cta_latest_closed_trading_day(instant, expected):
    from utils.cta_sync import latest_closed_trading_day
    assert latest_closed_trading_day(instant) == expected


# ── post-close and extended-session windows ─────────────────────────────────

def test_cri_cboe_post_close_window_skips_holiday():
    import cri_scan
    assert cri_scan.is_post_close_cboe_official_window(_et(2026, 11, 26, 17, 0)) is False
    assert cri_scan.is_post_close_cboe_official_window(_et(2026, 11, 25, 17, 0)) is True


@pytest.mark.parametrize("instant,closed", [
    pytest.param(THANKSGIVING_11, True, id="thanksgiving"),
    pytest.param(CHRISTMAS_11, True, id="christmas"),
    pytest.param(HALF_DAY_14, False, id="half-day-after-hours-still-ext"),
    pytest.param(SPRING_DST_0945, False, id="spring-dst-open"),
    pytest.param(datetime(2026, 11, 2, 8, 45, tzinfo=UTC), True, id="fall-dst-0345-est"),
])
def test_health_service_market_closed(instant, closed):
    from health_service import probes
    assert probes._market_closed_et(instant.astimezone(ET)) is closed


@pytest.mark.parametrize("instant,expected", [
    pytest.param(THANKSGIVING_11, "closed", id="thanksgiving"),
    pytest.param(CHRISTMAS_11, "closed", id="christmas"),
    pytest.param(HALF_DAY_11, "open", id="half-day-morning"),
    pytest.param(HALF_DAY_14, "extended", id="half-day-after-close"),
    pytest.param(SPRING_DST_0945, "open", id="spring-dst-open"),
    pytest.param(FALL_DST_0845, "extended", id="fall-dst-premarket"),
])
def test_watchdog_market_state(instant, expected):
    from watchdog import check
    assert check._market_state_for(instant.astimezone(UTC)) == expected


# ── no hard-coded Eastern offsets ───────────────────────────────────────────

_FIXED_ET_OFFSET = re.compile(
    r"(timedelta|_td)\(hours=-[45]\)|now\w*\)? - (timedelta|_td)\(hours=[45]\)"
)


def test_no_hardcoded_eastern_offset_in_scripts():
    files = subprocess.run(
        ["git", "ls-files", "scripts/*.py"], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for rel in files:
        if "/tests/" in rel or rel.endswith("exit_order_service.py"):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        offenders += [f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1)
                      if _FIXED_ET_OFFSET.search(line)]
    assert offenders == []


# ── static half-day fallback (no IBKR calendar cache) ───────────────────────

@pytest.mark.parametrize("instant,state", [
    pytest.param(HALF_DAY_11, "early_close", id="half-day-morning"),
    pytest.param(HALF_DAY_14, "closed", id="half-day-after-1pm-close"),
    pytest.param(_et(2026, 12, 24, 13, 30), "closed", id="christmas-eve-after-close"),
])
def test_static_early_close_without_ibkr_cache(monkeypatch, instant, state):
    monkeypatch.setattr(market_calendar, "_load_ibkr_calendar", lambda: {})
    assert market_calendar.market_state(instant)["state"] == state

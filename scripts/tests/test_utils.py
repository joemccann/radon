"""Tests for scripts/utils/ shared utility modules."""
import json
import os
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════════════
# Market Calendar utils
# ══════════════════════════════════════════════════════════════════════

class TestLoadHolidays:
    def test_returns_set_of_strings(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(2026)
        assert isinstance(holidays, set)
        assert all(isinstance(h, str) for h in holidays)

    def test_2026_holidays_include_known_dates(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(2026)
        assert "2026-01-01" in holidays  # New Year's
        assert "2026-01-19" in holidays  # MLK Day
        assert "2026-12-25" in holidays  # Christmas

    def test_defaults_to_current_year(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays()
        assert isinstance(holidays, set)
        assert len(holidays) > 0

    def test_unknown_year_returns_empty(self):
        from utils.market_calendar import load_holidays
        holidays = load_holidays(1999)
        assert holidays == set()


class TestIsMarketOpen:
    def test_weekday_during_hours(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # Tuesday 2026-03-03 at 10:00 AM ET
        dt = datetime(2026, 3, 3, 10, 0)
        assert is_market_open(dt) is True

    def test_weekend_closed(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # Saturday
        dt = datetime(2026, 3, 7, 12, 0)
        assert is_market_open(dt) is False

    def test_sunday_closed(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        dt = datetime(2026, 3, 8, 12, 0)
        assert is_market_open(dt) is False

    def test_holiday_closed(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # MLK Day 2026
        dt = datetime(2026, 1, 19, 12, 0)
        assert is_market_open(dt) is False

    def test_before_930_closed(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # Weekday at 9:00 AM
        dt = datetime(2026, 3, 3, 9, 0)
        assert is_market_open(dt) is False

    def test_after_4pm_closed(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # Weekday at 4:01 PM
        dt = datetime(2026, 3, 3, 16, 1)
        assert is_market_open(dt) is False

    def test_exactly_930_open(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        dt = datetime(2026, 3, 3, 9, 30)
        assert is_market_open(dt) is True

    def test_exactly_4pm_open(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        dt = datetime(2026, 3, 3, 16, 0)
        assert is_market_open(dt) is True

    def test_defaults_to_now(self):
        from utils.market_calendar import is_market_open_et as is_market_open
        # Should not raise, returns a bool
        result = is_market_open()
        assert isinstance(result, bool)


class TestGetLastNTradingDays:
    def test_returns_correct_count(self):
        from utils.market_calendar import get_last_n_trading_days
        # Wednesday at 17:00 (after market close)
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(3, from_date=dt)
        assert len(days) == 3

    def test_skips_weekends(self):
        from utils.market_calendar import get_last_n_trading_days
        # Monday at 10am (before close) — should start from previous Friday
        dt = datetime(2026, 3, 2, 10, 0)
        days = get_last_n_trading_days(1, from_date=dt)
        assert days[0] == "2026-02-27"  # Friday

    def test_skips_holidays(self):
        from utils.market_calendar import get_last_n_trading_days
        # Day after MLK Day (Tuesday 2026-01-20 at 10am)
        dt = datetime(2026, 1, 20, 10, 0)
        days = get_last_n_trading_days(2, from_date=dt)
        assert "2026-01-19" not in days
        assert len(days) == 2

    def test_returns_strings(self):
        from utils.market_calendar import get_last_n_trading_days
        dt = datetime(2026, 3, 4, 17, 0)
        days = get_last_n_trading_days(2, from_date=dt)
        assert all(isinstance(d, str) for d in days)

    def test_defaults_from_date(self):
        from utils.market_calendar import get_last_n_trading_days
        days = get_last_n_trading_days(1)
        assert len(days) == 1

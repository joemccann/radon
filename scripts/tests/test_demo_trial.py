"""Tests for scripts/utils/demo_trial.py — 3-trading-day demo window math."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from utils.demo_trial import (
    compute_trial_expiry,
    is_trial_active,
    trial_expiry_handler,
)

ET = ZoneInfo("America/New_York")


def test_three_trading_days_across_a_weekend():
    # Tue 2026-06-16 is a normal trading day. Day1=Tue, Day2=Wed, Day3=Thu.
    expiry = compute_trial_expiry("2026-06-16T09:05:00", trading_days=3)
    dt = datetime.fromisoformat(expiry)
    assert dt.date().isoformat() == "2026-06-18"  # Thursday
    assert (dt.hour, dt.minute) == (16, 0)


def test_three_trading_days_spanning_weekend_and_juneteenth_holiday():
    # Thu 2026-06-18: Day1=Thu 06-18, Day2 would be Fri 06-19 but that is
    # Juneteenth (holiday) → skipped; Sat/Sun skipped; Day2=Mon 06-22,
    # Day3=Tue 06-23. Exercises BOTH a weekend and a mid-window holiday.
    expiry = compute_trial_expiry("2026-06-18T10:00:00", trading_days=3)
    dt = datetime.fromisoformat(expiry)
    assert dt.date().isoformat() == "2026-06-23"  # Tuesday
    assert (dt.hour, dt.minute) == (16, 0)


def test_three_trading_days_across_a_us_holiday():
    # Start Fri 2026-07-03 (NYSE closed for Independence Day observed). The
    # market calendar excludes both the weekend and the holiday. Day1 must roll
    # to the next OPEN session, then count three trading days.
    expiry = compute_trial_expiry("2026-07-03T09:30:00", trading_days=3)
    dt = datetime.fromisoformat(expiry)
    # 2026-07-04 is Saturday; observed holiday is Friday 07-03. Trading days:
    # Mon 07-06 (1), Tue 07-07 (2), Wed 07-08 (3).
    assert dt.date().isoformat() == "2026-07-08"
    assert (dt.hour, dt.minute) == (16, 0)


def test_same_day_trading_day_start_counts_as_day_one():
    # trading_days=1 on a trading day → expiry is 16:00 ET the SAME day.
    expiry = compute_trial_expiry("2026-06-17T11:00:00", trading_days=1)
    dt = datetime.fromisoformat(expiry)
    assert dt.date().isoformat() == "2026-06-17"
    assert (dt.hour, dt.minute) == (16, 0)


def test_weekend_start_rolls_forward_to_next_trading_day():
    # Sat 2026-06-20 → first trading day is Mon 06-22 (trading_days=1).
    expiry = compute_trial_expiry("2026-06-20T12:00:00", trading_days=1)
    dt = datetime.fromisoformat(expiry)
    assert dt.date().isoformat() == "2026-06-22"


def test_is_trial_active_true_before_boundary():
    expiry = compute_trial_expiry("2026-06-16T09:00:00", trading_days=3)  # Thu 06-18 16:00 ET
    just_before = datetime(2026, 6, 18, 15, 59, tzinfo=ET)
    assert is_trial_active(expiry, now=just_before) is True


def test_is_trial_active_false_at_and_after_boundary():
    expiry = compute_trial_expiry("2026-06-16T09:00:00", trading_days=3)  # Thu 06-18 16:00 ET
    at_close = datetime(2026, 6, 18, 16, 0, tzinfo=ET)
    after = datetime(2026, 6, 18, 16, 1, tzinfo=ET)
    assert is_trial_active(expiry, now=at_close) is False
    assert is_trial_active(expiry, now=after) is False


def test_invalid_trading_days_raises():
    with pytest.raises(ValueError):
        compute_trial_expiry("2026-06-17T09:00:00", trading_days=0)


def test_trial_expiry_handler_shape():
    out = trial_expiry_handler("2026-06-16T09:05:00", trading_days=3)
    assert set(out) == {"started_at", "expires_at", "trading_days", "active"}
    assert out["trading_days"] == 3
    assert datetime.fromisoformat(out["expires_at"]).date().isoformat() == "2026-06-18"

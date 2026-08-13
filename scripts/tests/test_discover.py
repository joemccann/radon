"""Tests for discover.py — dark pool day analysis and scoring."""
import json
import sys

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from discover import (
    analyze_darkpool_day,
    calculate_score,
    discover_targeted,
    is_market_open,
    WEIGHTS,
)
from clients.uw_client import UWAPIError


# ── is_market_open ──────────────────────────────────────────────────

class TestIsMarketOpen:
    def test_weekday_open(self):
        assert is_market_open(datetime(2026, 3, 3)) is True

    def test_weekend_closed(self):
        assert is_market_open(datetime(2026, 3, 7)) is False

    def test_holiday_closed(self):
        assert is_market_open(datetime(2026, 1, 19)) is False


# ── analyze_darkpool_day ────────────────────────────────────────────

class TestAnalyzeDarkpoolDay:
    def test_empty_trades_no_data(self):
        result = analyze_darkpool_day([])
        assert result["direction"] == "NO_DATA"
        assert result["buy_ratio"] is None
        assert result["prints"] == 0

    def test_strong_buy_accumulation(self):
        trades = [
            {"size": "5000", "price": "101", "nbbo_bid": "99", "nbbo_ask": "101"},
        ]
        result = analyze_darkpool_day(trades)
        assert result["direction"] == "ACCUMULATION"
        assert result["buy_ratio"] == 1.0
        assert result["strength"] == 100.0

    def test_strong_sell_distribution(self):
        trades = [
            {"size": "5000", "price": "99", "nbbo_bid": "99", "nbbo_ask": "101"},
        ]
        result = analyze_darkpool_day(trades)
        assert result["direction"] == "DISTRIBUTION"
        assert result["buy_ratio"] == 0.0

    def test_canceled_skipped(self):
        trades = [
            {"size": "5000", "price": "101", "nbbo_bid": "99", "nbbo_ask": "101",
             "canceled": True},
        ]
        result = analyze_darkpool_day(trades)
        # Canceled trades still counted in prints, but not in volume
        assert result["buy_ratio"] is None

    def test_neutral_balanced(self):
        trades = [
            {"size": "1000", "price": "101", "nbbo_bid": "99", "nbbo_ask": "101"},
            {"size": "1000", "price": "99", "nbbo_bid": "99", "nbbo_ask": "101"},
        ]
        result = analyze_darkpool_day(trades)
        assert result["direction"] == "NEUTRAL"
        assert result["buy_ratio"] == 0.5

    def test_strength_capped_at_100(self):
        trades = [
            {"size": "5000", "price": "110", "nbbo_bid": "99", "nbbo_ask": "101"},
        ]
        result = analyze_darkpool_day(trades)
        assert result["strength"] <= 100.0


# ── calculate_score ─────────────────────────────────────────────────

class TestCalculateScore:
    def test_weights_sum_to_100(self):
        assert sum(WEIGHTS.values()) == 100

    def test_max_score_all_components_high(self):
        result = calculate_score(
            dp_strength=100,
            dp_sustained=5,
            has_confluence=True,
            vol_oi_ratio=5.0,
            sweep_count=3,
            alert_count=10,
        )
        assert result["total"] == 100.0

    def test_zero_score_no_signals(self):
        result = calculate_score(
            dp_strength=0,
            dp_sustained=0,
            has_confluence=False,
            vol_oi_ratio=0.5,
            sweep_count=0,
            alert_count=0,
        )
        assert result["total"] == 0.0

    def test_dp_sustained_scaling(self):
        """1 day = 20, 5 days = 100."""
        r1 = calculate_score(0, 1, False, 0, 0, 0)
        r5 = calculate_score(0, 5, False, 0, 0, 0)
        assert r1["components"]["dp_sustained"] == 20
        assert r5["components"]["dp_sustained"] == 100

    def test_confluence_binary(self):
        r_yes = calculate_score(0, 0, True, 0, 0, 0)
        r_no = calculate_score(0, 0, False, 0, 0, 0)
        assert r_yes["components"]["confluence"] == 100
        assert r_no["components"]["confluence"] == 0

    def test_vol_oi_normalization(self):
        """vol_oi <= 1 → 0, 2 → 50, 4+ → 100."""
        r_low = calculate_score(0, 0, False, 0.5, 0, 0)
        r_mid = calculate_score(0, 0, False, 2.0, 0, 0)
        r_high = calculate_score(0, 0, False, 5.0, 0, 0)
        assert r_low["components"]["vol_oi"] == 0
        assert r_mid["components"]["vol_oi"] == 50
        assert r_high["components"]["vol_oi"] == 100

    def test_sweep_count_scaling(self):
        """0 → 0, 1 → 50, 2+ → 100."""
        r0 = calculate_score(0, 0, False, 0, 0, 0)
        r1 = calculate_score(0, 0, False, 0, 1, 0)
        r2 = calculate_score(0, 0, False, 0, 2, 0)
        assert r0["components"]["sweeps"] == 0
        assert r1["components"]["sweeps"] == 50
        assert r2["components"]["sweeps"] == 100

    def test_weighted_breakdown_matches_total(self):
        result = calculate_score(
            dp_strength=60,
            dp_sustained=3,
            has_confluence=True,
            vol_oi_ratio=2.5,
            sweep_count=1,
            alert_count=5,
        )
        expected_total = sum(result["weighted"].values())
        assert abs(result["total"] - expected_total) < 0.2


# ── discovery_time is timezone-aware ────────────────────────────────
#
# Hetzner is UTC; the JS consumer parses naive ISO strings as local time
# and the freshness banner lies for any viewer west of UTC. discover.py
# must emit timezone-aware ISO so JS Date.parse() pins the instant
# correctly regardless of the viewer's offset.

def _has_tz_offset(iso_string: str) -> bool:
    # An ISO datetime is tz-aware iff it ends with "Z" or "+HH:MM" / "-HH:MM"
    # (Python's isoformat always emits the colon when tz is UTC).
    if iso_string.endswith("Z"):
        return True
    tail = iso_string[-6:]
    return len(tail) == 6 and tail[0] in "+-" and tail[3] == ":"


class TestDiscoveryTimeTimezoneAware:
    """discover.py must emit a timezone-aware ISO scan_time string.

    Both empty-result paths and the targeted-mode path are covered. The
    market-wide path with results is identical to the empty path for the
    purpose of the discovery_time field.
    """

    def test_helper_recognises_offset_strings(self):
        # Sanity: confirm the helper rejects naive strings (the bug) and
        # accepts both forms of tz-aware ISO (the fix).
        assert _has_tz_offset("2026-05-09T01:58:36.144211+00:00")
        assert _has_tz_offset("2026-05-09T01:58:36Z")
        assert not _has_tz_offset("2026-05-09T01:58:36.144211")

    def test_targeted_mode_discovery_time_is_timezone_aware(self):
        """Targeted mode (line 465) must emit tz-aware discovery_time."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get_flow_alerts.return_value = {"data": []}

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_darkpool_multi", return_value={
                 "aggregate": {"buy_ratio": 0.5, "direction": "NEUTRAL",
                               "strength": 0.0, "prints": 0},
                 "daily": [], "sustained_days": 0, "total_prints": 0,
             }):
            result = discover_targeted(["AAPL"])

        discovery_time = result["discovery_time"]
        assert isinstance(discovery_time, str) and discovery_time
        assert _has_tz_offset(discovery_time), (
            f"discovery_time {discovery_time!r} is naive — JS Date.parse() "
            "will treat it as local time and the freshness banner will lie"
        )
    def test_market_wide_empty_alerts_discovery_time_is_timezone_aware(self):
        """Market-wide empty-alert path (line 500) must emit tz-aware ISO."""
        from discover import discover

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("discover.UWClient", return_value=mock_client), \
             patch("discover.fetch_options_flow", return_value=[]):
            result = discover()

        discovery_time = result["discovery_time"]
        assert _has_tz_offset(discovery_time), (
            f"discovery_time {discovery_time!r} is naive — JS Date.parse() "
            "will treat it as local time and the freshness banner will lie"
        )


def test_required_uw_failure_cannot_publish_or_alert(capsys):
    """A required provider outage must be explicit and never look publishable."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with patch("discover.UWClient", return_value=mock_client), \
         patch("discover.fetch_options_flow", side_effect=UWAPIError("secret provider detail")), \
         patch("discover.mirror_scan_snapshot") as mirror, \
         patch("discover.run_alerts_for_results") as alert, \
         patch.object(sys, "argv", ["discover.py"]):
        from discover import main

        main()

    result = json.loads(capsys.readouterr().out)
    assert result["degraded"] is True
    assert result["error"] == "required provider data unavailable"
    assert result["provider_failures"] == [
        {
            "provider": "unusual_whales",
            "operation": "options_flow",
            "error_type": "UWAPIError",
        }
    ]
    assert "secret provider detail" not in json.dumps(result)
    mirror.assert_not_called()
    alert.assert_not_called()

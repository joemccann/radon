"""Tests for scanner.py — signal scoring from flow data."""
import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from scanner import analyze_signal, fetch_flow_data, scan


class TestAnalyzeSignal:
    def test_conflicting_options_bias_applies_penalty_in_production_path(self):
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 70,
                    "num_prints": 200,
                },
                "daily": [],
            },
            "options_flow": {"bias": "BEARISH"},
        }

        result = analyze_signal(flow_data)

        assert result["options_conflict"] is True
        assert result["score"] == 45

    def test_production_fetch_requests_options_flow_for_conflict_scoring(self):
        with patch("scanner.fetch_flow_module", return_value={}) as fetch:
            fetch_flow_data("SPY")

        assert fetch.call_args.kwargs["skip_options_flow"] is False

    def test_error_flow_data(self):
        result = analyze_signal({"error": "timeout"})
        assert result["score"] == -1
        assert result["signal"] == "ERROR"

    def test_strong_accumulation_sustained(self):
        """High strength + sustained days + recent confirms → STRONG."""
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 80,
                    "dp_buy_ratio": 0.9,
                    "num_prints": 200,
                },
                "daily": [
                    {"flow_direction": "ACCUMULATION", "flow_strength": 70},
                    {"flow_direction": "ACCUMULATION", "flow_strength": 60},
                    {"flow_direction": "ACCUMULATION", "flow_strength": 65},
                    {"flow_direction": "ACCUMULATION", "flow_strength": 55},
                ],
            }
        }
        result = analyze_signal(flow_data)
        assert result["signal"] == "STRONG"
        assert result["score"] >= 60

    def test_weak_signal_low_prints(self):
        """Low print count penalizes score."""
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 30,
                    "dp_buy_ratio": 0.6,
                    "num_prints": 20,
                },
                "daily": [
                    {"flow_direction": "ACCUMULATION", "flow_strength": 30},
                ],
            }
        }
        result = analyze_signal(flow_data)
        assert result["score"] < 40
        assert result["signal"] in ("WEAK", "NONE")

    def test_recent_confirms_aggregate_bonus(self):
        """Recent day matching aggregate direction with strong signal → bonus."""
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "DISTRIBUTION",
                    "flow_strength": 50,
                    "dp_buy_ratio": 0.3,
                    "num_prints": 150,
                },
                "daily": [
                    {"flow_direction": "DISTRIBUTION", "flow_strength": 60},
                ],
            }
        }
        result = analyze_signal(flow_data)
        # Base 50 + recent_confirm 15 = 65+ → STRONG
        assert result["score"] >= 60

    def test_recent_contradicts_penalty(self):
        """Recent day contradicting aggregate → penalty."""
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 60,
                    "dp_buy_ratio": 0.7,
                    "num_prints": 150,
                },
                "daily": [
                    {"flow_direction": "DISTRIBUTION", "flow_strength": 40},
                    {"flow_direction": "ACCUMULATION", "flow_strength": 60},
                ],
            }
        }
        result = analyze_signal(flow_data)
        # 60 base - 30 contradiction = 30, below STRONG
        assert result["score"] < 60

    def test_neutral_direction_no_signal(self):
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "NEUTRAL",
                    "flow_strength": 0,
                    "dp_buy_ratio": 0.5,
                    "num_prints": 100,
                },
                "daily": [
                    {"flow_direction": "NEUTRAL", "flow_strength": 0},
                ],
            }
        }
        result = analyze_signal(flow_data)
        assert result["signal"] == "NONE"

    def test_moderate_signal(self):
        """Score between 40 and 60 with direction → MODERATE."""
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 45,
                    "dp_buy_ratio": 0.65,
                    "num_prints": 120,
                },
                "daily": [
                    {"flow_direction": "NEUTRAL", "flow_strength": 0},
                ],
            }
        }
        result = analyze_signal(flow_data)
        assert result["signal"] in ("MODERATE", "WEAK")

    def test_sustained_days_calculated(self):
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 50,
                    "dp_buy_ratio": 0.7,
                    "num_prints": 100,
                },
                "daily": [
                    {"flow_direction": "ACCUMULATION", "flow_strength": 50},
                    {"flow_direction": "ACCUMULATION", "flow_strength": 45},
                    {"flow_direction": "NEUTRAL", "flow_strength": 0},
                ],
            }
        }
        result = analyze_signal(flow_data)
        assert result["sustained_days"] == 2

    def test_empty_daily_list(self):
        flow_data = {
            "dark_pool": {
                "aggregate": {
                    "flow_direction": "ACCUMULATION",
                    "flow_strength": 50,
                    "dp_buy_ratio": 0.7,
                    "num_prints": 100,
                },
                "daily": [],
            }
        }
        result = analyze_signal(flow_data)
        assert result["recent_direction"] == "UNKNOWN"


# ── scan_time timezone-awareness regression ──────────────────────
# JS `new Date()` parses naive ISO strings as local time; on a UTC
# host this rolls the trading day forward for users west of UTC.

class TestScanTimeTimezoneAware:
    def test_scan_time_has_utc_offset_when_watchlist_empty(self, monkeypatch):
        """scan() emits scan_time even when the Turso watchlist is empty."""
        monkeypatch.setattr("scanner.get_watchlist_items", lambda: [])
        monkeypatch.setattr("scanner.get_open_positions", lambda: set())

        buf = io.StringIO()
        with redirect_stdout(buf):
            scan(top_n=1, min_score=0, max_workers=1)

        output = json.loads(buf.getvalue())
        scan_time = output["scan_time"]
        assert "+00:00" in scan_time or scan_time.endswith("Z"), (
            f"scan_time {scan_time!r} is naive; JS will parse it as local time"
        )

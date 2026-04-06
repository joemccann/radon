"""Tests for gex_scan.py — GEX levels computation."""
import json
import math
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gex_scan import (
    bucket_profile,
    compute_gex_flip,
    find_key_levels,
    tag_profile,
    compute_expected_range,
    compute_directional_bias,
    compute_days_above_flip,
    merge_history,
    build_gex_output,
    fetch_strike_gex,
    _bucket_size_for,
)


# ── Fixtures ─────────────────────────────────────────────────────

def _make_strike_data(strikes_gex):
    """Helper: list of (strike, call_gex, put_gex) → parsed rows."""
    return [
        {
            "strike": s,
            "call_gex": cg,
            "put_gex": pg,
            "net_gex": cg + pg,
            "call_delta": 100.0,
            "put_delta": -80.0,
            "net_delta": 20.0,
        }
        for s, cg, pg in strikes_gex
    ]


SAMPLE_STRIKES = _make_strike_data([
    # Below flip: negative gamma dominates
    (5400, 10.0, -200.0),     # net = -190
    (5425, 20.0, -150.0),     # net = -130
    (5450, 30.0, -120.0),     # net = -90
    (5475, 50.0, -80.0),      # net = -30
    (5500, 100.0, -60.0),     # net = +40 → flip crosses here
    (5525, 120.0, -40.0),     # net = +80
    (5550, 200.0, -30.0),     # net = +170 → max magnet
    (5575, 80.0, -20.0),      # net = +60
    (5600, 50.0, -10.0),      # net = +40
    (5625, 30.0, -300.0),     # net = -270 → max accelerator
    (5650, 20.0, -5.0),       # net = +15
])

SPOT = 5520.0


# ── Bucket Size ──────────────────────────────────────────────────

class TestBucketSize:
    def test_spx_uses_25(self):
        assert _bucket_size_for("SPX", 5500) == 25

    def test_spy_uses_5(self):
        assert _bucket_size_for("SPY", 550) == 5

    def test_stock_scales_with_price(self):
        size = _bucket_size_for("AAPL", 200)
        assert size == 1


# ── Bucket Profile ───────────────────────────────────────────────

class TestBucketProfile:
    def test_buckets_aggregate_correctly(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        assert len(profile) > 0
        assert all("strike" in b for b in profile)
        assert all("net_gex" in b for b in profile)

    def test_filters_by_range(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT, range_pct=0.01)
        strikes = [b["strike"] for b in profile]
        low = SPOT * 0.99
        high = SPOT * 1.01
        assert all(low <= s <= high for s in strikes)

    def test_pct_from_spot_computed(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        for b in profile:
            expected_pct = round((b["strike"] - SPOT) / SPOT * 100, 2)
            assert b["pct_from_spot"] == expected_pct

    def test_empty_input(self):
        profile = bucket_profile([], 25, SPOT)
        assert profile == []


# ── GEX Flip ─────────────────────────────────────────────────────

class TestGexFlip:
    def test_flip_found_at_zero_crossing(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        flip = compute_gex_flip(profile, SPOT)
        assert flip is not None
        assert flip <= SPOT

    def test_flip_none_when_all_positive(self):
        all_positive = _make_strike_data([
            (5400, 100, -10),
            (5425, 100, -10),
            (5450, 100, -10),
        ])
        profile = bucket_profile(all_positive, 25, 5425)
        flip = compute_gex_flip(profile, 5425)
        # Cumulative never crosses zero from negative to positive
        # (starts positive), so flip should be None
        assert flip is None

    def test_flip_none_when_all_negative(self):
        all_negative = _make_strike_data([
            (5400, 10, -200),
            (5425, 10, -200),
            (5450, 10, -200),
        ])
        profile = bucket_profile(all_negative, 25, 5425)
        flip = compute_gex_flip(profile, 5425)
        assert flip is None

    def test_flip_takes_last_crossing_below_spot(self):
        data = _make_strike_data([
            (5400, 10, -100),    # net = -90  (negative)
            (5425, 200, -10),    # net = +190 (positive) → first crossing
            (5450, 10, -300),    # net = -290 (negative) → goes negative
            (5475, 400, -10),    # net = +390 (positive) → second crossing
            (5500, 50, -10),     # net = +40
        ])
        profile = bucket_profile(data, 25, 5510)
        flip = compute_gex_flip(profile, 5510)
        # Should find the last neg→pos crossing below spot at 5475
        assert flip == 5475


# ── Key Levels ───────────────────────────────────────────────────

class TestKeyLevels:
    def test_max_magnet_is_highest_positive(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        magnet = levels["max_magnet"]
        assert magnet is not None
        assert magnet["gamma"] > 0

        # Verify it's actually the highest
        max_net = max(b["net_gex"] for b in profile if b["net_gex"] > 0)
        assert magnet["gamma"] == round(max_net, 2)

    def test_max_accelerator_is_most_negative(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        accel = levels["max_accelerator"]
        assert accel is not None
        assert accel["gamma"] < 0

    def test_second_magnet_differs_from_first(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        if levels["second_magnet"] is not None:
            assert levels["second_magnet"]["strike"] != levels["max_magnet"]["strike"]

    def test_distance_from_spot(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        for name, lvl in levels.items():
            if lvl is None:
                continue
            expected_dist = round(lvl["strike"] - SPOT, 2)
            assert lvl["distance"] == expected_dist

    def test_empty_profile(self):
        levels = find_key_levels([], SPOT)
        assert levels["max_magnet"] is None
        assert levels["max_accelerator"] is None


# ── Profile Tagging ──────────────────────────────────────────────

class TestTagProfile:
    def test_spot_tagged(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        flip = compute_gex_flip(profile, SPOT)
        tagged = tag_profile(profile, SPOT, flip, levels)
        tags = [b["tag"] for b in tagged if b["tag"]]
        assert "SPOT" in tags

    def test_flip_tagged(self):
        profile = bucket_profile(SAMPLE_STRIKES, 25, SPOT)
        levels = find_key_levels(profile, SPOT)
        flip = compute_gex_flip(profile, SPOT)
        tagged = tag_profile(profile, SPOT, flip, levels)
        if flip is not None:
            tags = [b["tag"] for b in tagged if b["tag"]]
            assert "GEX FLIP" in tags


# ── Expected Range ───────────────────────────────────────────────

class TestExpectedRange:
    def test_range_symmetric_around_spot(self):
        result = compute_expected_range(5500, 0.20)
        assert result["low"] < 5500
        assert result["high"] > 5500
        # Should be symmetric
        low_dist = 5500 - result["low"]
        high_dist = result["high"] - 5500
        assert abs(low_dist - high_dist) < 0.01

    def test_higher_iv_wider_range(self):
        narrow = compute_expected_range(5500, 0.10)
        wide = compute_expected_range(5500, 0.30)
        narrow_width = narrow["high"] - narrow["low"]
        wide_width = wide["high"] - wide["low"]
        assert wide_width > narrow_width

    def test_none_iv_returns_none(self):
        result = compute_expected_range(5500, None)
        assert result["low"] is None
        assert result["high"] is None

    def test_iv_1d_computed(self):
        result = compute_expected_range(5500, 0.20)
        expected = 0.20 / math.sqrt(252) * 100
        assert abs(result["iv_1d"] - expected) < 0.01


# ── Directional Bias ─────────────────────────────────────────────

class TestDirectionalBias:
    def test_bull_when_above_flip_positive_gex_magnet_above(self):
        levels = {
            "max_magnet": {"strike": 5600, "gamma": 100},
            "max_accelerator": {"strike": 5300, "gamma": -200},
        }
        bias = compute_directional_bias(5520, 5500, 100, levels, 3)
        assert bias["direction"] == "BULL"

    def test_cautious_bull_when_above_flip_negative_gex(self):
        levels = {
            "max_magnet": {"strike": 5600, "gamma": 100},
            "max_accelerator": {"strike": 5300, "gamma": -200},
        }
        bias = compute_directional_bias(5520, 5500, -50, levels, 1)
        assert bias["direction"] == "CAUTIOUS_BULL"

    def test_bear_when_below_flip_negative_gex(self):
        levels = {
            "max_magnet": {"strike": 5600, "gamma": 100},
            "max_accelerator": {"strike": 5300, "gamma": -200},
        }
        bias = compute_directional_bias(5480, 5500, -200, levels, -2)
        assert bias["direction"] == "BEAR"

    def test_cautious_bear_when_below_flip_positive_gex(self):
        levels = {
            "max_magnet": {"strike": 5600, "gamma": 100},
            "max_accelerator": {"strike": 5300, "gamma": -200},
        }
        bias = compute_directional_bias(5480, 5500, 50, levels, -1)
        assert bias["direction"] == "CAUTIOUS_BEAR"

    def test_neutral_when_no_flip(self):
        bias = compute_directional_bias(5500, None, 0, {}, 0)
        assert bias["direction"] == "NEUTRAL"

    def test_consecutive_days_in_reasons(self):
        levels = {"max_magnet": {"strike": 5600, "gamma": 100}}
        bias = compute_directional_bias(5520, 5500, 100, levels, 5)
        reasons_text = " ".join(bias["reasons"])
        assert "5 consecutive days above" in reasons_text


# ── Days Above Flip ──────────────────────────────────────────────

class TestDaysAboveFlip:
    def test_counts_above(self):
        history = [
            {"spot": 5520, "gex_flip": 5500},
            {"spot": 5530, "gex_flip": 5500},
            {"spot": 5540, "gex_flip": 5500},
        ]
        assert compute_days_above_flip(history) == 3

    def test_counts_below_as_negative(self):
        history = [
            {"spot": 5480, "gex_flip": 5500},
            {"spot": 5470, "gex_flip": 5500},
        ]
        assert compute_days_above_flip(history) == -2

    def test_resets_on_direction_change(self):
        history = [
            {"spot": 5520, "gex_flip": 5500},  # above
            {"spot": 5480, "gex_flip": 5500},  # below → breaks streak
            {"spot": 5510, "gex_flip": 5500},  # above
            {"spot": 5515, "gex_flip": 5500},  # above
        ]
        assert compute_days_above_flip(history) == 2

    def test_empty_history(self):
        assert compute_days_above_flip([]) == 0

    def test_missing_flip_stops_count(self):
        history = [
            {"spot": 5520, "gex_flip": 5500},
            {"spot": 5530, "gex_flip": None},
        ]
        assert compute_days_above_flip(history) == 0


# ── History Merge ────────────────────────────────────────────────

class TestMergeHistory:
    def test_deduplicates_by_date(self):
        prior = [{"date": "2026-04-01", "net_gex": 100}]
        current = {"date": "2026-04-01", "net_gex": 200}
        merged = merge_history(prior, current)
        assert len(merged) == 1
        assert merged[0]["net_gex"] == 200

    def test_appends_new_date(self):
        prior = [{"date": "2026-04-01", "net_gex": 100}]
        current = {"date": "2026-04-02", "net_gex": 200}
        merged = merge_history(prior, current)
        assert len(merged) == 2

    def test_caps_at_max_days(self):
        prior = [{"date": f"2026-03-{i+1:02d}", "net_gex": i} for i in range(25)]
        current = {"date": "2026-04-01", "net_gex": 999}
        merged = merge_history(prior, current, max_days=20)
        assert len(merged) == 20
        assert merged[-1]["date"] == "2026-04-01"

    def test_sorted_by_date(self):
        prior = [
            {"date": "2026-04-03", "net_gex": 3},
            {"date": "2026-04-01", "net_gex": 1},
        ]
        current = {"date": "2026-04-02", "net_gex": 2}
        merged = merge_history(prior, current)
        dates = [h["date"] for h in merged]
        assert dates == sorted(dates)


# ── fetch_strike_gex parsing ─────────────────────────────────────

class TestFetchStrikeGex:
    def test_parses_valid_rows(self):
        mock_client = MagicMock()
        mock_client.get_greek_exposure_by_strike.return_value = {
            "data": [
                {
                    "date": "2026-04-02",
                    "strike": "5500",
                    "call_gex": "100.5",
                    "put_gex": "-50.3",
                    "call_delta": "200",
                    "put_delta": "-180",
                },
                {
                    "date": "2026-04-02",
                    "strike": "5525",
                    "call_gex": "80.0",
                    "put_gex": "-30.0",
                    "call_delta": "150",
                    "put_delta": "-120",
                },
            ]
        }
        result = fetch_strike_gex(mock_client, "SPX")
        assert len(result) == 2
        assert result[0]["strike"] == 5500.0
        assert result[0]["call_gex"] == 100.5
        assert result[0]["put_gex"] == -50.3
        assert result[0]["net_gex"] == 100.5 + (-50.3)

    def test_skips_invalid_rows(self):
        mock_client = MagicMock()
        mock_client.get_greek_exposure_by_strike.return_value = {
            "data": [
                {"strike": "bad", "call_gex": "100"},
                {"strike": "5500", "call_gex": "100", "put_gex": "-50",
                 "call_delta": "0", "put_delta": "0"},
            ]
        }
        result = fetch_strike_gex(mock_client, "SPX")
        assert len(result) == 1


# ── Full build_gex_output ────────────────────────────────────────

class TestBuildGexOutput:
    def test_full_output_shape(self):
        result = build_gex_output(
            ticker="SPX",
            strike_data=SAMPLE_STRIKES,
            aggregate_history=[],
            spot=SPOT,
            close=SPOT - 5,
            atm_iv=0.20,
            vol_pc=1.42,
            prior_history=[],
            market_open=False,
        )
        # Check all required top-level keys
        required_keys = [
            "scan_time", "market_open", "ticker", "spot", "close",
            "day_change", "day_change_pct", "net_gex", "net_dex",
            "atm_iv", "vol_pc", "levels", "profile", "expected_range",
            "bias", "history",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

        assert result["ticker"] == "SPX"
        assert result["spot"] == SPOT
        assert isinstance(result["profile"], list)
        assert isinstance(result["levels"], dict)
        assert isinstance(result["bias"], dict)
        assert isinstance(result["history"], list)

    def test_levels_populated(self):
        result = build_gex_output(
            ticker="SPX",
            strike_data=SAMPLE_STRIKES,
            aggregate_history=[],
            spot=SPOT,
            close=SPOT,
            atm_iv=0.20,
            vol_pc=1.0,
            prior_history=[],
            market_open=True,
        )
        levels = result["levels"]
        assert "max_magnet" in levels
        assert "max_accelerator" in levels
        assert "gex_flip" in levels

    def test_day_change_computed(self):
        result = build_gex_output(
            ticker="SPX",
            strike_data=SAMPLE_STRIKES,
            aggregate_history=[],
            spot=5520,
            close=5510,
            atm_iv=0.20,
            vol_pc=1.0,
            prior_history=[],
            market_open=True,
        )
        assert result["day_change"] == 10.0
        assert result["day_change_pct"] == round(10 / 5510 * 100, 4)

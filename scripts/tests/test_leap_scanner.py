"""Tests for leap_iv_scanner.py and leap_scanner_uw.py — HV calculation, mispricing, delta."""
import math
import json
import sys
from contextlib import nullcontext
import pytest

from leap_iv_scanner import (
    calculate_historical_volatility,
    analyze_mispricing,
    find_strikes_by_delta,
    VolatilityData,
)
from leap_scanner_uw import (
    calculate_hv,
    approximate_delta,
    build_json_payload,
    get_current_iv,
    get_leap_options,
    resolve_explicit_tickers,
)
import leap_scanner_uw


# ── calculate_historical_volatility (IB scanner) ────────────────────

class TestCalculateHistoricalVolatility:
    def test_constant_prices_zero_vol(self):
        prices = [100.0] * 30
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_known_series(self):
        """Increasing 1% daily → known annualized vol."""
        # Need period+1 prices, so 21+ for period=20
        # Add some variance: alternate +1% and -1% daily returns
        import random
        random.seed(42)
        prices = [100.0]
        for _ in range(25):
            change = 1 + random.uniform(-0.02, 0.02)
            prices.append(prices[-1] * change)
        hv = calculate_historical_volatility(prices, 20)
        # Should have non-zero volatility
        assert hv > 5

    def test_insufficient_data_returns_zero(self):
        prices = [100, 101, 102]
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_two_prices_returns_zero(self):
        prices = [100, 101]
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_longer_period_uses_recent(self):
        # 100 prices, period=20 → uses last 21 prices
        prices = [100 + i * 0.5 for i in range(100)]
        hv = calculate_historical_volatility(prices, 20)
        assert hv > 0


# ── calculate_hv (UW scanner) ──────────────────────────────────────

class TestCalculateHvUw:
    def test_constant_prices_none_like(self):
        prices = [100.0] * 30
        hv = calculate_hv(prices, 20)
        # Zero returns → zero variance → 0.0
        assert hv == 0.0

    def test_insufficient_data_returns_none(self):
        prices = [100, 101]
        hv = calculate_hv(prices, 20)
        assert hv is None

    def test_valid_series(self):
        prices = [100 * (1.005 ** i) for i in range(25)]
        hv = calculate_hv(prices, 20)
        assert hv is not None
        assert hv > 0


# ── approximate_delta ───────────────────────────────────────────────

class TestApproximateDelta:
    def test_deep_itm(self):
        delta = approximate_delta(strike=80, price=100, iv=30, dte=365)
        assert delta == 0.8

    def test_atm(self):
        delta = approximate_delta(strike=100, price=100, iv=30, dte=365)
        assert delta == 0.5

    def test_slightly_otm(self):
        delta = approximate_delta(strike=110, price=100, iv=30, dte=365)
        assert delta == 0.35

    def test_far_otm(self):
        delta = approximate_delta(strike=140, price=100, iv=30, dte=365)
        assert delta == 0.1

    def test_zero_price(self):
        delta = approximate_delta(strike=100, price=0, iv=30, dte=365)
        assert delta == 0.5

    def test_zero_dte(self):
        delta = approximate_delta(strike=100, price=100, iv=30, dte=0)
        assert delta == 0.5


# ── analyze_mispricing ──────────────────────────────────────────────

class TestAnalyzeMispricing:
    def _make_vol_data(self, hv_20=40, hv_60=35, hv_252=30):
        return VolatilityData(
            ticker="XLK",
            sector="Technology",
            current_price=200,
            hv_20=hv_20,
            hv_60=hv_60,
            hv_252=hv_252,
        )

    def test_hv20_above_iv_mispriced(self):
        vol = self._make_vol_data(hv_20=45, hv_60=40, hv_252=35)
        option = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.30, "theta": -0.02,
            "oi": 1000, "volume": 50,
        }
        result = analyze_mispricing(option, vol, min_gap=15)
        assert result.is_mispriced is True
        assert result.hv_20_gap == 20.0  # 45 - 25
        assert result.mispricing_score > 0

    def test_hv_below_iv_not_mispriced(self):
        vol = self._make_vol_data(hv_20=20, hv_60=22, hv_252=25)
        option = {
            "iv": 30, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.30, "theta": -0.02,
            "oi": 1000, "volume": 50,
        }
        result = analyze_mispricing(option, vol, min_gap=15)
        assert result.is_mispriced is False
        assert result.hv_20_gap < 0

    def test_vega_boost_factor(self):
        vol = self._make_vol_data(hv_20=50, hv_60=45, hv_252=40)
        option_low_vega = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.10, "theta": -0.02,
        }
        option_high_vega = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.45, "theta": -0.02,
        }
        r_low = analyze_mispricing(option_low_vega, vol, min_gap=15)
        r_high = analyze_mispricing(option_high_vega, vol, min_gap=15)
        assert r_high.mispricing_score > r_low.mispricing_score


# ── explicit ticker-scan support (scan-by-ticker) ───────────────────

class TestResolveExplicitTickers:
    def test_merges_positional_and_comma_list(self):
        assert resolve_explicit_tickers(["nvda"], "amd, tsm") == ["NVDA", "AMD", "TSM"]

    def test_dedupes_across_sources(self):
        assert resolve_explicit_tickers(["MU", "mu"], "MU,AAPL") == ["MU", "AAPL"]

    def test_empty_inputs_yield_empty_list(self):
        assert resolve_explicit_tickers([], None) == []
        assert resolve_explicit_tickers(None, "") == []


class TestBuildJsonPayload:
    def test_stamps_universe_and_requested_tickers(self):
        payload = build_json_payload([], 10.0, "explicit", ["NVDA", "AMD"])
        assert payload["universe"] == "explicit"
        assert payload["requested_tickers"] == ["NVDA", "AMD"]
        assert payload["min_gap"] == 10.0
        assert payload["results"] == []
        assert payload["scan_time"]

    def test_preset_universe_stamp(self):
        payload = build_json_payload([], 15.0, "preset:mag7", ["AAPL"])
        assert payload["universe"] == "preset:mag7"

    def test_indexes_universe_stamp(self):
        payload = build_json_payload([], 10.0, "preset:indexes", ["NVDA", "AAPL"])
        assert payload["universe"] == "preset:indexes"


class TestResolveScanInputs:
    def test_indexes_uses_file_preset_and_stamps_universe(self, index_preset_dir):
        import leap_scanner_uw as leap
        from utils.presets import load_preset

        tickers, universe = leap.resolve_scan_inputs(explicit_tickers=[], preset="indexes")
        assert universe == "preset:indexes"
        assert "indexes" not in leap.PRESETS
        assert tickers == load_preset("indexes").tickers
        assert "NVDA" in tickers
        assert "AAPL" in tickers

    def test_explicit_tickers_win_over_preset(self):
        import leap_scanner_uw as leap

        tickers, universe = leap.resolve_scan_inputs(
            explicit_tickers=["nvda"], preset="indexes"
        )
        assert universe == "explicit"
        assert tickers == ["NVDA"]

    def test_workers_arg_defaults_to_16(self):
        import inspect
        import re

        import leap_scanner_uw as leap

        source = inspect.getsource(leap)
        assert re.search(
            r'add_argument\(\s*["\']--workers["\'][\s\S]*?default\s*=\s*16',
            source,
        )


def test_all_provider_failures_preserve_cache_and_fail_health(tmp_path, monkeypatch):
    cache = tmp_path / "leap.json"
    previous = {"scan_time": "old", "results": [{"ticker": "SPY"}]}
    cache.write_text(json.dumps(previous))
    monkeypatch.setattr(leap_scanner_uw, "DASHBOARD_CACHE_PATH", cache)
    monkeypatch.setattr(leap_scanner_uw, "UWClient", lambda: nullcontext(object()))
    monkeypatch.setattr(leap_scanner_uw, "scan_ticker", lambda *_args: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["leap_scanner_uw.py", "SPY", "QQQ", "--json", "--output", str(tmp_path / "report.html")],
    )

    assert leap_scanner_uw.main() == 1
    assert json.loads(cache.read_text()) == previous


# ── find_strikes_by_delta ───────────────────────────────────────────

class TestFindStrikesByDelta:
    def test_finds_closest_match(self):
        options = [
            {"delta": 0.52, "strike": 100},
            {"delta": 0.31, "strike": 110},
            {"delta": 0.19, "strike": 120},
        ]
        result = find_strikes_by_delta(options, [0.50, 0.30, 0.20], 100)
        assert 0.50 in result
        assert result[0.50]["strike"] == 100
        assert 0.30 in result
        assert result[0.30]["strike"] == 110

    def test_no_match_within_threshold(self):
        options = [
            {"delta": 0.80, "strike": 80},
        ]
        result = find_strikes_by_delta(options, [0.50], 100)
        assert 0.50 not in result

    def test_none_delta_skipped(self):
        options = [
            {"delta": None, "strike": 100},
            {"delta": 0.49, "strike": 105},
        ]
        result = find_strikes_by_delta(options, [0.50], 100)
        assert 0.50 in result
        assert result[0.50]["strike"] == 105


# ── null-tolerant UW field coercion (2026-07-05 regression) ─────────
# UW returns keys PRESENT with null values (ETF chains, weekends), so
# dict.get(key, 0) hands None to float()/int() and the TypeError is not
# caught by the loop's (ValueError, IndexError) handler - it killed the
# whole ticker ("Error scanning XLU: float() argument ... 'NoneType'").

class _FakeUW:
    def __init__(self, contracts=None, iv_data=None):
        self._contracts = contracts
        self._iv_data = iv_data

    def get_option_contracts(self, ticker):
        return {"data": self._contracts or []}

    def get_iv_rank(self, ticker):
        return {"data": self._iv_data or []}


class TestNullUwFields:
    def test_null_implied_vol_contract_skipped_not_fatal(self):
        contracts = [
            {  # null IV: must be skipped, not raise TypeError
                "option_symbol": "XLU   270115C00045000",
                "implied_volatility": None,
                "volume": None,
                "open_interest": None,
            },
            {  # healthy contract survives
                "option_symbol": "XLU   270115C00050000",
                "implied_volatility": 0.14,
                "volume": 12,
                "open_interest": 340,
            },
        ]
        leaps = get_leap_options("XLU", min_year=2027, _client=_FakeUW(contracts=contracts))
        assert [l.strike for l in leaps] == [50.0]

    def test_null_volume_and_oi_coerce_to_zero(self):
        contracts = [
            {
                "option_symbol": "XLU   270115C00050000",
                "implied_volatility": 0.14,
                "volume": None,
                "open_interest": None,
            },
        ]
        leaps = get_leap_options("XLU", min_year=2027, _client=_FakeUW(contracts=contracts))
        assert len(leaps) == 1
        assert leaps[0].volume == 0
        assert leaps[0].oi == 0

    def test_null_iv_rank_fields_return_zero(self):
        iv, rank = get_current_iv(
            "XLU", _client=_FakeUW(iv_data=[{"volatility": None, "iv_rank_1y": None}])
        )
        assert iv == 0
        assert rank == 0

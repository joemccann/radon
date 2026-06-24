"""Regression tests for strength_confirmation_scanner.py."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import strength_confirmation_scanner as strength


def test_dedupe_tickers_rejects_numeric_noise() -> None:
    assert strength.dedupe_tickers(["AAPL", "aapl", "2026", "MSFT"]) == ["AAPL", "MSFT"]


def test_resolve_explicit_tickers_overrides_preset() -> None:
    tickers, source = strength.resolve_tickers(["mu", "MU", "2026"], "ndx100")

    assert tickers == ["MU"]
    assert source == "explicit"


def test_build_output_counts_confirmed_strength_only_when_all_groups_pass() -> None:
    confirmed = strength.StrengthCandidate(
        ticker="AAPL",
        verdict="REAL_STRENGTH_CONFIRMED",
        score=100,
        groups_passed=7,
        spot=100,
        factors=[],
        errors=[],
    )
    watch = strength.StrengthCandidate(
        ticker="MSFT",
        verdict="WATCHLIST",
        score=72,
        groups_passed=5,
        spot=300,
        factors=[],
        errors=[],
    )

    payload = strength.build_output([watch, confirmed], "explicit", 2, requested_tickers=["AAPL", "MSFT"])

    assert payload["confirmed_strength_count"] == 1
    assert payload["candidates_found"] == 2
    assert payload["requested_tickers"] == ["AAPL", "MSFT"]
    assert payload["results"][0]["ticker"] == "AAPL"


def test_resolve_ndx100_uses_fallback_when_preset_is_structurally_corrupt(monkeypatch) -> None:
    import utils.presets as presets

    loaded = SimpleNamespace(name="ndx100", tickers=["1985", "2024", "2025"])
    monkeypatch.setattr(presets, "load_preset", lambda _name: loaded)

    tickers, source = strength.resolve_tickers([], "ndx100")

    assert source == "fallback:ndx100"
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    assert "1985" not in tickers


class BullishClient:
    def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
        assert ticker == "AAPL"
        assert candle_size == "1d"
        closes = [85 + i * 0.22 for i in range(70)]
        return {"data": [{"close": close} for close in closes]}

    def get_stock_flow_alerts(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"option_type": "call", "ask_side": True, "premium": 500_000},
                {"option_type": "call", "side": "ask", "premium": 400_000},
                {"option_type": "put", "bid_side": True, "premium": 150_000},
            ]
        }

    def get_options_volume(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": [{"call_premium": 1_200_000, "put_premium": 350_000}]}

    def get_iv_rank(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": [{"date": "2026-06-24", "volatility": 0.22, "iv_rank_1y": 24}]}

    def get_greek_exposure_by_strike(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"strike": 105, "call_gex": 500_000, "put_gex": -180_000},
                {"strike": 110, "call_gex": 900_000, "put_gex": -70_000},
                {"strike": 115, "call_gex": 650_000, "put_gex": -20_000},
            ]
        }

    def get_greek_exposure(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"date": "2026-06-22", "call_gex": 1_000_000, "put_gex": -1_400_000},
                {"date": "2026-06-23", "call_gex": 1_300_000, "put_gex": -1_100_000},
                {"date": "2026-06-24", "call_gex": 1_800_000, "put_gex": -900_000},
            ]
        }

    def get_stock_oi_change(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"strike": 110, "expiry": "2026-07-17", "option_type": "call", "oi_change": 1200},
                {"strike": 115, "expiry": "2026-07-17", "option_type": "call", "oi_change": 900},
                {"strike": 120, "expiry": "2026-08-21", "option_type": "call", "oi_change": 700},
            ]
        }

    def get_volatility_term_structure(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"expiry": "2026-07-17", "volatility": 0.20},
                {"expiry": "2026-08-21", "volatility": 0.24},
                {"expiry": "2026-09-18", "volatility": 0.25},
            ]
        }

    def get_option_contracts(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"option_symbol": "AAPL260717P00100000", "implied_volatility": 0.28, "delta": -0.25},
                {"option_symbol": "AAPL260717C00120000", "implied_volatility": 0.30, "delta": 0.25},
                {"option_symbol": "AAPL260717C00130000", "implied_volatility": 0.34, "delta": 0.10},
            ]
        }

    def get_historical_risk_reversal_skew(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"date": "2026-06-20", "value": 0.08},
                {"date": "2026-06-24", "value": 0.03},
            ]
        }

    def get_stock_info(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": {"sector": "Technology"}}

    def get_sector_etfs(self):
        return {
            "data": [
                {"ticker": "XLK", "change": 1.2, "call_premium": 2_000_000, "put_premium": 600_000},
                {"ticker": "XLF", "change": 0.4, "call_premium": 1_200_000, "put_premium": 900_000},
                {"ticker": "XLV", "change": 0.3, "call_premium": 1_100_000, "put_premium": 800_000},
                {"ticker": "XLY", "change": 0.6, "call_premium": 1_000_000, "put_premium": 700_000},
            ]
        }


def test_scan_ticker_confirms_real_strength_when_all_groups_pass() -> None:
    candidate = strength.scan_ticker(
        "AAPL",
        client=BullishClient(),
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=strength.MarketContext(
            vix_front=17.0,
            vix_back=19.5,
            vix_front_prev=18.0,
            vix_back_prev=19.7,
            cta_exposure_pct=130.0,
            cta_forced_reduction_pct=0.0,
            spx_distance_pct=2.0,
            sectors_positive_ratio=0.75,
            sectors_call_premium_ratio=0.67,
            notes=[],
        ),
    )

    assert candidate is not None
    assert candidate.verdict == "REAL_STRENGTH_CONFIRMED"
    assert candidate.groups_passed == 7
    assert candidate.score == 100
    factor_map = {factor.group: factor for factor in candidate.factors}
    assert factor_map["Q-SCORES"].passed is True
    assert factor_map["SYSTEMATIC POSITIONING"].source == "APPROX"
    assert factor_map["MARKET BREADTH"].source in {"UW", "APPROX"}


def test_scan_ticker_returns_watchlist_when_one_group_fails() -> None:
    weak_context = strength.MarketContext(
        vix_front=20.0,
        vix_back=18.5,
        vix_front_prev=19.5,
        vix_back_prev=18.6,
        cta_exposure_pct=130.0,
        cta_forced_reduction_pct=0.0,
        spx_distance_pct=2.0,
        sectors_positive_ratio=0.75,
        sectors_call_premium_ratio=0.67,
        notes=[],
    )

    candidate = strength.scan_ticker(
        "AAPL",
        client=BullishClient(),
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=weak_context,
    )

    assert candidate is not None
    assert candidate.verdict == "WATCHLIST"
    assert candidate.groups_passed == 6
    assert {factor.group: factor for factor in candidate.factors}["TERM STRUCTURE"].passed is False

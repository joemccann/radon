"""Regression tests for theta_harvester_scanner.py."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import theta_harvester_scanner as theta


def test_dedupe_tickers_rejects_corrupt_numeric_preset_entries() -> None:
    assert theta.dedupe_tickers(["AAPL", "aapl", "1985", "2025", "MSFT"]) == [
        "AAPL",
        "MSFT",
    ]


def test_resolve_explicit_tickers_overrides_preset() -> None:
    tickers, source = theta.resolve_tickers(["mu", "MU", "2026"], "ndx100")

    assert tickers == ["MU"]
    assert source == "explicit"


def test_build_output_records_requested_tickers() -> None:
    payload = theta.build_output([], "explicit", 1, requested_tickers=["MU"])

    assert payload["universe"] == "explicit"
    assert payload["requested_tickers"] == ["MU"]
    assert payload["tickers_scanned"] == 1


def test_resolve_ndx100_uses_fallback_when_preset_is_structurally_corrupt(monkeypatch) -> None:
    import utils.presets as presets

    loaded = SimpleNamespace(name="ndx100", tickers=["1985", "2024", "2025"])
    monkeypatch.setattr(presets, "load_preset", lambda _name: loaded)

    tickers, source = theta.resolve_tickers([], "ndx100")

    assert source == "fallback:ndx100"
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    assert "1985" not in tickers


def test_select_short_strangle_prefers_near_flat_short_delta() -> None:
    now = datetime(2026, 6, 24, tzinfo=timezone.utc)
    contracts = {
        "data": [
            {
                "option_symbol": "AAPL260717P00095000",
                "delta": -0.15,
                "theta": -0.04,
                "gamma": 0.002,
                "vega": 0.018,
                "bid": 0.9,
                "ask": 1.1,
            },
            {
                "option_symbol": "AAPL260717C00105000",
                "delta": 0.16,
                "theta": -0.035,
                "gamma": 0.0022,
                "vega": 0.02,
                "bid": 0.8,
                "ask": 1.0,
            },
            {
                "option_symbol": "AAPL260717C00120000",
                "delta": 0.06,
                "theta": -0.01,
                "gamma": 0.0005,
                "vega": 0.006,
                "bid": 0.1,
                "ask": 0.2,
            },
        ],
    }

    structure = theta.select_short_strangle(contracts, spot=100.0, fallback_iv=35.0, now=now)

    assert structure is not None
    assert structure.short_put.strike == 95.0
    assert structure.short_call.strike == 105.0
    assert structure.net_delta == pytest.approx(-0.01)
    assert structure.theta == pytest.approx(0.075)
    assert structure.gamma < 0
    assert structure.credit == pytest.approx(1.9)


class FakeUWClient:
    def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
        assert ticker == "AAPL"
        assert candle_size == "1d"
        return {"data": [{"close": 100.0} for _ in range(70)]}

    def get_iv_rank(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {
                    "date": "2026-06-24",
                    "volatility": 0.35,
                    "iv_rank_1y": 72,
                }
            ]
        }

    def get_option_contracts(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {
                    "option_symbol": "AAPL260717P00095000",
                    "delta": -0.15,
                    "theta": -0.04,
                    "gamma": 0.002,
                    "vega": 0.018,
                    "bid": 0.9,
                    "ask": 1.1,
                    "volume": 200,
                    "open_interest": 900,
                },
                {
                    "option_symbol": "AAPL260717C00105000",
                    "delta": 0.16,
                    "theta": -0.035,
                    "gamma": 0.0022,
                    "vega": 0.02,
                    "bid": 0.8,
                    "ask": 1.0,
                    "volume": 180,
                    "open_interest": 850,
                },
            ]
        }

    def get_greek_exposure_by_strike(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"strike": 90, "call_gex": 750_000, "put_gex": -50_000},
                {"strike": 100, "call_gex": 500_000, "put_gex": -25_000},
                {"strike": 110, "call_gex": 350_000, "put_gex": -10_000},
            ]
        }


def test_scan_ticker_scores_true_theta_when_delta_iv_dealer_and_range_gates_pass() -> None:
    now = datetime(2026, 6, 24, tzinfo=timezone.utc)

    candidate = theta.scan_ticker("AAPL", client=FakeUWClient(), now=now)

    assert candidate is not None
    assert candidate.ticker == "AAPL"
    assert candidate.verdict == "THETA_HARVEST"
    assert candidate.setup == "TRUE_THETA"
    assert candidate.gates["delta_near_zero"] is True
    assert candidate.gates["iv_rich_vs_rv"] is True
    assert candidate.gates["dealer_support"] is True
    assert candidate.gates["theta_positive"] is True
    assert candidate.gates["range_bound"] is True
    assert candidate.structure.net_delta == pytest.approx(-0.01)
    assert candidate.iv_rv_edge == pytest.approx(35.0)

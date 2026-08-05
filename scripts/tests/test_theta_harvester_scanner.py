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


# ── DTE window + min-credit search params ─────────────────────────

_NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)


def _strangle_contracts() -> dict:
    """A single 23-DTE strangle (expiry 2026-07-17), credit 1.9 per share."""
    return {
        "data": [
            {"option_symbol": "AAPL260717P00095000", "delta": -0.15, "theta": -0.04,
             "gamma": 0.002, "vega": 0.018, "bid": 0.9, "ask": 1.1},
            {"option_symbol": "AAPL260717C00105000", "delta": 0.16, "theta": -0.035,
             "gamma": 0.0022, "vega": 0.02, "bid": 0.8, "ask": 1.0},
        ],
    }


def test_select_short_strangle_dte_window_excludes_out_of_range_expiry() -> None:
    # The only pair is 23 DTE; a 30-DTE minimum must reject it entirely.
    assert theta.select_short_strangle(
        _strangle_contracts(), spot=100.0, fallback_iv=35.0, now=_NOW, min_dte=30, max_dte=60,
    ) is None


def test_select_short_strangle_dte_window_includes_in_range_expiry() -> None:
    structure = theta.select_short_strangle(
        _strangle_contracts(), spot=100.0, fallback_iv=35.0, now=_NOW, min_dte=14, max_dte=30,
    )
    assert structure is not None
    assert structure.dte == 23


def test_select_short_strangle_min_credit_rejects_thin_premium() -> None:
    # Credit is 1.9/share; a 2.5 floor rejects, a 1.0 floor keeps it.
    assert theta.select_short_strangle(
        _strangle_contracts(), spot=100.0, fallback_iv=35.0, now=_NOW, min_credit=2.5,
    ) is None
    kept = theta.select_short_strangle(
        _strangle_contracts(), spot=100.0, fallback_iv=35.0, now=_NOW, min_credit=1.0,
    )
    assert kept is not None
    assert kept.credit == pytest.approx(1.9)


def test_build_output_records_search_params() -> None:
    default = theta.build_output([], "explicit", 1)
    assert default["params"] == {"min_dte": theta.MIN_DTE, "max_dte": theta.MAX_DTE, "min_credit": 0.0}

    custom = theta.build_output([], "explicit", 1, params={"min_dte": 21, "max_dte": 60, "min_credit": 2.0})
    assert custom["params"] == {"min_dte": 21, "max_dte": 60, "min_credit": 2.0}


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
    # Earnings is post-process only; scan_ticker leaves it null by default.
    assert candidate.earnings is None


# ── Earnings annotation (surface risk only; no verdict/score gate) ─


def _minimal_candidate(
    ticker: str = "AAPL",
    dte: int = 23,
    score: float = 80.0,
    verdict: str = "THETA_HARVEST",
) -> theta.ThetaCandidate:
    """Build a ThetaCandidate with a stub structure for annotation unit tests."""
    put = theta.OptionLeg(
        symbol=f"{ticker}260717P00095000",
        expiry="20260717",
        strike=95.0,
        right="P",
        iv=35.0,
        delta=-0.15,
        theta=-0.04,
        gamma=0.002,
        vega=0.018,
        bid=0.9,
        ask=1.1,
    )
    call = theta.OptionLeg(
        symbol=f"{ticker}260717C00105000",
        expiry="20260717",
        strike=105.0,
        right="C",
        iv=35.0,
        delta=0.16,
        theta=-0.035,
        gamma=0.0022,
        vega=0.02,
        bid=0.8,
        ask=1.0,
    )
    structure = theta.ThetaStructure(
        expiry="20260717",
        dte=dte,
        short_put=put,
        short_call=call,
        net_delta=-0.01,
        theta=0.075,
        gamma=-0.0042,
        vega=-0.038,
        credit=1.9,
    )
    return theta.ThetaCandidate(
        ticker=ticker,
        score=score,
        verdict=verdict,
        structure=structure,
        spot=100.0,
        iv=35.0,
        hv20=20.0,
        hv60=22.0,
        iv_rv_edge=15.0,
        iv_rv_ratio=1.75,
        trend_20d_pct=1.0,
        range_score=0.8,
        dealer_support="SUPPORT",
        net_gex=1_000_000.0,
        gex_flip=90.0,
        setup="TRUE_THETA" if verdict == "THETA_HARVEST" else verdict,
        gates={
            "delta_near_zero": True,
            "iv_rich_vs_rv": True,
            "dealer_support": True,
            "theta_positive": True,
            "gamma_controlled": True,
            "range_bound": True,
        },
        errors=[],
        earnings=None,
    )


class FakeEarningsClient:
    """Mock UW client that only implements get_earnings_by_ticker."""

    def __init__(self, by_ticker: dict | None = None, *, fail: bool = False):
        self.by_ticker = by_ticker or {}
        self.fail = fail
        self.calls: list[str] = []

    def get_earnings_by_ticker(self, ticker: str) -> dict:
        self.calls.append(ticker.upper())
        if self.fail:
            raise RuntimeError("uw earnings unavailable")
        if ticker.upper() in self.by_ticker:
            payload = self.by_ticker[ticker.upper()]
            if isinstance(payload, Exception):
                raise payload
            return payload
        return {"data": []}


# Midday ET so calendar-day math is unambiguous (earnings_dates uses ET today).
_EARNINGS_NOW = datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc)  # 10:00 ET


def test_annotate_candidates_attaches_earnings_within_dte() -> None:
    candidate = _minimal_candidate("AAPL", dte=23)
    client = FakeEarningsClient(
        {
            "AAPL": {
                "data": [
                    {
                        "report_date": "2026-07-10",
                        "report_time": "postmarket",
                        "source": "company",
                        "expected_move_perc": 0.05,
                        "actual_eps": None,
                    }
                ]
            }
        }
    )

    out = theta.annotate_candidates_with_earnings([candidate], client, now=_EARNINGS_NOW)

    assert out is candidate or out[0] is candidate
    assert candidate.verdict == "THETA_HARVEST"
    assert candidate.score == 80.0
    assert candidate.earnings is not None
    assert candidate.earnings["report_date"] == "2026-07-10"
    assert candidate.earnings["report_time"] == "postmarket"
    assert candidate.earnings["days_until"] == 16  # Jun 24 → Jul 10
    assert candidate.earnings["within_dte"] is True  # 16 <= dte 23
    assert candidate.earnings["source"] == "company"
    assert candidate.earnings["expected_move_pct"] == pytest.approx(5.0)
    # Slim field only — no bookkeeping keys.
    assert "missing" not in candidate.earnings
    assert "ticker" not in candidate.earnings
    assert "dte" not in candidate.earnings
    assert client.calls == ["AAPL"]


def test_annotate_candidates_earnings_outside_dte_still_surfaces() -> None:
    candidate = _minimal_candidate("AAPL", dte=14)
    client = FakeEarningsClient(
        {
            "AAPL": {
                "data": [
                    {
                        "report_date": "2026-07-20",
                        "report_time": "premarket",
                        "source": "estimation",
                        "expected_move_perc": 4.0,
                        "actual_eps": None,
                    }
                ]
            }
        }
    )

    theta.annotate_candidates_with_earnings([candidate], client, now=_EARNINGS_NOW)

    assert candidate.earnings is not None
    assert candidate.earnings["days_until"] == 26  # Jun 24 → Jul 20
    assert candidate.earnings["within_dte"] is False  # 26 > dte 14
    # v1: still surface; do not flip verdict.
    assert candidate.verdict == "THETA_HARVEST"


def test_annotate_candidates_missing_earnings_is_null() -> None:
    candidate = _minimal_candidate("MSFT", dte=30)
    client = FakeEarningsClient({"MSFT": {"data": []}})

    theta.annotate_candidates_with_earnings([candidate], client, now=_EARNINGS_NOW)

    assert candidate.earnings is None
    assert candidate.verdict == "THETA_HARVEST"


def test_annotate_candidates_fetch_failure_keeps_candidate() -> None:
    candidate = _minimal_candidate("HONA", dte=16, score=75.0)
    client = FakeEarningsClient(fail=True)

    theta.annotate_candidates_with_earnings(
        [candidate], client, now=datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
    )

    # annotate swallows per-ticker errors into missing → slim field null.
    assert candidate.earnings is None
    assert candidate.verdict == "THETA_HARVEST"
    assert candidate.score == 75.0


def test_annotate_candidates_batch_uses_structure_dte_per_ticker() -> None:
    aapl = _minimal_candidate("AAPL", dte=10)
    msft = _minimal_candidate("MSFT", dte=45)
    client = FakeEarningsClient(
        {
            "AAPL": {
                "data": [
                    {
                        "report_date": "2026-07-05",
                        "report_time": "AMC",
                        "source": "company",
                        "expected_move_perc": 0.06,
                        "actual_eps": None,
                    }
                ]
            },
            "MSFT": {
                "data": [
                    {
                        "report_date": "2026-07-05",
                        "report_time": "BMO",
                        "source": "company",
                        "expected_move_perc": 0.04,
                        "actual_eps": None,
                    }
                ]
            },
        }
    )

    theta.annotate_candidates_with_earnings([aapl, msft], client, now=_EARNINGS_NOW)

    assert client.calls == ["AAPL", "MSFT"]
    assert aapl.earnings is not None
    assert aapl.earnings["days_until"] == 11  # Jun 24 → Jul 5
    assert aapl.earnings["within_dte"] is False  # 11 > 10
    assert msft.earnings is not None
    assert msft.earnings["within_dte"] is True  # 11 <= 45


def test_build_output_includes_serializable_earnings_field() -> None:
    candidate = _minimal_candidate("AAPL", dte=23)
    candidate.earnings = {
        "report_date": "2026-07-10",
        "report_time": "postmarket",
        "days_until": 16,
        "within_dte": True,
        "source": "company",
        "expected_move_pct": 5.0,
    }
    payload = theta.build_output([candidate], "explicit", 1, requested_tickers=["AAPL"])
    row = payload["results"][0]
    assert row["earnings"]["report_date"] == "2026-07-10"
    assert row["earnings"]["within_dte"] is True
    # asdict must remain JSON-serializable
    import json

    json.dumps(payload)


def test_slim_earnings_field_null_when_missing() -> None:
    assert theta._slim_earnings_field({"missing": True, "report_date": None}) is None
    assert theta._slim_earnings_field({}) is None
    assert theta._slim_earnings_field(
        {
            "missing": False,
            "report_date": "2026-08-05",
            "report_time": "postmarket",
            "days_until": 0,
            "within_dte": True,
            "source": "company",
            "expected_move_pct": 8.76,
            "ticker": "HONA",
            "dte": 16,
        }
    ) == {
        "report_date": "2026-08-05",
        "report_time": "postmarket",
        "days_until": 0,
        "within_dte": True,
        "source": "company",
        "expected_move_pct": 8.76,
    }

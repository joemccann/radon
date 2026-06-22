"""Unit tests for the portfolio correlation risk-budget guard (F8).

Synthetic price series with known correlation structure drive every assertion.
No live IB/UW/Turso/network access — all inputs are in-memory dicts.
"""

import math

import pytest

from portfolio_risk import (
    DEFAULT_CORR_THRESHOLD,
    DEFAULT_MIN_OVERLAP,
    build_correlation_matrix,
    build_risk_budget_report,
    position_underlying,
)


# ── Synthetic price-series helpers ───────────────────────────────────────────


def _series(start: float, steps, dates=None):
    """Build a {date: price} series from successive multiplicative steps."""
    prices = [start]
    for step in steps:
        prices.append(prices[-1] * (1.0 + step))
    if dates is None:
        dates = [f"2026-01-{i + 1:02d}" for i in range(len(prices))]
    return {d: round(p, 4) for d, p in zip(dates, prices)}


def _correlated_pair(returns_a, beta, noise):
    """series_b return = beta * series_a return + noise, both anchored at 100."""
    a = _series(100.0, returns_a)
    b_steps = [beta * r + n for r, n in zip(returns_a, noise)]
    b = _series(100.0, b_steps)
    return a, b


_RET_A = [0.02, -0.01, 0.03, -0.02, 0.015, -0.005, 0.025, -0.018, 0.012, 0.008]
_ZERO_NOISE = [0.0] * len(_RET_A)
_ANTI = [-r for r in _RET_A]


# ── position_underlying ──────────────────────────────────────────────────────


class TestPositionUnderlying:
    def test_stock_maps_to_ticker(self):
        assert position_underlying({"ticker": "AAPL", "structure_type": "Stock"}) == "AAPL"

    def test_option_maps_to_underlying_ticker(self):
        pos = {"ticker": "CRCL", "structure_type": "Long Call", "expiry": "2026-06-18"}
        assert position_underlying(pos) == "CRCL"

    def test_symbol_key_fallback(self):
        assert position_underlying({"symbol": "MSFT"}) == "MSFT"


# ── build_correlation_matrix ─────────────────────────────────────────────────


class TestCorrelationMatrix:
    def test_perfectly_correlated_series(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        matrix = build_correlation_matrix(
            {"AAA": a, "BBB": b}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        assert matrix["AAA"]["BBB"] == pytest.approx(1.0, abs=1e-6)
        assert matrix["AAA"]["AAA"] == pytest.approx(1.0, abs=1e-9)

    def test_anti_correlated_series(self):
        a = _series(100.0, _RET_A)
        b = _series(100.0, _ANTI)
        matrix = build_correlation_matrix(
            {"AAA": a, "INV": b}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        # Inverted simple-return moves are not perfectly symmetric in log space,
        # so the correlation is very strongly negative but not exactly -1.0.
        assert matrix["AAA"]["INV"] < -0.99

    def test_insufficient_overlap_marked_none(self):
        a = _series(100.0, _RET_A)
        # Only three overlapping dates with the others — below min_overlap.
        short = {"2026-01-01": 10.0, "2026-01-02": 10.2, "2026-01-03": 10.1}
        matrix = build_correlation_matrix(
            {"AAA": a, "THIN": short}, min_overlap=DEFAULT_MIN_OVERLAP
        )
        assert matrix["AAA"]["THIN"] is None
        assert matrix["THIN"]["AAA"] is None


# ── build_risk_budget_report ─────────────────────────────────────────────────


def _portfolio(positions, bankroll=1_000_000.0):
    return {"bankroll": bankroll, "positions": positions}


class TestRiskBudgetReport:
    def test_correlated_stack_breaches_budget(self):
        """Two highly-correlated names each at the 2.5% cap stack to 5% > budget."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio,
            {"AAA": a, "BBB": b},
            corr_threshold=DEFAULT_CORR_THRESHOLD,
        )

        assert len(report["clusters"]) == 1
        cluster = report["clusters"][0]
        assert set(cluster["tickers"]) == {"AAA", "BBB"}
        # Aggregate exposure is the sum of the two weights (~5% of book).
        assert cluster["aggregate_exposure"] == pytest.approx(0.05, abs=1e-6)
        assert cluster["budget"] == pytest.approx(0.025, abs=1e-9)
        assert cluster["breached"] is True

        assert len(report["breaches"]) == 1
        assert set(report["breaches"][0]["tickers"]) == {"AAA", "BBB"}

    def test_uncorrelated_positions_do_not_cluster(self):
        a = _series(100.0, _RET_A)
        b = _series(100.0, _ANTI)  # anti-correlated → not a cluster
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "INV", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "INV": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        assert report["clusters"] == []
        assert report["breaches"] == []

    def test_correlated_but_under_budget_does_not_breach(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        small = bankroll * 0.005
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": small},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": small},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        # They cluster (highly correlated) but 1% aggregate < 2.5% budget.
        assert len(report["clusters"]) == 1
        assert report["clusters"][0]["breached"] is False
        assert report["breaches"] == []

    def test_option_position_maps_to_underlying_series(self):
        """An option position correlates via its underlying's price history."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {
                    "ticker": "AAA",
                    "structure_type": "Long Call",
                    "expiry": "2026-06-18",
                    "market_value": cap,
                    "max_risk": cap,
                },
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        assert len(report["clusters"]) == 1
        assert set(report["clusters"][0]["tickers"]) == {"AAA", "BBB"}
        assert report["clusters"][0]["breached"] is True

    def test_insufficient_data_path_surfaced(self):
        """A position with too-thin history is reported, not silently dropped."""
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        thin = {"2026-01-01": 10.0, "2026-01-02": 10.2}
        bankroll = 1_000_000.0
        cap = bankroll * 0.025
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": cap},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": cap},
                {"ticker": "THIN", "structure_type": "Stock", "market_value": cap},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio,
            {"AAA": a, "BBB": b, "THIN": thin},
            corr_threshold=DEFAULT_CORR_THRESHOLD,
        )
        assert "THIN" in report["insufficient_data"]
        # AAA/BBB still cluster and breach despite THIN being unmeasurable.
        assert len(report["clusters"]) == 1
        assert "THIN" not in report["clusters"][0]["tickers"]

    def test_aggregate_exposure_is_book_fraction(self):
        a, b = _correlated_pair(_RET_A, beta=1.0, noise=_ZERO_NOISE)
        bankroll = 2_000_000.0
        portfolio = _portfolio(
            [
                {"ticker": "AAA", "structure_type": "Stock", "market_value": 60_000.0},
                {"ticker": "BBB", "structure_type": "Stock", "market_value": 40_000.0},
            ],
            bankroll=bankroll,
        )
        report = build_risk_budget_report(
            portfolio, {"AAA": a, "BBB": b}, corr_threshold=DEFAULT_CORR_THRESHOLD
        )
        # (60k + 40k) / 2M = 0.05
        assert report["aggregate_exposure"] == pytest.approx(0.05, abs=1e-9)

    def test_empty_portfolio_is_graceful(self):
        report = build_risk_budget_report(_portfolio([]), {})
        assert report["clusters"] == []
        assert report["breaches"] == []
        assert report["aggregate_exposure"] == 0.0
        assert report["insufficient_data"] == []

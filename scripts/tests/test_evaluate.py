#!/usr/bin/env python3
"""Tests for the evaluate.py unified evaluation script.

Uses RED/GREEN TDD — tests written first, then implementation to pass them.
All external dependencies (UW API, IB) are mocked.
"""
import json
import sys
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Fixtures: Synthetic data for each milestone
# ---------------------------------------------------------------------------

@pytest.fixture
def ticker_data():
    """Milestone 1 — Ticker validation result."""
    return {
        "ticker": "AAPL",
        "fetched_at": "2026-03-05T11:00:00",
        "verified": True,
        "company_name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "market_cap": 3_850_000_000_000,
        "avg_volume": 49_300_000,
        "current_price": 258.26,
        "options_available": True,
        "error": None,
    }


@pytest.fixture
def ticker_data_invalid():
    """Milestone 1 — Ticker not found."""
    return {
        "ticker": "ZZZZZ",
        "fetched_at": "2026-03-05T11:00:00",
        "verified": False,
        "company_name": None,
        "sector": None,
        "options_available": False,
        "error": "Ticker 'ZZZZZ' not found",
    }


@pytest.fixture
def flow_data_accumulation():
    """Milestone 2 — Strong accumulation signal."""
    return {
        "ticker": "AAPL",
        "fetched_at": "2026-03-05T11:00:00",
        "trading_days_checked": [
            "2026-03-05", "2026-03-04", "2026-03-03", "2026-03-02", "2026-02-27", "2026-02-26"
        ],
        "dark_pool": {
            "aggregate": {
                "total_volume": 30_000_000,
                "total_premium": 8_000_000_000,
                "buy_volume": 27_000_000,
                "sell_volume": 3_000_000,
                "dp_buy_ratio": 0.90,
                "flow_direction": "ACCUMULATION",
                "flow_strength": 80.0,
                "num_prints": 2500,
            },
            "daily": [
                {"date": "2026-03-05", "dp_buy_ratio": 0.85, "flow_direction": "ACCUMULATION", "flow_strength": 70.0, "total_premium": 1_200_000_000},
                {"date": "2026-03-04", "dp_buy_ratio": 0.88, "flow_direction": "ACCUMULATION", "flow_strength": 76.0, "total_premium": 1_400_000_000},
                {"date": "2026-03-03", "dp_buy_ratio": 0.92, "flow_direction": "ACCUMULATION", "flow_strength": 84.0, "total_premium": 1_600_000_000},
                {"date": "2026-03-02", "dp_buy_ratio": 0.95, "flow_direction": "ACCUMULATION", "flow_strength": 90.0, "total_premium": 1_800_000_000},
                {"date": "2026-02-27", "dp_buy_ratio": 0.99, "flow_direction": "ACCUMULATION", "flow_strength": 98.0, "total_premium": 3_000_000_000},
                {"date": "2026-02-26", "dp_buy_ratio": 0.75, "flow_direction": "ACCUMULATION", "flow_strength": 50.0, "total_premium": 900_000_000},
            ],
        },
        "options_flow": {"bias": "BULLISH"},
        "combined_signal": "STRONG_BULLISH_CONFLUENCE",
    }


@pytest.fixture
def flow_data_fading():
    """Milestone 2 — Accumulation fading (like real AAPL Mar 5)."""
    return {
        "ticker": "AAPL",
        "fetched_at": "2026-03-05T11:00:00",
        "trading_days_checked": [
            "2026-03-05", "2026-03-04", "2026-03-03", "2026-03-02", "2026-02-27", "2026-02-26"
        ],
        "dark_pool": {
            "aggregate": {
                "total_volume": 30_000_000,
                "total_premium": 7_900_000_000,
                "buy_volume": 24_000_000,
                "sell_volume": 6_000_000,
                "dp_buy_ratio": 0.809,
                "flow_direction": "ACCUMULATION",
                "flow_strength": 61.9,
                "num_prints": 2500,
            },
            "daily": [
                {"date": "2026-03-05", "dp_buy_ratio": 0.552, "flow_direction": "NEUTRAL", "flow_strength": 10.5, "total_premium": 118_000_000},
                {"date": "2026-03-04", "dp_buy_ratio": 0.742, "flow_direction": "ACCUMULATION", "flow_strength": 48.4, "total_premium": 1_329_000_000},
                {"date": "2026-03-03", "dp_buy_ratio": 0.256, "flow_direction": "DISTRIBUTION", "flow_strength": 48.8, "total_premium": 1_012_000_000},
                {"date": "2026-03-02", "dp_buy_ratio": 0.917, "flow_direction": "ACCUMULATION", "flow_strength": 83.4, "total_premium": 1_386_000_000},
                {"date": "2026-02-27", "dp_buy_ratio": 0.993, "flow_direction": "ACCUMULATION", "flow_strength": 98.6, "total_premium": 3_167_000_000},
                {"date": "2026-02-26", "dp_buy_ratio": 0.757, "flow_direction": "ACCUMULATION", "flow_strength": 51.3, "total_premium": 928_000_000},
            ],
        },
        "options_flow": {"bias": "LEAN_BULLISH"},
        "combined_signal": "DP_ACCUMULATION_ONLY",
    }


@pytest.fixture
def options_data_bullish():
    """Milestone 3 — Bullish options chain + flow."""
    return {
        "ticker": "AAPL",
        "fetched_at": "2026-03-05T11:00:00",
        "chain": {
            "bias": "LEAN_BULLISH",
            "put_call_ratio": 0.53,
            "call_premium": 39_000_000,
            "put_premium": 21_000_000,
            "call_volume": 306_000,
            "put_volume": 147_000,
            "call_oi": 470_000,
            "put_oi": 80_000,
        },
        "flow": {
            "flow_bias": "BULLISH",
            "recent_bias": "BEARISH",
            "flow_strength": 30,
            "total_alerts": 100,
            "call_premium": 13_900_000,
            "put_premium": 7_300_000,
            "sweep_premium": 3_200_000,
        },
        "analysis": {
            "chain_bias": "LEAN_BULLISH",
            "flow_bias": "BULLISH",
            "combined_bias": "BULLISH",
            "confidence": "HIGH",
            "signals": [],
        },
    }


@pytest.fixture
def oi_data_massive():
    """Milestone 3B — Massive OI change (institutional positioning)."""
    return [
        {
            "option_symbol": "AAPL260417C00270000",
            "ticker": "AAPL",
            "oi_diff_plain": 11372,
            "prev_oi": 9535,
            "current_oi": 20907,
            "prev_total_premium": "11792066",
            "option_type": "call",
            "strike": "270.0",
            "expires": "2026-04-17",
            "signal": "MASSIVE",
            "premium": 11_792_066,
        },
        {
            "option_symbol": "AAPL260306C00265000",
            "ticker": "AAPL",
            "oi_diff_plain": 2279,
            "prev_oi": 7814,
            "current_oi": 10093,
            "prev_total_premium": "5726761",
            "option_type": "call",
            "strike": "265.0",
            "expires": "2026-03-06",
            "signal": "LARGE",
            "premium": 5_726_761,
        },
    ]


@pytest.fixture
def analyst_data_bullish():
    """Milestone 1C — Bullish analyst consensus."""
    return {
        "ticker": "AAPL",
        "source": "uw",
        "ratings": {
            "buy_pct": 75.9,
            "hold_pct": 17.2,
            "sell_pct": 6.9,
            "total": 29,
            "recommendation": "buy",
        },
        "price_target": {"mean": 288.0},
    }


@pytest.fixture
def price_history():
    """IB historical bars — price action for edge check."""
    return [
        {"date": "2026-02-27", "open": 272.77, "close": 264.18, "volume": 26_248_237},
        {"date": "2026-03-02", "open": 262.46, "close": 264.72, "volume": 18_290_672},
        {"date": "2026-03-03", "open": 263.48, "close": 263.75, "volume": 18_325_414},
        {"date": "2026-03-04", "open": 264.70, "close": 262.52, "volume": 18_489_944},
        {"date": "2026-03-05", "open": 260.79, "close": 258.26, "volume": 15_257_620},
    ]


# ---------------------------------------------------------------------------
# Module-level import for the evaluate module
# ---------------------------------------------------------------------------

from evaluate import (
    EvaluationResult,
    MilestoneResult,
    compute_sustained_days,
    determine_edge,
    run_evaluation,
    format_report,
)


# ===========================================================================
# 1. EvaluationResult data class
# ===========================================================================

class TestEvaluationResult:
    """EvaluationResult holds all milestone outputs and the final verdict."""

    def test_initial_state(self):
        r = EvaluationResult(ticker="AAPL")
        assert r.ticker == "AAPL"
        assert r.decision == "PENDING"
        assert r.failing_gate is None
        assert r.milestones == {}

    def test_add_milestone(self):
        r = EvaluationResult(ticker="AAPL")
        m = MilestoneResult(name="ticker_validation", passed=True, data={"verified": True})
        r.milestones["M1"] = m
        assert r.milestones["M1"].passed is True

    def test_failed_milestone_sets_gate(self):
        r = EvaluationResult(ticker="AAPL")
        r.failing_gate = "EDGE"
        r.decision = "NO_TRADE"
        assert r.decision == "NO_TRADE"
        assert r.failing_gate == "EDGE"

    def test_all_gates_passed(self):
        r = EvaluationResult(ticker="AAPL")
        r.decision = "TRADE"
        assert r.decision == "TRADE"


# ===========================================================================
# 2. compute_sustained_days — counts consecutive same-direction from today
# ===========================================================================

class TestComputeSustainedDays:
    """Counts streak of consecutive accumulation/distribution from most recent day."""

    def test_all_accumulation(self, flow_data_accumulation):
        daily = flow_data_accumulation["dark_pool"]["daily"]
        days = compute_sustained_days(daily, direction="ACCUMULATION")
        assert days == 6  # all 6 are accumulation

    def test_fading_signal(self, flow_data_fading):
        daily = flow_data_fading["dark_pool"]["daily"]
        days = compute_sustained_days(daily, direction="ACCUMULATION")
        assert days == 0  # most recent (Mar 5) is NEUTRAL → streak = 0

    def test_distribution_streak(self):
        daily = [
            {"date": "2026-03-05", "flow_direction": "DISTRIBUTION"},
            {"date": "2026-03-04", "flow_direction": "DISTRIBUTION"},
            {"date": "2026-03-03", "flow_direction": "ACCUMULATION"},
        ]
        assert compute_sustained_days(daily, direction="DISTRIBUTION") == 2

    def test_empty_daily(self):
        assert compute_sustained_days([], direction="ACCUMULATION") == 0

    def test_single_day_match(self):
        daily = [{"date": "2026-03-05", "flow_direction": "ACCUMULATION"}]
        assert compute_sustained_days(daily, direction="ACCUMULATION") == 1

    def test_single_day_no_match(self):
        daily = [{"date": "2026-03-05", "flow_direction": "NEUTRAL"}]
        assert compute_sustained_days(daily, direction="ACCUMULATION") == 0


# ===========================================================================
# 3. determine_edge — the Gate 2 logic
# ===========================================================================

class TestDetermineEdge:
    """Edge determination uses DP flow, options, OI, and price data."""

    def test_strong_accumulation_passes(
        self, flow_data_accumulation, options_data_bullish, oi_data_massive, price_history
    ):
        result = determine_edge(
            flow=flow_data_accumulation,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        assert result["passed"] is True
        assert result["sustained_days"] >= 3
        assert result["flow_strength"] >= 50

    def test_fading_signal_fails(
        self, flow_data_fading, options_data_bullish, oi_data_massive, price_history
    ):
        result = determine_edge(
            flow=flow_data_fading,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        assert result["passed"] is False
        assert result["sustained_days"] == 0
        assert "fad" in result["reason"].lower() or "sustained" in result["reason"].lower()

    def test_neutral_flow_fails(self, options_data_bullish, oi_data_massive, price_history):
        neutral_flow = {
            "dark_pool": {
                "aggregate": {"flow_strength": 5.0, "dp_buy_ratio": 0.51, "flow_direction": "NEUTRAL"},
                "daily": [
                    {"date": "2026-03-05", "flow_direction": "NEUTRAL", "dp_buy_ratio": 0.51, "flow_strength": 2.0},
                ],
            },
        }
        result = determine_edge(
            flow=neutral_flow,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        assert result["passed"] is False

    def test_high_recent_strength_passes_without_3_day_streak(
        self, options_data_bullish, oi_data_massive, price_history
    ):
        """Alternative criterion: if recent strength >70, don't need 3-day streak."""
        flow = {
            "dark_pool": {
                "aggregate": {"flow_strength": 55.0, "dp_buy_ratio": 0.80, "flow_direction": "ACCUMULATION"},
                "daily": [
                    {"date": "2026-03-05", "flow_direction": "ACCUMULATION", "dp_buy_ratio": 0.90, "flow_strength": 80.0},
                    {"date": "2026-03-04", "flow_direction": "DISTRIBUTION", "dp_buy_ratio": 0.30, "flow_strength": 40.0},
                    {"date": "2026-03-03", "flow_direction": "ACCUMULATION", "dp_buy_ratio": 0.85, "flow_strength": 70.0},
                ],
            },
        }
        result = determine_edge(
            flow=flow,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        # sustained_days = 1 (only Mar 5), but recent strength = 80 > 70
        assert result["passed"] is True
        assert result["sustained_days"] < 3

    def test_options_contradicting_reduces_confidence(
        self, flow_data_accumulation, oi_data_massive, price_history
    ):
        """Bearish options with bullish DP → still passes but lower confidence."""
        bearish_options = {
            "analysis": {
                "chain_bias": "BEARISH",
                "flow_bias": "BEARISH",
                "combined_bias": "BEARISH",
                "confidence": "HIGH",
            },
        }
        result = determine_edge(
            flow=flow_data_accumulation,
            options=bearish_options,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        # Strong DP + bearish options → still passes but flags conflict
        assert result["passed"] is True
        assert result.get("options_conflict") is True

    def test_signal_already_in_price_fails(
        self, flow_data_accumulation, options_data_bullish, oi_data_massive
    ):
        """If price already rallied during accumulation → signal priced in."""
        rising_prices = [
            {"date": "2026-02-27", "open": 250.00, "close": 260.00},
            {"date": "2026-03-02", "open": 260.00, "close": 270.00},
            {"date": "2026-03-03", "open": 270.00, "close": 278.00},
            {"date": "2026-03-04", "open": 278.00, "close": 285.00},
            {"date": "2026-03-05", "open": 285.00, "close": 290.00},
        ]
        result = determine_edge(
            flow=flow_data_accumulation,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=rising_prices,
        )
        assert result["passed"] is False
        assert "priced" in result["reason"].lower() or "price" in result["reason"].lower()


# ===========================================================================
# 4. run_evaluation — full orchestrator (mocked externals)
# ===========================================================================

class TestRunEvaluation:
    """The main orchestrator runs milestones in parallel, then sequential."""

    def test_m3_m3b_or_price_fetch_failure_stops_at_edge_gate(self):
        raw = {
            "M1": {"verified": True, "options_available": True},
            "M1B": {},
            "M1C": {},
            "M1D": {},
            "M2": {"trading_days_checked": [], "dark_pool": {}},
            "M3": {"error": "provider unavailable"},
            "M3B": [],
            "PRICE": [],
        }
        with patch("evaluate._run_parallel_milestones", return_value=raw):
            result = run_evaluation("SPY")

        assert result.decision == "NO_TRADE"
        assert result.failing_gate == "EDGE"
        assert result.milestones["M3"].passed is False
        assert "provider unavailable" in (result.milestones["M3"].error or "")

    def test_pending_structure_and_kelly_are_not_passed_or_success_exit(
        self, ticker_data, flow_data_accumulation, options_data_bullish,
        oi_data_massive, price_history,
    ):
        raw = {
            "M1": ticker_data,
            "M1B": {},
            "M1C": {},
            "M1D": {},
            "M2": flow_data_accumulation,
            "M3": options_data_bullish,
            "M3B": oi_data_massive,
            "PRICE": price_history,
        }
        with patch("evaluate._run_parallel_milestones", return_value=raw):
            result = run_evaluation("AAPL")

        assert result.decision == "PENDING"
        assert result.milestones["M5"].passed is False
        assert result.milestones["M6"].passed is False
        from evaluate import _decision_exit_code
        assert _decision_exit_code([result]) == 2

    @patch("evaluate.fetch_ticker_info")
    @patch("evaluate.fetch_flow")
    @patch("evaluate.fetch_options")
    @patch("evaluate.fetch_ticker_oi_changes")
    @patch("evaluate.fetch_analyst_ratings")
    @patch("evaluate.fetch_seasonality")
    @patch("evaluate.fetch_news")
    @patch("evaluate.fetch_price_history")
    def test_invalid_ticker_aborts_immediately(
        self, mock_price, mock_news, mock_season, mock_analyst, mock_oi,
        mock_options, mock_flow, mock_ticker
    ):
        mock_ticker.return_value = {
            "ticker": "ZZZZZ", "verified": False,
            "error": "Ticker not found", "options_available": False,
        }
        # Other mocks return empty/neutral to avoid errors
        mock_flow.return_value = {"dark_pool": {"aggregate": {}, "daily": []}}
        mock_options.return_value = {"analysis": {}}
        mock_oi.return_value = []
        mock_analyst.return_value = {}
        mock_season.return_value = {}
        mock_news.return_value = {}
        mock_price.return_value = []

        result = run_evaluation("ZZZZZ")
        assert result.decision == "NO_TRADE"
        assert result.failing_gate == "TICKER_VALIDATION"

    @patch("evaluate.fetch_ticker_info")
    @patch("evaluate.fetch_flow")
    @patch("evaluate.fetch_options")
    @patch("evaluate.fetch_ticker_oi_changes")
    @patch("evaluate.fetch_analyst_ratings")
    @patch("evaluate.fetch_seasonality")
    @patch("evaluate.fetch_news")
    @patch("evaluate.fetch_price_history")
    def test_fading_signal_stops_at_edge(
        self, mock_price, mock_news, mock_season, mock_analyst, mock_oi,
        mock_options, mock_flow, mock_ticker,
        ticker_data, flow_data_fading, options_data_bullish,
        oi_data_massive, analyst_data_bullish, price_history,
    ):
        mock_ticker.return_value = ticker_data
        mock_flow.return_value = flow_data_fading
        mock_options.return_value = options_data_bullish
        mock_oi.return_value = oi_data_massive
        mock_analyst.return_value = analyst_data_bullish
        mock_season.return_value = {"rating": "FAVORABLE", "win_rate": 65, "avg_return": 3.8}
        mock_news.return_value = {}
        mock_price.return_value = price_history

        result = run_evaluation("AAPL")
        assert result.decision == "NO_TRADE"
        assert result.failing_gate == "EDGE"
        # Milestones 1-3B should be complete
        assert "M1" in result.milestones
        assert "M2" in result.milestones
        assert "M3" in result.milestones
        assert "M3B" in result.milestones
        assert result.milestones["M1"].passed is True

    @patch("evaluate.fetch_ticker_info")
    @patch("evaluate.fetch_flow")
    @patch("evaluate.fetch_options")
    @patch("evaluate.fetch_ticker_oi_changes")
    @patch("evaluate.fetch_analyst_ratings")
    @patch("evaluate.fetch_seasonality")
    @patch("evaluate.fetch_news")
    @patch("evaluate.fetch_price_history")
    def test_strong_signal_reaches_structure(
        self, mock_price, mock_news, mock_season, mock_analyst, mock_oi,
        mock_options, mock_flow, mock_ticker,
        ticker_data, flow_data_accumulation, options_data_bullish,
        oi_data_massive, analyst_data_bullish, price_history,
    ):
        mock_ticker.return_value = ticker_data
        mock_flow.return_value = flow_data_accumulation
        mock_options.return_value = options_data_bullish
        mock_oi.return_value = oi_data_massive
        mock_analyst.return_value = analyst_data_bullish
        mock_season.return_value = {"rating": "FAVORABLE", "win_rate": 65, "avg_return": 3.8}
        mock_news.return_value = {}
        mock_price.return_value = price_history

        result = run_evaluation("AAPL")
        # Edge should pass with this strong data
        assert "M4" in result.milestones
        assert result.milestones["M4"].passed is True

    @patch("evaluate.fetch_ticker_info")
    @patch("evaluate.fetch_flow")
    @patch("evaluate.fetch_options")
    @patch("evaluate.fetch_ticker_oi_changes")
    @patch("evaluate.fetch_analyst_ratings")
    @patch("evaluate.fetch_seasonality")
    @patch("evaluate.fetch_news")
    @patch("evaluate.fetch_price_history")
    def test_no_options_aborts(
        self, mock_price, mock_news, mock_season, mock_analyst, mock_oi,
        mock_options, mock_flow, mock_ticker,
    ):
        mock_ticker.return_value = {
            "ticker": "XYZA", "verified": True, "company_name": "Test Corp",
            "options_available": False, "sector": "Test", "current_price": 50.0,
        }
        mock_flow.return_value = {"dark_pool": {"aggregate": {}, "daily": []}}
        mock_options.return_value = {"analysis": {}}
        mock_oi.return_value = []
        mock_analyst.return_value = {}
        mock_season.return_value = {}
        mock_news.return_value = {}
        mock_price.return_value = []

        result = run_evaluation("XYZA")
        assert result.decision == "NO_TRADE"
        assert result.failing_gate == "TICKER_VALIDATION"

    @patch("evaluate.datetime")
    @patch("evaluate.fetch_ticker_info")
    @patch("evaluate.fetch_flow")
    @patch("evaluate.fetch_options")
    @patch("evaluate.fetch_ticker_oi_changes")
    @patch("evaluate.fetch_analyst_ratings")
    @patch("evaluate.fetch_seasonality")
    @patch("evaluate.fetch_news")
    @patch("evaluate.fetch_price_history")
    def test_today_included_in_flow(
        self, mock_price, mock_news, mock_season, mock_analyst, mock_oi,
        mock_options, mock_flow, mock_ticker, mock_datetime,
        ticker_data, flow_data_accumulation, options_data_bullish,
        oi_data_massive, analyst_data_bullish, price_history,
    ):
        """Verify we check that today's date appears in the flow data."""
        # Mock datetime.now() to return the date in our test fixtures
        mock_datetime.now.return_value = datetime(2026, 3, 5, 11, 0, 0)

        mock_ticker.return_value = ticker_data
        mock_flow.return_value = flow_data_accumulation
        mock_options.return_value = options_data_bullish
        mock_oi.return_value = oi_data_massive
        mock_analyst.return_value = analyst_data_bullish
        mock_season.return_value = {"rating": "FAVORABLE"}
        mock_news.return_value = {}
        mock_price.return_value = price_history

        result = run_evaluation("AAPL")
        # The flow_data_accumulation has today (2026-03-05) in trading_days_checked
        m2 = result.milestones.get("M2")
        assert m2 is not None
        assert m2.data.get("includes_today") is True


# ===========================================================================
# 5. format_report — human-readable output
# ===========================================================================

class TestFormatReport:
    """format_report produces a structured text summary."""

    def test_no_trade_report_includes_failing_gate(self):
        r = EvaluationResult(ticker="AAPL")
        r.decision = "NO_TRADE"
        r.failing_gate = "EDGE"
        r.milestones["M1"] = MilestoneResult(
            name="ticker_validation", passed=True,
            data={"company_name": "Apple Inc.", "current_price": 258.26}
        )
        r.milestones["M4"] = MilestoneResult(
            name="edge_determination", passed=False,
            data={"reason": "Sustained days = 0", "sustained_days": 0}
        )
        report = format_report(r)
        assert "NO_TRADE" in report
        assert "EDGE" in report
        assert "Apple" in report

    def test_trade_report_includes_structure(self):
        r = EvaluationResult(ticker="GOOG")
        r.decision = "TRADE"
        r.milestones["M5"] = MilestoneResult(
            name="structure", passed=True,
            data={"structure_type": "Bull Call Spread", "rr_ratio": 3.0}
        )
        r.milestones["M6"] = MilestoneResult(
            name="kelly_sizing", passed=True,
            data={"position_pct": 2.48, "contracts": 44, "total_cost": 27544}
        )
        report = format_report(r)
        assert "TRADE" in report
        assert "Bull Call Spread" in report
        assert "3.0" in report  # R:R ratio

    def test_report_includes_data_freshness(self):
        r = EvaluationResult(ticker="AAPL")
        r.fetched_at = "2026-03-05 11:00 AM PT"
        r.decision = "NO_TRADE"
        r.failing_gate = "EDGE"
        report = format_report(r)
        assert "2026-03-05" in report
        assert "Data as of" in report or "📊" in report


# ===========================================================================
# 6. Edge cases and error handling
# ===========================================================================

class TestEdgeCases:
    """Error handling, missing data, partial failures."""

    def test_empty_oi_changes_does_not_crash(
        self, flow_data_fading, options_data_bullish, price_history
    ):
        result = determine_edge(
            flow=flow_data_fading,
            options=options_data_bullish,
            oi_changes=[],
            price_history=price_history,
        )
        assert isinstance(result, dict)
        assert "passed" in result

    def test_missing_daily_flow_does_not_crash(
        self, options_data_bullish, price_history
    ):
        flow = {"dark_pool": {"aggregate": {"flow_strength": 0, "dp_buy_ratio": 0.5, "flow_direction": "NEUTRAL"}, "daily": []}}
        result = determine_edge(
            flow=flow, options=options_data_bullish,
            oi_changes=[], price_history=price_history,
        )
        assert result["passed"] is False
        assert result["sustained_days"] == 0

    def test_none_options_handled(
        self, flow_data_accumulation, oi_data_massive, price_history
    ):
        """If options fetch failed entirely, edge can still be determined from DP alone."""
        result = determine_edge(
            flow=flow_data_accumulation,
            options=None,
            oi_changes=oi_data_massive,
            price_history=price_history,
        )
        assert isinstance(result, dict)
        assert "passed" in result

    def test_empty_price_history_still_works(
        self, flow_data_accumulation, options_data_bullish, oi_data_massive
    ):
        result = determine_edge(
            flow=flow_data_accumulation,
            options=options_data_bullish,
            oi_changes=oi_data_massive,
            price_history=[],
        )
        assert isinstance(result, dict)
        assert "passed" in result


# ===========================================================================
# 7. OI categorization
# ===========================================================================

class TestOICategorization:
    """OI changes are categorized by premium size."""

    def test_massive_oi_flagged(self, oi_data_massive):
        from evaluate import categorize_oi_signals
        cats = categorize_oi_signals(oi_data_massive)
        assert cats["massive_count"] >= 1
        assert cats["total_premium"] > 10_000_000

    def test_empty_oi(self):
        from evaluate import categorize_oi_signals
        cats = categorize_oi_signals([])
        assert cats["massive_count"] == 0
        assert cats["total_premium"] == 0


# ===========================================================================
# 8. Seasonality parsing
# ===========================================================================

class TestSeasonality:
    """Seasonality is context, not a gate — but needs to be parsed."""

    def test_favorable_rating(self):
        from evaluate import rate_seasonality
        assert rate_seasonality(65, 5.5) == "FAVORABLE"

    def test_neutral_rating(self):
        from evaluate import rate_seasonality
        assert rate_seasonality(55, 2.0) == "NEUTRAL"

    def test_unfavorable_rating(self):
        from evaluate import rate_seasonality
        assert rate_seasonality(40, -1.0) == "UNFAVORABLE"

    def test_edge_case_boundary(self):
        from evaluate import rate_seasonality
        # 60% and 5% exactly → boundary check
        assert rate_seasonality(60, 5.0) in ("FAVORABLE", "NEUTRAL")

"""Tests for portfolio_attribution.py — M1 through M7."""

import sys
from pathlib import Path
from unittest.mock import patch

# Ensure scripts/ is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portfolio_attribution import (
    classify_trade,
    classify_edge_type,
    build_attribution,
    _is_closed,
    _get_pnl,
    _get_risk_type,
    load_and_build,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

STRATEGY_NAMES = {
    "dark-pool-flow": "Dark Pool Flow",
    "leap-iv-mispricing": "LEAP IV Mispricing",
    "garch-convergence": "GARCH Convergence Spreads",
    "risk-reversal": "Risk Reversal",
    "vcg": "Volatility-Credit Gap (VCG)",
    "cri": "Crash Risk Index (CRI)",
}


def _make_trade(**kwargs):
    """Helper to build a trade dict with defaults."""
    base = {
        "id": 1,
        "date": "2026-03-01",
        "ticker": "TEST",
        "structure": "Long Call",
        "decision": "EXECUTED",
        "action": "TRADE",
    }
    base.update(kwargs)
    return base


# ── M1: Strategy Classifier ─────────────────────────────────────────────


class TestClassify:
    """M1: classify_trade maps trades to strategy IDs."""

    def test_classify_dark_pool_by_edge_type(self):
        trade = _make_trade(edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"})
        assert classify_trade(trade) == "dark-pool-flow"

    def test_classify_dark_pool_by_gate_and_strength(self):
        trade = _make_trade(
            edge_analysis={"dp_strength": 75},
            gates_passed=["EDGE (94.87% buy ratio, 5-day sustained, 89.7 strength)"],
        )
        assert classify_trade(trade) == "dark-pool-flow"

    def test_classify_leap_iv_by_edge_type(self):
        trade = _make_trade(edge_analysis={"edge_type": "IV_MISPRICING"})
        assert classify_trade(trade) == "leap-iv-mispricing"

    def test_classify_leap_iv_by_structure(self):
        trade = _make_trade(structure="Long Call - LEAP")
        assert classify_trade(trade) == "leap-iv-mispricing"

    def test_classify_iv_mispricing_plus_flow(self):
        """IV_MISPRICING + FLOW_CONFLUENCE → leap-iv-mispricing (primary edge)."""
        trade = _make_trade(edge_analysis={"edge_type": "IV_MISPRICING + FLOW_CONFLUENCE"})
        assert classify_trade(trade) == "leap-iv-mispricing"

    def test_classify_garch(self):
        trade = _make_trade(edge_analysis={"edge_type": "GARCH_CONVERGENCE"})
        assert classify_trade(trade) == "garch-convergence"

    def test_classify_risk_reversal_by_structure(self):
        trade = _make_trade(structure="Risk Reversal P$100/C$115")
        assert classify_trade(trade) == "risk-reversal"

    def test_classify_risk_reversal_by_undefined_legs(self):
        trade = _make_trade(
            risk_profile="UNDEFINED",
            legs=[
                {"type": "Short Put", "strike": 100},
                {"type": "Long Call", "strike": 115},
            ],
        )
        assert classify_trade(trade) == "risk-reversal"

    def test_classify_vcg(self):
        trade = _make_trade(edge_analysis={"edge_type": "VCG_DIVERGENCE"})
        assert classify_trade(trade) == "vcg"

    def test_classify_cri_by_edge(self):
        trade = _make_trade(edge_analysis={"edge_type": "CRI_SIGNAL"})
        assert classify_trade(trade) == "cri"

    def test_classify_cri_spxu_put(self):
        trade = _make_trade(ticker="SPXU", structure="Long Put Spread")
        assert classify_trade(trade) == "cri"

    def test_classify_unclassified_equity(self):
        trade = _make_trade(structure="Closed Stock (STK)", decision="IB_AUTO_IMPORT")
        assert classify_trade(trade) == "unclassified"

    def test_classify_unclassified_no_metadata(self):
        trade = _make_trade(structure="Long Option (OPT)", decision="IB_AUTO_IMPORT")
        assert classify_trade(trade) == "unclassified"

    def test_classify_real_goog_trade(self):
        """Real GOOG trade from trade_log — should be dark-pool-flow."""
        trade = _make_trade(
            ticker="GOOG",
            structure="Bull Call Spread",
            edge_analysis={
                "edge_type": "DARK_POOL_ACCUMULATION",
                "dp_flow": "EXTRAORDINARY_ACCUMULATION",
                "dp_strength": 89.7,
            },
            gates_passed=[
                "CONVEXITY (3.0:1 R:R)",
                "EDGE (94.87% buy ratio, 5-day sustained, 89.7 strength)",
                "RISK_MGMT (2.46% of bankroll, within 2.5% cap)",
            ],
        )
        assert classify_trade(trade) == "dark-pool-flow"

    def test_classify_real_alab_trade(self):
        """Real ALAB trade — LEAP with IV_MISPRICING edge."""
        trade = _make_trade(
            ticker="ALAB",
            structure="Long Call - LEAP",
            edge_analysis={"edge_type": "IV_MISPRICING"},
        )
        assert classify_trade(trade) == "leap-iv-mispricing"

    def test_classify_real_oxy_trade(self):
        """Real OXY trade — thesis trade, no DP signal."""
        trade = _make_trade(
            ticker="OXY",
            structure="Bear Put Spread",
            edge_analysis={"type": "thesis_trade"},
        )
        assert classify_trade(trade) == "unclassified"


# ── M2: Strategy P&L ────────────────────────────────────────────────────


class TestStrategyPnl:
    """M2: Realized P&L aggregated per strategy."""

    def test_strategy_pnl_single_closed(self):
        trades = [
            _make_trade(
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                realized_pnl=5000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        dp = next(s for s in result["by_strategy"] if s["strategy_id"] == "dark-pool-flow")
        assert dp["realized_pnl"] == 5000.0

    def test_strategy_pnl_multiple_closed(self):
        trades = [
            _make_trade(id=1, edge_analysis={"edge_type": "IV_MISPRICING"}, realized_pnl=3000.0, decision="CLOSED"),
            _make_trade(id=2, edge_analysis={"edge_type": "IV_MISPRICING"}, realized_pnl=-1000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        leap = next(s for s in result["by_strategy"] if s["strategy_id"] == "leap-iv-mispricing")
        assert leap["realized_pnl"] == 2000.0

    def test_strategy_pnl_open_not_counted(self):
        trades = [
            _make_trade(
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                realized_pnl=None,
                decision="EXECUTED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        dp = next(s for s in result["by_strategy"] if s["strategy_id"] == "dark-pool-flow")
        assert dp["realized_pnl"] == 0.0
        assert dp["open_count"] == 1
        assert dp["closed_count"] == 0

    def test_strategy_pnl_total(self):
        trades = [
            _make_trade(id=1, edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"}, realized_pnl=10000.0, decision="CLOSED"),
            _make_trade(id=2, structure="Risk Reversal", realized_pnl=-2000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        assert result["total_realized_pnl"] == 8000.0


# ── M3: Strategy Win Rate ───────────────────────────────────────────────


class TestStrategyWinRate:
    """M3: Win/loss count and hit rate per strategy."""

    def test_strategy_win_rate_all_winners(self):
        trades = [
            _make_trade(id=1, edge_analysis={"edge_type": "IV_MISPRICING"}, realized_pnl=5000.0, decision="CLOSED"),
            _make_trade(id=2, edge_analysis={"edge_type": "IV_MISPRICING"}, realized_pnl=3000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        leap = next(s for s in result["by_strategy"] if s["strategy_id"] == "leap-iv-mispricing")
        assert leap["win_rate"] == 1.0
        assert leap["winners"] == 2
        assert leap["losers"] == 0

    def test_strategy_win_rate_mixed(self):
        trades = [
            _make_trade(id=1, structure="Risk Reversal", realized_pnl=5000.0, decision="CLOSED"),
            _make_trade(id=2, structure="Risk Reversal", realized_pnl=-2000.0, decision="CLOSED"),
            _make_trade(id=3, structure="Risk Reversal", realized_pnl=1000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        rr = next(s for s in result["by_strategy"] if s["strategy_id"] == "risk-reversal")
        assert abs(rr["win_rate"] - 2/3) < 0.001
        assert rr["winners"] == 2
        assert rr["losers"] == 1

    def test_strategy_win_rate_none_when_no_closed(self):
        trades = [
            _make_trade(edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"}, decision="EXECUTED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        dp = next(s for s in result["by_strategy"] if s["strategy_id"] == "dark-pool-flow")
        assert dp["win_rate"] is None

    def test_strategy_win_rate_avg_win_loss(self):
        trades = [
            _make_trade(id=1, structure="Risk Reversal", realized_pnl=6000.0, decision="CLOSED"),
            _make_trade(id=2, structure="Risk Reversal", realized_pnl=4000.0, decision="CLOSED"),
            _make_trade(id=3, structure="Risk Reversal", realized_pnl=-3000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        rr = next(s for s in result["by_strategy"] if s["strategy_id"] == "risk-reversal")
        assert rr["avg_win"] == 5000.0
        assert rr["avg_loss"] == -3000.0


# ── M4: Kelly Calibration ───────────────────────────────────────────────


class TestKellyCalibration:
    """M4: Predicted vs actual win rate per strategy."""

    def test_kelly_calibration_perfect(self):
        """When predicted matches actual → accuracy = 1.0."""
        trades = [
            _make_trade(
                id=1,
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                kelly_calculation={"p_itm_estimate": 0.50},
                realized_pnl=5000.0,
                decision="CLOSED",
            ),
            _make_trade(
                id=2,
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                kelly_calculation={"p_itm_estimate": 0.50},
                realized_pnl=-2000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        cal = result["kelly_calibration"].get("dark-pool-flow")
        assert cal is not None
        assert cal["expected_win_rate"] == 0.5
        assert cal["actual_win_rate"] == 0.5
        assert cal["accuracy"] == 1.0

    def test_kelly_calibration_overestimate(self):
        """When predicted > actual → accuracy < 1.0."""
        trades = [
            _make_trade(
                id=1,
                edge_analysis={"edge_type": "IV_MISPRICING"},
                kelly_calculation={"p_itm_estimate": 0.80},
                realized_pnl=-1000.0,
                decision="CLOSED",
            ),
            _make_trade(
                id=2,
                edge_analysis={"edge_type": "IV_MISPRICING"},
                kelly_calculation={"p_itm_estimate": 0.80},
                realized_pnl=-500.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        cal = result["kelly_calibration"]["leap-iv-mispricing"]
        assert cal["expected_win_rate"] == 0.8
        assert cal["actual_win_rate"] == 0.0
        assert cal["accuracy"] == 0.0  # Clamped to 0

    def test_kelly_calibration_uses_probability_fallback(self):
        """When kelly_calculation uses 'probability' instead of 'p_itm_estimate'."""
        trades = [
            _make_trade(
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                kelly_calculation={"probability": 0.35},
                realized_pnl=5000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        cal = result["kelly_calibration"].get("dark-pool-flow")
        assert cal is not None
        assert cal["expected_win_rate"] == 0.35

    def test_kelly_calibration_no_kelly_data(self):
        """Trades without kelly_calculation → not in calibration dict."""
        trades = [
            _make_trade(
                structure="Closed Stock (STK)",
                realized_pnl=500.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        assert "unclassified" not in result["kelly_calibration"]


# ── M5: Ticker Attribution ──────────────────────────────────────────────


class TestTickerAttribution:
    """M5: P&L by ticker with best/worst."""

    def test_ticker_attribution_basic(self):
        trades = [
            _make_trade(id=1, ticker="AAPL", realized_pnl=5000.0, decision="CLOSED"),
            _make_trade(id=2, ticker="GOOG", realized_pnl=-2000.0, decision="CLOSED"),
            _make_trade(id=3, ticker="AAPL", realized_pnl=1000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        aapl = next(t for t in result["by_ticker"] if t["ticker"] == "AAPL")
        goog = next(t for t in result["by_ticker"] if t["ticker"] == "GOOG")
        assert aapl["realized_pnl"] == 6000.0
        assert aapl["trade_count"] == 2
        assert goog["realized_pnl"] == -2000.0
        assert result["best_ticker"] == "AAPL"
        assert result["worst_ticker"] == "GOOG"

    def test_ticker_attribution_sorted_by_pnl(self):
        trades = [
            _make_trade(id=1, ticker="A", realized_pnl=-100.0, decision="CLOSED"),
            _make_trade(id=2, ticker="B", realized_pnl=500.0, decision="CLOSED"),
            _make_trade(id=3, ticker="C", realized_pnl=200.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        tickers = [t["ticker"] for t in result["by_ticker"]]
        assert tickers == ["B", "C", "A"]

    def test_ticker_attribution_includes_strategies(self):
        trades = [
            _make_trade(
                ticker="NVDA",
                edge_analysis={"edge_type": "IV_MISPRICING"},
                realized_pnl=1000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        nvda = next(t for t in result["by_ticker"] if t["ticker"] == "NVDA")
        assert "leap-iv-mispricing" in nvda["strategies"]


# ── M6: Edge Quality ────────────────────────────────────────────────────


class TestEdgeQuality:
    """M6: P&L split by edge type."""

    def test_edge_quality_dark_pool(self):
        trades = [
            _make_trade(
                edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"},
                realized_pnl=10000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        dp = next(e for e in result["by_edge"] if e["edge_type"] == "dark_pool")
        assert dp["realized_pnl"] == 10000.0
        assert dp["win_rate"] == 1.0

    def test_edge_quality_thesis(self):
        trades = [
            _make_trade(
                edge_analysis={"type": "thesis_trade"},
                realized_pnl=-5000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        thesis = next(e for e in result["by_edge"] if e["edge_type"] == "thesis")
        assert thesis["realized_pnl"] == -5000.0

    def test_edge_quality_none(self):
        trades = [
            _make_trade(
                structure="Closed Stock (STK)",
                realized_pnl=500.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        none_edge = next(e for e in result["by_edge"] if e["edge_type"] == "none")
        assert none_edge["realized_pnl"] == 500.0

    def test_edge_quality_multiple_types(self):
        trades = [
            _make_trade(id=1, edge_analysis={"edge_type": "DARK_POOL_ACCUMULATION"}, realized_pnl=8000.0, decision="CLOSED"),
            _make_trade(id=2, edge_analysis={"edge_type": "IV_MISPRICING"}, realized_pnl=3000.0, decision="CLOSED"),
            _make_trade(id=3, structure="Stock", realized_pnl=-1000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        edge_types = {e["edge_type"] for e in result["by_edge"]}
        assert "dark_pool" in edge_types
        assert "iv_mispricing" in edge_types
        assert "none" in edge_types


# ── M7: Risk Profile ────────────────────────────────────────────────────


class TestRiskProfile:
    """M7: P&L split by defined vs undefined risk."""

    def test_risk_profile_defined(self):
        trades = [
            _make_trade(
                risk_profile="DEFINED",
                realized_pnl=5000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        defined = next(r for r in result["by_risk"] if r["risk_type"] == "defined")
        assert defined["realized_pnl"] == 5000.0

    def test_risk_profile_undefined(self):
        trades = [
            _make_trade(
                risk_profile="UNDEFINED",
                structure="Risk Reversal",
                realized_pnl=9000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        undef = next(r for r in result["by_risk"] if r["risk_type"] == "undefined")
        assert undef["realized_pnl"] == 9000.0

    def test_risk_profile_equity(self):
        trades = [
            _make_trade(
                risk_profile="EQUITY",
                structure="Long Stock",
                realized_pnl=-2000.0,
                decision="CLOSED",
            ),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        eq = next(r for r in result["by_risk"] if r["risk_type"] == "equity")
        assert eq["realized_pnl"] == -2000.0

    def test_risk_profile_inferred_from_structure(self):
        """When risk_profile is not set, infer from structure."""
        trades = [
            _make_trade(structure="Bull Call Spread", realized_pnl=3000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        defined = next(r for r in result["by_risk"] if r["risk_type"] == "defined")
        assert defined["realized_pnl"] == 3000.0

    def test_risk_profile_win_rate(self):
        trades = [
            _make_trade(id=1, risk_profile="DEFINED", realized_pnl=5000.0, decision="CLOSED"),
            _make_trade(id=2, risk_profile="DEFINED", realized_pnl=-1000.0, decision="CLOSED"),
            _make_trade(id=3, risk_profile="DEFINED", realized_pnl=2000.0, decision="CLOSED"),
        ]
        result = build_attribution(trades, STRATEGY_NAMES)
        defined = next(r for r in result["by_risk"] if r["risk_type"] == "defined")
        assert abs(defined["win_rate"] - 2/3) < 0.001


# ── Integration with journal reader ─────────────────────────────────────


class TestRealData:
    """Integration test using journal-shaped rows."""

    def test_load_and_build_reads_journal_rows(self):
        """Verify attribution works with journal rows from the DB reader."""
        trades = [
            _make_trade(id=1, ticker="AAPL", realized_pnl=1000.0, decision="CLOSED"),
            _make_trade(id=2, ticker="MSFT", realized_pnl=-250.0, decision="CLOSED"),
        ]
        with patch("db.readers.read_journal_trades", return_value=trades) as reader:
            result = load_and_build()

        reader.assert_called_once_with()
        assert result["total_trades"] == 2
        assert result["closed_trades"] == 2
        assert isinstance(result["by_strategy"], list)
        assert isinstance(result["by_ticker"], list)
        assert isinstance(result["by_edge"], list)
        assert isinstance(result["by_risk"], list)
        assert result["best_ticker"] is not None
        assert result["worst_ticker"] is not None

        # Every trade should be classified
        strategy_count = sum(s["trade_count"] for s in result["by_strategy"])
        assert strategy_count == result["total_trades"]

        # P&L should be consistent
        strategy_pnl = sum(s["realized_pnl"] for s in result["by_strategy"])
        assert abs(strategy_pnl - result["total_realized_pnl"]) < 0.01

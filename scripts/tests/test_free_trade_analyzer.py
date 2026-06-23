#!/usr/bin/env python3
"""
Tests for free_trade_analyzer.py
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import free_trade_analyzer as free_trade_module
from free_trade_analyzer import (
    Leg,
    FreeTradeSuggestion,
    classify_position,
    get_startup_summary,
    load_portfolio,
    PositionAnalysis,
)


class TestLeg:
    """Test Leg dataclass calculations."""
    
    def test_long_call_entry_cost(self):
        """Long call entry cost is positive (paid premium)."""
        leg = Leg(
            direction="LONG",
            leg_type="Call",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=3.00,
        )
        # Paid $2.50 * 10 * 100 = $2,500
        assert leg.entry_cost == 2500.0
    
    def test_short_put_entry_cost(self):
        """Short put entry cost is negative (received premium)."""
        leg = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=1.00,
        )
        # Received $2.50 * 10 * 100 = -$2,500
        assert leg.entry_cost == -2500.0
    
    def test_long_call_pnl(self):
        """Long call P&L calculation."""
        leg = Leg(
            direction="LONG",
            leg_type="Call",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=4.00,
        )
        # Current value: 4.00 * 10 * 100 = $4,000
        # Entry cost: $2,500
        # P&L: $4,000 - $2,500 = $1,500
        assert leg.pnl == 1500.0
    
    def test_short_put_pnl(self):
        """Short put P&L calculation (profit when price drops)."""
        leg = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=1.00,
        )
        # Entry cost: -$2,500 (received)
        # Current value: -$1,000 (would pay to close)
        # P&L: -$1,000 - (-$2,500) = $1,500 profit
        assert leg.pnl == 1500.0
    
    def test_close_cost_long(self):
        """Long position close cost (receive premium)."""
        leg = Leg(
            direction="LONG",
            leg_type="Call",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=4.00,
        )
        # Sell to close: receive $4.00 * 10 * 100 = -$4,000 (negative = receive)
        assert leg.close_cost == -4000.0
    
    def test_close_cost_short(self):
        """Short position close cost (pay premium)."""
        leg = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=100,
            contracts=10,
            entry_price=2.50,
            current_price=1.00,
        )
        # Buy to close: pay $1.00 * 10 * 100 = $1,000
        assert leg.close_cost == 1000.0


class TestFreeTradeSuggestion:
    """Test free trade calculations."""
    
    def test_risk_reversal_not_free(self):
        """Risk reversal that's not yet free."""
        core = Leg(
            direction="LONG",
            leg_type="Call",
            strike=138,
            contracts=25,
            entry_price=2.81,
            current_price=3.15,
        )
        hedge = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=128,
            contracts=25,
            entry_price=2.81,
            current_price=1.00,
        )
        
        suggestion = FreeTradeSuggestion(core_leg=core, hedge_leg=hedge)
        
        # Core cost: $2.81 * 25 * 100 = $7,025
        assert suggestion.core_entry_cost == pytest.approx(7025, rel=0.01)
        
        # Hedge P&L: ($2.81 - $1.00) * 25 * 100 = $4,525
        assert suggestion.hedge_current_pnl == pytest.approx(4525, rel=0.01)
        
        # Effective cost: $7,025 - $4,525 = $2,500
        assert suggestion.effective_core_cost == pytest.approx(2500, rel=0.01)
        
        # Not free yet
        assert suggestion.is_free == False
        
        # ~64% to free
        assert suggestion.pct_to_free == pytest.approx(64.4, rel=0.05)
    
    def test_risk_reversal_is_free(self):
        """Risk reversal where hedge profit exceeds core cost."""
        core = Leg(
            direction="LONG",
            leg_type="Call",
            strike=138,
            contracts=25,
            entry_price=2.81,
            current_price=5.00,
        )
        hedge = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=128,
            contracts=25,
            entry_price=2.81,
            current_price=0.10,  # Nearly worthless
        )
        
        suggestion = FreeTradeSuggestion(core_leg=core, hedge_leg=hedge)
        
        # Hedge P&L: ($2.81 - $0.10) * 25 * 100 = $6,775
        # Core cost: $7,025
        # Still not quite free, but close
        
        # Let's make it truly free
        hedge.entry_price = 3.50  # Sold higher
        suggestion = FreeTradeSuggestion(core_leg=core, hedge_leg=hedge)
        
        # Hedge P&L: ($3.50 - $0.10) * 25 * 100 = $8,500
        # Core cost: $7,025
        # Effective: $7,025 - $8,500 = -$1,475 (negative = free + profit)
        assert suggestion.is_free == True
        assert suggestion.pct_to_free == 100.0
    
    def test_breakeven_close_price(self):
        """Calculate breakeven close price for hedge."""
        core = Leg(
            direction="LONG",
            leg_type="Call",
            strike=100,
            contracts=10,
            entry_price=5.00,
            current_price=6.00,
        )
        hedge = Leg(
            direction="SHORT",
            leg_type="Put",
            strike=90,
            contracts=10,
            entry_price=3.00,
            current_price=2.00,
        )
        
        suggestion = FreeTradeSuggestion(core_leg=core, hedge_leg=hedge)
        
        # Core cost: $5.00 * 10 * 100 = $5,000
        # To make free, hedge profit must = $5,000
        # Profit = (entry - close) * 10 * 100 = $5,000
        # 3.00 - close = 5.00
        # close = -2.00 → clamped to 0
        assert suggestion.breakeven_close_price == 0.0


class TestClassifyPosition:
    """Test position classification."""
    
    def test_bullish_risk_reversal(self):
        """Long call + short put = bullish risk reversal."""
        call = Leg("LONG", "Call", 100, 10, 2.0, 3.0)
        put = Leg("SHORT", "Put", 90, 10, 1.5, 1.0)
        
        structure, core, hedge = classify_position([call, put])
        
        assert structure == "risk_reversal_bullish"
        assert core == call
        assert hedge == put
    
    def test_bearish_risk_reversal(self):
        """Long put + short call = bearish risk reversal."""
        call = Leg("SHORT", "Call", 110, 10, 2.0, 1.5)
        put = Leg("LONG", "Put", 100, 10, 3.0, 4.0)
        
        structure, core, hedge = classify_position([put, call])
        
        assert structure == "risk_reversal_bearish"
        assert core == put
        assert hedge == call
    
    def test_bull_call_spread(self):
        """Long lower strike call + short higher strike call."""
        long_call = Leg("LONG", "Call", 100, 10, 5.0, 6.0)
        short_call = Leg("SHORT", "Call", 110, 10, 2.0, 1.5)
        
        structure, core, hedge = classify_position([long_call, short_call])
        
        assert structure == "bull_call_spread"
        assert core == long_call
        assert hedge == short_call
    
    def test_bear_put_spread(self):
        """Long higher strike put + short lower strike put."""
        long_put = Leg("LONG", "Put", 100, 10, 5.0, 6.0)
        short_put = Leg("SHORT", "Put", 90, 10, 2.0, 1.0)
        
        structure, core, hedge = classify_position([long_put, short_put])
        
        assert structure == "bear_put_spread"
        assert core == long_put
        assert hedge == short_put
    
    def test_single_leg_no_analysis(self):
        """Single leg positions don't have free trade analysis."""
        call = Leg("LONG", "Call", 100, 10, 2.0, 3.0)
        
        structure, core, hedge = classify_position([call])
        
        assert structure == "single_leg"
        assert core == call
        assert hedge is None


class TestPortfolioLoading:
    """Test live-vs-cache portfolio loading."""

    def test_load_portfolio_syncs_from_ib_before_reading_cache(self, monkeypatch, tmp_path):
        """Default portfolio loading must refresh from IB before reading DB snapshot."""

        calls = []

        def fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
            calls.append(cmd)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        monkeypatch.setattr(free_trade_module.subprocess, "run", fake_run)

        with patch("db.readers.read_latest_portfolio_snapshot", return_value={"positions": [{"ticker": "MSFT"}]}):
            with patch("db.readers.read_portfolio_positions", return_value=[{"ticker": "MSFT"}]):
                positions = load_portfolio()

        assert [p["ticker"] for p in positions] == ["MSFT"]
        assert calls == [[
            sys.executable,
            str(free_trade_module.SCRIPT_DIR / "ib_sync.py"),
            "--sync",
            "--skip-audit",
        ]]

    def test_load_portfolio_ib_failure_fails_closed_instead_of_using_stale_cache(self, monkeypatch, tmp_path):
        """If IB sync fails, do not keep showing stale cached positions as if they were current."""
        def fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
            class Result:
                returncode = 1
                stderr = "gateway down"

            return Result()

        monkeypatch.setattr(free_trade_module.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="Cannot verify current portfolio via IB"):
            load_portfolio()

    def test_load_portfolio_cache_mode_skips_ib_sync(self, monkeypatch, tmp_path):
        """Explicit cache mode keeps the old offline behavior for manual use."""
        def fake_run(*args, **kwargs):
            raise AssertionError("IB sync should not run in cache mode")

        monkeypatch.setattr(free_trade_module.subprocess, "run", fake_run)

        with patch("db.readers.read_portfolio_positions", return_value=[{"ticker": "AAOI"}]) as reader:
            positions = load_portfolio(source="cache")

        reader.assert_called_once_with()
        assert [p["ticker"] for p in positions] == ["AAOI"]

    def test_load_portfolio_ib_sync_requires_db_snapshot(self, monkeypatch):
        def fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
            class Result:
                returncode = 0
                stderr = ""

            return Result()

        monkeypatch.setattr(free_trade_module.subprocess, "run", fake_run)

        with patch("db.readers.read_latest_portfolio_snapshot", return_value=None):
            with pytest.raises(RuntimeError, match="sync produced no DB snapshot"):
                load_portfolio()


class TestStartupSummary:
    """Test startup notification summary."""
    
    def test_no_opportunities(self):
        """No summary when no opportunities."""
        analyses = [
            PositionAnalysis(
                ticker="TEST",
                structure="Test",
                structure_type="test",
                expiry="2026-01-01",
                contracts=10,
                legs=[],
                best_opportunity_pct=30,  # Below threshold
            )
        ]
        
        summary = get_startup_summary(analyses)
        assert summary is None
    
    def test_near_free_opportunity(self):
        """Summary shows near-free positions."""
        analyses = [
            PositionAnalysis(
                ticker="EWY",
                structure="Risk Reversal",
                structure_type="risk_reversal_bullish",
                expiry="2026-01-01",
                contracts=10,
                legs=[],
                best_opportunity_pct=62,
            )
        ]
        
        summary = get_startup_summary(analyses, threshold=50)
        assert "EWY" in summary
        assert "62%" in summary
    
    def test_free_position(self):
        """Summary shows free positions."""
        # Create a mock suggestion that is free
        core = Leg("LONG", "Call", 100, 10, 2.0, 3.0)
        hedge = Leg("SHORT", "Put", 90, 10, 3.0, 0.1)
        suggestion = FreeTradeSuggestion(core_leg=core, hedge_leg=hedge)
        
        analyses = [
            PositionAnalysis(
                ticker="FREE",
                structure="Risk Reversal",
                structure_type="risk_reversal_bullish",
                expiry="2026-01-01",
                contracts=10,
                legs=[],
                suggestions=[suggestion],
                best_opportunity_pct=100,
            )
        ]
        
        summary = get_startup_summary(analyses)
        assert "🎉 FREE: FREE" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

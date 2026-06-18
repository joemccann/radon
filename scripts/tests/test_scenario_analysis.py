"""Tests for scenario_analysis.py — portfolio stress testing."""
import pytest
import math
from datetime import datetime, timedelta

from scenario_analysis import (
    approx_delta,
    compute_position_delta,
    compute_exposure,
    scenario_price_shock,
    scenario_delta_decay,
)

# Date-relative expiry for the delta tests, which need a real positive DTE.
# A hardcoded date is a time bomb: `_option_dte` truncates `(expiry - now).days`
# to 0 once the date is <1 day out, so `approx_delta` falls back to a flat ±0.5
# and a bull call spread's two call legs cancel to exactly 0.0 — the 2026-06-19
# breakage that detonated on 2026-06-18. ~90 days keeps DTE comfortably positive
# regardless of run date so the BS approximation (not the floor) is exercised.
_FAR_EXPIRY = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")


# ── Fixtures ────────────────────────────────────────────

def _make_stock(ticker="AAPL", qty=100, direction="LONG", entry_cost=25000, market_value=25000):
    return {
        "id": 1,
        "ticker": ticker,
        "structure": "Stock",
        "structure_type": "Stock",
        "expiry": "N/A",
        "contracts": qty,
        "direction": direction,
        "entry_cost": entry_cost,
        "market_value": market_value,
        "legs": [
            {"direction": direction, "contracts": qty, "type": "Stock", "strike": None,
             "market_price": market_value / qty if qty else 0, "market_value": market_value}
        ],
    }


def _make_long_call(ticker="AAPL", strike=250.0, contracts=10, expiry="2026-06-19",
                    market_price=8.0, entry_cost=6000):
    mv = market_price * contracts * 100
    return {
        "id": 2,
        "ticker": ticker,
        "structure": "Long Call",
        "structure_type": "Long Call",
        "expiry": expiry,
        "contracts": contracts,
        "direction": "LONG",
        "entry_cost": entry_cost,
        "market_value": mv,
        "legs": [
            {"direction": "LONG", "contracts": contracts, "type": "Call", "strike": strike,
             "market_price": market_price, "market_value": mv}
        ],
    }


def _make_long_put(ticker="AAPL", strike=230.0, contracts=5, expiry="2026-06-19",
                   market_price=4.0, entry_cost=2500):
    mv = market_price * contracts * 100
    return {
        "id": 3,
        "ticker": ticker,
        "structure": "Long Put",
        "structure_type": "Long Put",
        "expiry": expiry,
        "contracts": contracts,
        "direction": "LONG",
        "entry_cost": entry_cost,
        "market_value": mv,
        "legs": [
            {"direction": "LONG", "contracts": contracts, "type": "Put", "strike": strike,
             "market_price": market_price, "market_value": mv}
        ],
    }


def _make_bull_call_spread(ticker="AAPL", long_strike=250.0, short_strike=270.0,
                           contracts=10, expiry="2026-06-19",
                           long_mp=8.0, short_mp=3.0, entry_cost=5000):
    return {
        "id": 4,
        "ticker": ticker,
        "structure": f"Bull Call Spread ${long_strike}/${short_strike}",
        "structure_type": "Bull Call Spread",
        "expiry": expiry,
        "contracts": contracts,
        "direction": "DEBIT",
        "entry_cost": entry_cost,
        "market_value": (long_mp - short_mp) * contracts * 100,
        "legs": [
            {"direction": "LONG", "contracts": contracts, "type": "Call", "strike": long_strike,
             "market_price": long_mp, "market_value": long_mp * contracts * 100},
            {"direction": "SHORT", "contracts": contracts, "type": "Call", "strike": short_strike,
             "market_price": short_mp, "market_value": short_mp * contracts * 100},
        ],
    }


def _portfolio(positions, bankroll=100000):
    return {"bankroll": bankroll, "positions": positions}


# ── approx_delta ────────────────────────────────────────

class TestApproxDelta:
    def test_call_deep_itm(self):
        """Deep ITM call → delta near 1.0."""
        d = approx_delta(spot=300, strike=200, dte=30, opt_type="Call")
        assert d > 0.9

    def test_call_atm(self):
        """ATM call → delta near 0.5."""
        d = approx_delta(spot=250, strike=250, dte=30, opt_type="Call")
        assert 0.45 < d < 0.55

    def test_call_deep_otm(self):
        """Deep OTM call → delta near 0."""
        d = approx_delta(spot=200, strike=300, dte=30, opt_type="Call")
        assert d < 0.1

    def test_put_otm(self):
        """OTM put → small negative delta."""
        d = approx_delta(spot=260, strike=230, dte=45, opt_type="Put")
        assert -0.3 < d < 0

    def test_put_deep_itm(self):
        """Deep ITM put → delta near -1.0."""
        d = approx_delta(spot=200, strike=300, dte=30, opt_type="Put")
        assert d < -0.9

    def test_put_atm(self):
        """ATM put → delta near -0.5."""
        d = approx_delta(spot=250, strike=250, dte=30, opt_type="Put")
        assert -0.55 < d < -0.45

    def test_zero_dte_fallback(self):
        """Zero DTE → returns ±0.5."""
        assert approx_delta(100, 100, 0, "Call") == 0.5
        assert approx_delta(100, 100, 0, "Put") == -0.5


# ── compute_position_delta ──────────────────────────────

class TestComputePositionDelta:
    def test_stock_long(self):
        """Long 100 shares → delta = 100."""
        pos = _make_stock(qty=100)
        d = compute_position_delta(pos, spot=250)
        assert d == 100

    def test_stock_short(self):
        """Short 50 shares → delta = -50."""
        pos = _make_stock(qty=50, direction="SHORT", market_value=-12500)
        d = compute_position_delta(pos, spot=250)
        assert d == -50

    def test_long_call(self):
        """Long call → positive delta."""
        pos = _make_long_call(strike=250, contracts=10, expiry=_FAR_EXPIRY)
        d = compute_position_delta(pos, spot=260)
        assert d > 0

    def test_long_put(self):
        """Long put → negative delta."""
        pos = _make_long_put(strike=250, contracts=5, expiry=_FAR_EXPIRY)
        d = compute_position_delta(pos, spot=240)
        assert d < 0

    def test_bull_call_spread(self):
        """Spread → net of long and short leg deltas (positive but less than naked call)."""
        spread = _make_bull_call_spread(long_strike=250, short_strike=270, contracts=10, expiry=_FAR_EXPIRY)
        naked = _make_long_call(strike=250, contracts=10, expiry=_FAR_EXPIRY)
        d_spread = compute_position_delta(spread, spot=260)
        d_naked = compute_position_delta(naked, spot=260)
        assert d_spread > 0
        assert d_spread < d_naked  # spread delta < naked call delta

    def test_no_spot_returns_zero(self):
        """Missing spot price → delta = 0."""
        pos = _make_long_call()
        d = compute_position_delta(pos, spot=None)
        assert d == 0


# ── compute_exposure ────────────────────────────────────

class TestComputeExposure:
    def test_single_stock(self):
        port = _portfolio([_make_stock(qty=100, market_value=25000)])
        spots = {"AAPL": 250}
        exp = compute_exposure(port, spots)
        assert exp["dollar_delta"] == pytest.approx(25000, rel=0.01)
        assert exp["net_long"] > 0
        assert exp["net_short"] == 0

    def test_mixed_portfolio(self):
        port = _portfolio([
            _make_stock("AAPL", qty=100, market_value=25000),
            _make_long_put("SPY", strike=500, contracts=2, market_price=10.0),
        ])
        spots = {"AAPL": 250, "SPY": 510}
        exp = compute_exposure(port, spots)
        # Stock is long, put is short delta → dollar delta less than stock alone
        stock_dd = 100 * 250
        assert exp["dollar_delta"] < stock_dd


# ── scenario_price_shock ────────────────────────────────

class TestPriceShock:
    def test_net_liq_drops_on_negative_shock(self):
        """Long portfolio + negative price shock → net liq decreases."""
        port = _portfolio([_make_stock(qty=100, market_value=25000)], bankroll=100000)
        spots = {"AAPL": 250}
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        assert result["impact"]["net_liq_change"] < 0

    def test_dollar_delta_decreases_on_negative_shock(self):
        """Long portfolio + negative shock → dollar delta decreases (lower spot)."""
        port = _portfolio([_make_stock(qty=100, market_value=25000)])
        spots = {"AAPL": 250}
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        assert result["stressed"]["dollar_delta"] < result["current"]["dollar_delta"]

    def test_per_position_breakdown(self):
        """Each position has pnl_impact and new_mv."""
        port = _portfolio([_make_stock(qty=100, market_value=25000)])
        spots = {"AAPL": 250}
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        assert len(result["positions"]) == 1
        p = result["positions"][0]
        assert "pnl_impact" in p
        assert "new_mv" in p
        # Stock: 100 shares, spot drops 10% ($25) → P&L = 100 * -25 = -2500
        assert p["pnl_impact"] == pytest.approx(-2500, rel=0.01)

    def test_positive_shock(self):
        """Positive shock → net liq increases for long portfolio."""
        port = _portfolio([_make_stock(qty=100, market_value=25000)])
        spots = {"AAPL": 250}
        result = scenario_price_shock(port, spots, shock_pct=0.05)
        assert result["impact"]["net_liq_change"] > 0

    def test_options_delta_approximation(self):
        """Options P&L uses delta approximation."""
        port = _portfolio([_make_long_call(strike=250, contracts=10, market_price=8.0)])
        spots = {"AAPL": 260}
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        # Should have negative P&L for long call on negative shock
        assert result["positions"][0]["pnl_impact"] < 0


# ── scenario_delta_decay ────────────────────────────────

class TestDeltaDecay:
    def test_dollar_delta_scales(self):
        """10% delta decay → dollar delta is 90% of original."""
        port = _portfolio([_make_long_call(strike=250, contracts=10)])
        spots = {"AAPL": 260}
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        assert result["stressed"]["dollar_delta"] == pytest.approx(
            result["current"]["dollar_delta"] * 0.90, rel=0.01
        )

    def test_net_liq_drops(self):
        """Delta decay → net liq drops via extrinsic value loss."""
        # Use OTM call (spot < strike) so all premium is extrinsic
        port = _portfolio([_make_long_call(strike=250, contracts=10, market_price=8.0)])
        spots = {"AAPL": 240}  # OTM → mp is all extrinsic
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        assert result["impact"]["net_liq_change"] < 0

    def test_stocks_unaffected(self):
        """Stock positions have no delta decay."""
        port = _portfolio([_make_stock(qty=100, market_value=25000)])
        spots = {"AAPL": 250}
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        # Stock delta doesn't decay, and no extrinsic to lose
        assert result["impact"]["net_liq_change"] == 0
        assert result["stressed"]["dollar_delta"] == result["current"]["dollar_delta"]

    def test_extrinsic_value_loss_math(self):
        """Verify extrinsic decay calculation for a single call."""
        # Call at strike=250, spot=260, mp=8.0 → intrinsic=10, extrinsic=-2 (capped at 0)
        # Actually: spot=260, strike=250, intrinsic=10, mp=8 → extrinsic = max(0, 8-10) = 0
        # Use OTM call: spot=240, strike=250, mp=8 → intrinsic=0, extrinsic=8
        port = _portfolio([_make_long_call(strike=250, contracts=10, market_price=8.0)])
        spots = {"AAPL": 240}  # OTM → all extrinsic
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        # Extrinsic = 8.0, loss = 0.10 * 8.0 * 10 * 100 = 800
        assert result["impact"]["net_liq_change"] == pytest.approx(-800, rel=0.01)

    def test_spread_both_legs_decay(self):
        """Spread: both legs lose extrinsic, net effect depends on direction."""
        port = _portfolio([_make_bull_call_spread(
            long_strike=250, short_strike=270, contracts=10,
            long_mp=8.0, short_mp=3.0
        )])
        spots = {"AAPL": 240}  # Both legs OTM → all extrinsic
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        # Long leg extrinsic loss = 0.10 * 8.0 * 10 * 100 = 800 (hurts)
        # Short leg extrinsic loss = 0.10 * 3.0 * 10 * 100 = 300 (helps)
        # Net = -800 + 300 = -500
        assert result["impact"]["net_liq_change"] == pytest.approx(-500, rel=0.01)


# ── Edge cases ──────────────────────────────────────────

class TestEdgeCases:
    def test_empty_portfolio_price_shock(self):
        port = _portfolio([], bankroll=100000)
        spots = {}
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        assert result["current"]["dollar_delta"] == 0
        assert result["impact"]["net_liq_change"] == 0

    def test_empty_portfolio_delta_decay(self):
        port = _portfolio([], bankroll=100000)
        spots = {}
        result = scenario_delta_decay(port, spots, decay_pct=0.10)
        assert result["current"]["dollar_delta"] == 0
        assert result["impact"]["net_liq_change"] == 0

    def test_missing_spot_for_ticker(self):
        """Position with no spot in spots dict → skipped gracefully."""
        port = _portfolio([_make_long_call(ticker="XYZ")])
        spots = {}  # No XYZ price
        result = scenario_price_shock(port, spots, shock_pct=-0.10)
        assert result["positions"][0]["pnl_impact"] == 0

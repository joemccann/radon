"""Tests for scripts/paper/fills.py — simulated fill-price computation.

When a resting order fills in the shadow engine, the executed price is NOT
the raw limit/quote — a real fill crosses (part of) the bid/ask spread. The
fill model reuses scripts/costs.py:half_spread_slippage so the paper engine
and the live transaction-cost model agree on what crossing the spread costs.

Convention:
  - A BUY pays UP from the mid by the per-share half-spread (worse for buyer).
  - A SELL receives DOWN from the mid by the per-share half-spread.
  - The half-spread per share is derived from the quote via costs.py
    (``(ask - bid) / 2``), or the estimated fallback when no quote exists.

The dollar slippage from costs.half_spread_slippage is per-contract-set
(× OPTION_MULTIPLIER). The per-share price adjustment is that figure divided
back out to a per-share number so it can be added to / subtracted from the
reference price. The two views MUST reconcile.
"""
import pytest

from costs import (
    ESTIMATED_HALF_SPREAD_PER_CONTRACT,
    OPTION_MULTIPLIER,
    half_spread_slippage,
)
from paper.fills import compute_fill_price, FillContext


class TestBuyFillIncludesSlippage:
    """A BUY fill price is worse (higher) than the mid by the half-spread."""

    def test_buy_pays_half_spread_above_mid(self):
        ctx = FillContext(side="BUY", bid=9.90, ask=10.10)
        mid = (9.90 + 10.10) / 2
        half_spread = (10.10 - 9.90) / 2  # 0.10 per share
        expected = mid + half_spread
        assert compute_fill_price(ctx) == pytest.approx(expected)

    def test_buy_slippage_reconciles_with_costs_module(self):
        ctx = FillContext(side="BUY", bid=9.90, ask=10.10)
        mid = (9.90 + 10.10) / 2
        fill = compute_fill_price(ctx)
        per_share_slippage = fill - mid
        # The per-contract-set dollar slippage from costs.py must equal the
        # per-share adjustment × the multiplier (one contract).
        dollar = half_spread_slippage(9.90, 10.10, 1)
        assert per_share_slippage * OPTION_MULTIPLIER == pytest.approx(dollar)


class TestSellFillIncludesSlippage:
    """A SELL fill price is worse (lower) than the mid by the half-spread."""

    def test_sell_receives_half_spread_below_mid(self):
        ctx = FillContext(side="SELL", bid=9.90, ask=10.10)
        mid = (9.90 + 10.10) / 2
        half_spread = (10.10 - 9.90) / 2
        expected = mid - half_spread
        assert compute_fill_price(ctx) == pytest.approx(expected)


class TestEstimatedFallback:
    """With no quote, the estimated half-spread fallback applies."""

    def test_buy_uses_estimated_half_spread_when_no_quote(self):
        ctx = FillContext(side="BUY", reference_price=10.00)
        expected = 10.00 + ESTIMATED_HALF_SPREAD_PER_CONTRACT
        assert compute_fill_price(ctx) == pytest.approx(expected)

    def test_sell_uses_estimated_half_spread_when_no_quote(self):
        ctx = FillContext(side="SELL", reference_price=10.00)
        expected = 10.00 - ESTIMATED_HALF_SPREAD_PER_CONTRACT
        assert compute_fill_price(ctx) == pytest.approx(expected)


class TestNeverNegative:
    """A fill price is clamped to zero, never negative."""

    def test_sell_fill_clamped_at_zero(self):
        ctx = FillContext(side="SELL", reference_price=0.01)
        assert compute_fill_price(ctx) >= 0.0

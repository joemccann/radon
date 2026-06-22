"""Fill-price computation for the paper-trading shadow-fill engine.

When a resting order fills, the executed price is the mid (or the supplied
reference) shifted ADVERSELY by the per-share half-spread — a buyer pays up,
a seller receives down. The half-spread is sourced through
``scripts/costs.py`` so the paper engine and the live transaction-cost model
agree on what crossing the spread costs.

``costs.half_spread_slippage`` returns a per-contract-SET dollar figure
(× ``OPTION_MULTIPLIER``). Here we want a per-SHARE price adjustment, so we
divide that figure back out by the multiplier for one contract. The two views
reconcile exactly:

    per_share_adjustment × OPTION_MULTIPLIER == half_spread_slippage(bid, ask, 1)

Pure function — no I/O, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from costs import OPTION_MULTIPLIER, half_spread_slippage

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class FillContext:
    """Inputs needed to price a simulated fill.

    Supply either a live ``bid``/``ask`` quote (preferred — real spread) or a
    bare ``reference_price`` (the matcher's crossing price), in which case the
    estimated half-spread fallback from ``costs.py`` applies.
    """

    side: str                              # BUY | SELL
    bid: Optional[float] = None
    ask: Optional[float] = None
    reference_price: Optional[float] = None

    def base_price(self) -> float:
        """The unadjusted reference: quote mid, or the supplied reference."""
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        if self.reference_price is not None:
            return self.reference_price
        raise ValueError("FillContext needs a quote or a reference_price")


def compute_fill_price(ctx: FillContext) -> float:
    """Adverse-selection fill price: base shifted by the half-spread.

    BUY pays the half-spread UP; SELL receives it DOWN. Never negative.
    """
    base = ctx.base_price()
    per_share = _per_share_half_spread(ctx.bid, ctx.ask)

    if ctx.side == BUY:
        return base + per_share
    if ctx.side == SELL:
        return max(0.0, base - per_share)
    raise ValueError(f"Unsupported side: {ctx.side!r}")


def _per_share_half_spread(bid: Optional[float], ask: Optional[float]) -> float:
    """Per-share half-spread, derived through costs.half_spread_slippage.

    Asking costs.py for one contract's slippage and dividing by the multiplier
    keeps the paper engine and the cost model on a single source of truth for
    both the live-quote case and the estimated fallback.
    """
    dollar_per_contract = half_spread_slippage(bid, ask, 1)
    return dollar_per_contract / OPTION_MULTIPLIER

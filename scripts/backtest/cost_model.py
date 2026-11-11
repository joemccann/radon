"""Bridge from ``scripts/costs.py`` (dollar round-trip cost) to the fraction
the backtest engine subtracts from each trade's gross return.

The engine works in return space; ``costs.py`` works in dollars. This module
converts a dollar round-trip cost into a fraction of the notional the trade's
return is measured on, so the two stay consistent (edit costs.py, the fraction
follows).

The engine marks every trade against the UNDERLYING's forward return (a put
hedge is modelled as a short underlying position) and the replayed signal rows
carry no option premium. So the default per-trade cost is the costs.py round
trip for one contract per leg of the strategy's documented structure, divided
by the notional one contract controls at the origin close.
"""

from __future__ import annotations

from typing import Optional

from costs import OPTION_MULTIPLIER, estimate_round_trip_cost

from .engine import CostRule, SignalPoint

#: Contracts the default cost assumes: one, so the costs.py per-order
#: commission floor applies (the conservative case for a single-lot trade).
DEFAULT_CONTRACTS: float = 1.0


def round_trip_cost_fraction(
    *,
    contracts: float,
    num_legs: int,
    notional_per_share: float,
    entry_bid: Optional[float] = None,
    entry_ask: Optional[float] = None,
    exit_bid: Optional[float] = None,
    exit_ask: Optional[float] = None,
) -> float:
    """Round-trip cost as a fraction of notional.

    ``notional_per_share`` is the per-share price the trade's return is
    measured on (an option premium for an option-return backtest, the
    underlying close for this engine's marks); notional is
    ``notional_per_share * contracts * OPTION_MULTIPLIER``. A zero or negative
    notional yields 0.0 (no meaningful fraction).
    """
    notional = float(notional_per_share) * abs(contracts) * OPTION_MULTIPLIER
    if notional <= 0.0:
        return 0.0
    dollar_cost = estimate_round_trip_cost(
        contracts,
        num_legs,
        entry_bid=entry_bid,
        entry_ask=entry_ask,
        exit_bid=exit_bid,
        exit_ask=exit_ask,
    )
    return dollar_cost / notional


def real_round_trip_cost_rule(price_field: str, num_legs: int) -> CostRule:
    """Per-trade cost rule: the costs.py round trip over the origin close.

    ``price_field`` names the signal key holding the underlying close the
    engine marks the trade on. A missing close raises: a trade the engine
    could mark always has one, so its absence is a wiring bug, not a free trade.
    """

    def cost_for(point: SignalPoint) -> float:
        return round_trip_cost_fraction(
            contracts=DEFAULT_CONTRACTS,
            num_legs=num_legs,
            notional_per_share=float(point.signal[price_field]),
        )

    return cost_for

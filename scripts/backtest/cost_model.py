"""Bridge from ``scripts/costs.py`` (dollar round-trip cost) to the fraction
the backtest engine subtracts from each trade's gross return.

The engine works in return space; ``costs.py`` works in dollars. This module
converts a dollar round-trip cost into a fraction of the premium notional so
the two stay consistent (edit costs.py, the fraction follows).
"""

from __future__ import annotations

from typing import Optional

from costs import OPTION_MULTIPLIER, estimate_round_trip_cost


def round_trip_cost_fraction(
    *,
    contracts: float,
    num_legs: int,
    premium_per_share: float,
    entry_bid: Optional[float] = None,
    entry_ask: Optional[float] = None,
    exit_bid: Optional[float] = None,
    exit_ask: Optional[float] = None,
) -> float:
    """Round-trip cost as a fraction of premium notional.

    ``premium_per_share`` is the option premium per share; notional is
    ``premium_per_share * contracts * OPTION_MULTIPLIER``. A zero or negative
    notional yields 0.0 (no meaningful fraction).
    """
    notional = float(premium_per_share) * abs(contracts) * OPTION_MULTIPLIER
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

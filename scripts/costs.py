#!/usr/bin/env python3
"""Shared transaction-cost + slippage model (Python side).

Mirror of ``web/lib/order/costs.ts``. Both files MUST model identical numbers
so a Python backtest and the live TypeScript order builder agree on what a fill
costs. Edit one, edit the other.

Three components, all pure functions (no I/O, no network, no DB):

  1. ``commission_cost``       — IB-style flat per-contract commission with a
                                 per-order floor.
  2. ``half_spread_slippage``  — half the quoted bid/ask spread, per contract.
                                 Falls back to an ESTIMATED half-spread when the
                                 contemporaneous quote is unknown.
  3. ``combo_fill_penalty``    — extra slippage for multi-leg BAG orders, which
                                 cross more spread than a single instrument.

Parameter provenance
--------------------
The constants below are PLAN PARAMETERS, not empirically fitted values:

  - ``COMMISSION_PER_CONTRACT`` / ``COMMISSION_MIN_PER_ORDER`` track IBKR's
    published US-options tiered/fixed schedule (USD 0.65/contract, USD 1.00
    order minimum) as of 2026. Treat as a reasonable default, not a contract.
  - ``ESTIMATED_HALF_SPREAD_PER_CONTRACT`` is an ASSUMED fallback for when no
    contemporaneous quote is available (e.g. historical backtest bars without
    a stored spread). It is a modelling assumption, NOT derived from a fill
    study. Prefer a real bid/ask whenever one exists.
  - ``COMBO_LEG_SLIPPAGE_PENALTY`` is an assumed per-extra-leg spread cost for
    BAG orders. Also a modelling assumption.

All slippage / penalty figures are per-share; the ``× 100`` options multiplier
is applied inside each function so callers pass per-share quote values.
"""
from __future__ import annotations

from typing import Optional

# --- Plan parameters (see module docstring for provenance) -------------------

#: IBKR US-options per-contract commission (USD). Default, not a contract.
COMMISSION_PER_CONTRACT: float = 0.65

#: IBKR per-order minimum commission (USD). Floors tiny single-contract orders.
COMMISSION_MIN_PER_ORDER: float = 1.00

#: ASSUMED fallback half-spread per share when no live quote is available.
#: Modelling assumption — NOT empirically derived. Prefer a real bid/ask.
ESTIMATED_HALF_SPREAD_PER_CONTRACT: float = 0.025

#: ASSUMED extra slippage (per share) for each leg beyond the first in a BAG
#: order. Modelling assumption — NOT empirically derived.
COMBO_LEG_SLIPPAGE_PENALTY: float = 0.01

#: Standard US options contract multiplier (shares per contract).
OPTION_MULTIPLIER: int = 100


def commission_cost(contracts: float) -> float:
    """Flat per-contract commission with a per-order floor.

    Zero contracts means no order, hence no commission (the floor does NOT
    apply). Negative counts (a SELL leg) are charged by magnitude.
    """
    count = abs(contracts)
    if count == 0:
        return 0.0
    return max(COMMISSION_MIN_PER_ORDER, count * COMMISSION_PER_CONTRACT)


def half_spread_slippage(
    bid: Optional[float],
    ask: Optional[float],
    contracts: float,
) -> float:
    """Dollar cost of crossing half the bid/ask spread.

    When ``bid``/``ask`` are present and form a valid (non-crossed) quote, the
    per-share half-spread is ``(ask - bid) / 2``. When the quote is missing or
    crossed/zero-width, fall back to ``ESTIMATED_HALF_SPREAD_PER_CONTRACT`` so
    the cost is never understated to $0.
    """
    half_spread = _resolve_half_spread(bid, ask)
    return half_spread * abs(contracts) * OPTION_MULTIPLIER


def combo_fill_penalty(num_legs: int, contracts: float) -> float:
    """Extra slippage for a multi-leg BAG order.

    A single-leg order pays no penalty. Each leg beyond the first crosses an
    additional spread, modelled at ``COMBO_LEG_SLIPPAGE_PENALTY`` per share.
    """
    if num_legs <= 1:
        return 0.0
    extra_legs = num_legs - 1
    return extra_legs * COMBO_LEG_SLIPPAGE_PENALTY * abs(contracts) * OPTION_MULTIPLIER


def total_entry_cost(
    contracts: float,
    num_legs: int,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
) -> float:
    """One-way (entry OR exit) cost: commission + half-spread + combo penalty."""
    return (
        commission_cost(contracts)
        + half_spread_slippage(bid, ask, contracts)
        + combo_fill_penalty(num_legs, contracts)
    )


def estimate_round_trip_cost(
    contracts: float,
    num_legs: int,
    entry_bid: Optional[float] = None,
    entry_ask: Optional[float] = None,
    exit_bid: Optional[float] = None,
    exit_ask: Optional[float] = None,
) -> float:
    """Round-trip cost: entry leg + exit leg.

    The exit leg uses the exit quote when supplied; otherwise it falls back to
    the estimated half-spread (the exit quote is usually unknown at entry).
    """
    entry = total_entry_cost(contracts, num_legs, entry_bid, entry_ask)
    exit_cost = total_entry_cost(contracts, num_legs, exit_bid, exit_ask)
    return entry + exit_cost


def _resolve_half_spread(bid: Optional[float], ask: Optional[float]) -> float:
    """Per-share half-spread from a quote, or the estimated fallback."""
    if bid is None or ask is None:
        return ESTIMATED_HALF_SPREAD_PER_CONTRACT
    spread = ask - bid
    if spread <= 0:
        return ESTIMATED_HALF_SPREAD_PER_CONTRACT
    return spread / 2.0

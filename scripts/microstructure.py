#!/usr/bin/env python3
"""Microstructure imbalance / microprice scan (F10).

Pure functions over a two-sided depth-book snapshot. No I/O, no network, no DB.
Mirrors the TypeScript derivation in ``web/lib/book/microstructure.ts`` so a
Python scan and the live Book tab agree on the numbers. Edit one, edit the
other.

Two signals, both derived from resting depth only (the trade-direction tape is
still Phase 3 / empty, so VPIN / order-flow toxicity is deliberately OUT of
scope here):

  - ``book_imbalance`` — net resting pressure in [-1, 1] over the top-N levels:
        (sum bid size - sum ask size) / (sum bid size + sum ask size)
    +1 = all bid (buyers stacked), -1 = all ask (sellers stacked), 0 = balanced.

  - ``microprice`` — the size-weighted touch price. A heavy ASK pulls the fair
    price DOWN toward the bid and vice-versa, so it weights each touch price by
    the OPPOSITE side's size:
        (bestBid * askSize + bestAsk * bidSize) / (bidSize + askSize)

``microstructure_metrics`` bundles both plus the plain mid and the micro premium
(microprice - mid) for a snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# Default depth used for the imbalance sum. Matches the relay's typical 5-10
# row depth ticket; callers may override per scan.
DEFAULT_TOP_N = 5


@dataclass(frozen=True)
class DepthLevel:
    """One resting level. Mirrors the relay ``DepthLevel`` (price + size only;
    venue attribution is irrelevant to these aggregates)."""

    price: float
    size: float

    @staticmethod
    def from_raw(raw) -> "DepthLevel":
        if isinstance(raw, DepthLevel):
            return raw
        return DepthLevel(
            price=float(raw.get("price", 0.0) or 0.0),
            size=float(raw.get("size", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class DepthSnapshot:
    """A two-sided book ordered best -> worst on each side (index 0 = inside)."""

    bid: Sequence[DepthLevel]
    ask: Sequence[DepthLevel]

    @staticmethod
    def from_book(book) -> "DepthSnapshot":
        """Build from a raw relay-shaped dict ``{"bid": [...], "ask": [...]}``."""
        bid = [DepthLevel.from_raw(level) for level in (book.get("bid") or [])]
        ask = [DepthLevel.from_raw(level) for level in (book.get("ask") or [])]
        return DepthSnapshot(bid=bid, ask=ask)


@dataclass(frozen=True)
class MicrostructureMetrics:
    imbalance: Optional[float]
    microprice: Optional[float]
    mid: Optional[float]
    micro_premium: Optional[float]


def _sum_size(levels: Sequence[DepthLevel], top_n: int) -> float:
    return sum(level.size for level in levels[:top_n])


def book_imbalance(
    bid: Sequence[DepthLevel],
    ask: Sequence[DepthLevel],
    top_n: int = DEFAULT_TOP_N,
) -> Optional[float]:
    """Net resting pressure in [-1, 1] over the top-N levels.

    Returns ``None`` when there is no resting size on either side (an empty or
    zero-size book carries no imbalance signal). A one-sided book is a fully
    saturated signal: all bid -> +1, all ask -> -1.
    """
    bid_size = _sum_size(bid, top_n)
    ask_size = _sum_size(ask, top_n)
    total = bid_size + ask_size
    if total <= 0:
        return None
    return (bid_size - ask_size) / total


def microprice(
    best_bid: Optional[float],
    best_ask: Optional[float],
    bid_size: float,
    ask_size: float,
) -> Optional[float]:
    """Volume-weighted microprice off the touch.

    Each touch price is weighted by the OPPOSITE side's size, so a thick ask
    pulls the fair value toward the bid. Returns ``None`` when either quote is
    missing or there is no size to weight against.
    """
    if best_bid is None or best_ask is None:
        return None
    total = bid_size + ask_size
    if total <= 0:
        return None
    return (best_bid * ask_size + best_ask * bid_size) / total


def microstructure_metrics(
    snapshot: DepthSnapshot,
    top_n: int = DEFAULT_TOP_N,
) -> MicrostructureMetrics:
    """Bundle imbalance + microprice + mid + micro premium for a snapshot."""
    imbalance = book_imbalance(snapshot.bid, snapshot.ask, top_n=top_n)

    best_bid = snapshot.bid[0].price if snapshot.bid else None
    best_ask = snapshot.ask[0].price if snapshot.ask else None
    best_bid_size = snapshot.bid[0].size if snapshot.bid else 0.0
    best_ask_size = snapshot.ask[0].size if snapshot.ask else 0.0

    micro = microprice(best_bid, best_ask, best_bid_size, best_ask_size)
    mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    micro_premium = micro - mid if micro is not None and mid is not None else None

    return MicrostructureMetrics(
        imbalance=imbalance,
        microprice=micro,
        mid=mid,
        micro_premium=micro_premium,
    )

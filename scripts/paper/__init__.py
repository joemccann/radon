"""Paper-trading / shadow-fill engine (Feature F13).

A homegrown matching engine that turns resting LIMIT/STOP orders + an incoming
tick into simulated fills, prices them through the shared cost model
(``scripts/costs.py``), and persists them to a paper-namespaced store
(``paper_fills`` table) so the live IB-rehydrated ``journal`` table is never
disturbed.

LIMITATION — this matcher CANNOT reproduce IB-side combo-router behaviour.
IB Smart routing silently drops some BAG structures (notably bearish risk
reversals: SELL CALL + BUY PUT at different strikes), holding them in
``PendingSubmit`` forever with no ``errorEvent``. A pure-Python matcher always
fills a marketable order, so it gives a falsely optimistic result for those
structures. To test real combo-router behaviour, point the order path at the
IBKR NATIVE paper-trading account on port 7497 instead of this engine. See
``feedback_ib_combo_router_silent_drops_bearish_rr.md``.
"""

from .matcher import RestingOrder, MarketTick, FillDecision, match_order
from .fills import FillContext, compute_fill_price

__all__ = [
    "RestingOrder",
    "MarketTick",
    "FillDecision",
    "match_order",
    "FillContext",
    "compute_fill_price",
]

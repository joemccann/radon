"""Paper-trading / shadow-fill engine (Feature F13).

A homegrown matching engine that turns resting LIMIT/STOP orders + an incoming
tick into simulated fills, prices them through the shared cost model
(``scripts/costs.py``), and persists them to a paper-namespaced store
(``paper_fills`` table) so the live IB-rehydrated ``journal`` table is never
disturbed.

Limitation: this matcher fills a marketable order from the tick. That fill
is not an IB acknowledgement. An unacknowledged live order has an unknown
outcome; reconcile open orders before any new submit. The order placement
contract is ``scripts/CLAUDE.md``. A real broker session is TWS paper port
7497 (``scripts/clients/ib_client.py`` ``DEFAULT_TWS_PORT``), not this engine.
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

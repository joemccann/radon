"""Chat capability pin map for FastAPI routes.

Unclassified live routes fail CI. This module is classification only:
no OpenAPI fetch, no dispatch, no radonFetch equivalent.
"""

from __future__ import annotations

from typing import Literal

Capability = Literal[
    "read",
    "read.spawn",
    "mutate.workspace",
    "mutate.trading",
    "admin",
    "internal",
]

REFUSED_CAPABILITIES: frozenset[str] = frozenset(
    {"admin", "internal", "mutate.trading"}
)

CatalogKey = tuple[str, str]

CATALOG: dict[CatalogKey, Capability] = {
    ("GET", "/admin/services"): "admin",
    ("POST", "/admin/services/{unit}/{action}"): "admin",
    ("POST", "/admin/stack/restart"): "admin",
    ("GET", "/attribution"): "read",
    ("GET", "/backtest"): "read",
    ("GET", "/backtest/{strategy}"): "read",
    ("POST", "/blotter"): "mutate.workspace",
    ("POST", "/bpi/scan"): "read.spawn",
    ("POST", "/breadth/scan"): "read.spawn",
    ("GET", "/cash-flows"): "read",
    ("GET", "/catalysts"): "read",
    ("POST", "/contract/qualify"): "read.spawn",
    ("POST", "/cta/share"): "internal",
    ("POST", "/demo/trial-expiry"): "internal",
    ("POST", "/discover"): "read.spawn",
    ("GET", "/docs"): "internal",
    ("GET", "/docs/oauth2-redirect"): "internal",
    ("GET", "/earnings"): "read",
    ("GET", "/earnings/{ticker}"): "read",
    ("GET", "/event-odds/{ticker}"): "read",
    ("POST", "/flow-analysis"): "read.spawn",
    ("GET", "/flow-analysis/{ticker}"): "read",
    ("POST", "/flow-analysis/{ticker}"): "read.spawn",
    ("POST", "/flow-surprise"): "read.spawn",
    ("POST", "/forecast/chronos"): "read.spawn",
    ("GET", "/futures/chain"): "read",
    ("POST", "/gamma-rotation/scan"): "read.spawn",
    ("POST", "/garch-convergence/scan"): "read.spawn",
    ("POST", "/gex/scan"): "read.spawn",
    ("POST", "/gex/share"): "internal",
    ("GET", "/health"): "internal",
    ("GET", "/health/lite"): "internal",
    ("POST", "/historical/bars"): "read.spawn",
    ("POST", "/historical/head-timestamp"): "read.spawn",
    ("POST", "/ib/reset-backoff"): "admin",
    ("POST", "/ib/restart"): "admin",
    ("GET", "/index-options/chain"): "read",
    ("GET", "/informed-flow/{ticker}"): "read",
    ("POST", "/internals/share"): "internal",
    ("GET", "/internals/skew-history"): "read",
    ("POST", "/journal/reconcile"): "mutate.workspace",
    ("POST", "/journal/rehydrate"): "mutate.workspace",
    ("GET", "/knowledge/prior-evals"): "read",
    ("POST", "/knowledge/search"): "read",
    ("POST", "/leap/scan"): "read.spawn",
    ("GET", "/llm-token-index"): "read",
    ("GET", "/market-calendar"): "read",
    ("POST", "/market-calendar/refresh"): "read.spawn",
    ("GET", "/openapi.json"): "internal",
    ("GET", "/options/chain"): "read",
    ("GET", "/options/expirations"): "read",
    ("GET", "/options/exposure/{symbol}"): "read",
    ("GET", "/options/rv-ratio/{symbol}"): "read",
    ("POST", "/options/rv-ratio/{symbol}/scan"): "read.spawn",
    ("GET", "/options/uw-chain"): "read",
    ("POST", "/orders/cancel"): "mutate.trading",
    ("POST", "/orders/cancel-all"): "mutate.trading",
    ("POST", "/orders/modify"): "mutate.trading",
    ("POST", "/orders/place"): "mutate.trading",
    ("POST", "/orders/refresh"): "mutate.workspace",
    ("POST", "/orders/replace"): "mutate.trading",
    ("POST", "/orders/whatif"): "read.spawn",
    ("POST", "/paper/place"): "mutate.trading",
    ("POST", "/performance"): "read.spawn",
    ("POST", "/performance/background"): "read.spawn",
    ("POST", "/pi/exec"): "internal",
    ("POST", "/portfolio/background-sync"): "mutate.workspace",
    ("POST", "/portfolio/sync"): "mutate.workspace",
    ("GET", "/preferences"): "read",
    ("DELETE", "/preferences/{key}"): "mutate.workspace",
    ("PUT", "/preferences/{key}"): "mutate.workspace",
    ("GET", "/quote/{ticker}"): "read",
    ("GET", "/redoc"): "internal",
    ("POST", "/regime/scan"): "read.spawn",
    ("POST", "/regime/share"): "internal",
    ("POST", "/scan"): "read.spawn",
    ("GET", "/share/content"): "internal",
    ("GET", "/short-availability/{ticker}"): "read",
    ("POST", "/strength-confirmation/scan"): "read.spawn",
    ("POST", "/theta-harvester/scan"): "read.spawn",
    ("GET", "/ticker/ratings"): "read",
    ("POST", "/trading/halt"): "admin",
    ("POST", "/trading/kill"): "admin",
    ("POST", "/trading/resume"): "admin",
    ("GET", "/trading/status"): "admin",
    ("GET", "/uw/usage"): "read",
    ("POST", "/uw/usage/record"): "internal",
    ("POST", "/vcg/scan"): "read.spawn",
    ("POST", "/vcg/share"): "internal",
    ("POST", "/workflow/run"): "mutate.trading",
    ("POST", "/ws-ticket"): "internal",
    ("POST", "/ws-ticket/validate"): "internal",
}


def is_refused(cap: str) -> bool:
    return cap in REFUSED_CAPABILITIES


def capability_for(method: str, path: str) -> Capability | None:
    """Look up a pinned capability. Unpinned routes, including POSTs, deny."""
    return CATALOG.get((method.upper(), path))

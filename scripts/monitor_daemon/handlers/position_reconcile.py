#!/usr/bin/env python3
"""
Position Reconcile — scheduled broker-truth position check (REL-001 / R-005).

``ib_reconcile.py`` only ran when a browser loaded ``/journal`` and its
report paged no one. This handler runs the same per-contract quantity
comparison inside the monitor daemon every cycle during market hours and
writes a dedicated ``position-reconcile`` service_health row:

  - ok     — IB positions and the latest portfolio snapshot agree
  - error  — quantity drift / missing positions (structured detail), or
             the check itself could not run

Alert-only: never mutates the journal or the snapshot. IB is the source
of truth; the snapshot must prove it agrees.

Registration: web/lib/serviceHealthWindows.ts + scripts/watchdog/services.py
(intraday bucket, requires_ib=true).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .base import BaseHandler

logger = logging.getLogger(__name__)

SERVICE_NAME = "position-reconcile"

# Positions change on fills; 30 min bounds drift-detection latency without
# adding meaningful IB session churn (fresh auto-ID connection per cycle).
RECONCILE_INTERVAL_SECONDS = 1800


def _default_client_factory():
    from clients.ib_client import IBClient

    client = IBClient()
    client.connect(client_id="auto")
    return client


def _default_snapshot_loader() -> dict:
    from db.readers import read_latest_portfolio_snapshot

    return read_latest_portfolio_snapshot() or {"positions": []}


def _default_positions_fetcher(client: Any) -> list:
    import ib_reconcile

    return ib_reconcile.fetch_ib_positions(client)


class PositionReconcileHandler(BaseHandler):
    """Scheduled IB-vs-snapshot position reconciliation (alert-only)."""

    name = "position_reconcile"
    interval_seconds = RECONCILE_INTERVAL_SECONDS
    requires_market_hours = True
    # Literal (not SERVICE_NAME) so the registration-completeness AST
    # collector sees it and enforces the serviceHealthWindows.ts entry.
    service_name = "position-reconcile"

    def __init__(
        self,
        client_factory: Optional[Callable[[], Any]] = None,
        snapshot_loader: Optional[Callable[[], dict]] = None,
        positions_fetcher: Optional[Callable[[Any], list]] = None,
    ):
        super().__init__()
        self._client_factory = client_factory or _default_client_factory
        self._snapshot_loader = snapshot_loader or _default_snapshot_loader
        self._positions_fetcher = positions_fetcher or _default_positions_fetcher

    def execute(self) -> dict[str, Any]:
        import ib_reconcile

        # Raising here is deliberate: BaseHandler records the error state
        # and does not latch last_run, so the next cycle retries.
        client = self._client_factory()
        try:
            ib_positions = self._positions_fetcher(client)
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001 — teardown of a dead session
                pass

        portfolio = self._snapshot_loader()
        discrepancies = ib_reconcile.find_position_discrepancies(
            ib_positions, portfolio
        )
        report = ib_reconcile.generate_reconciliation_report([], discrepancies)
        detail = ib_reconcile._health_detail(report)

        state = "error" if report["needs_attention"] else "ok"
        self.record_cycle_health(state, error=detail)

        result: dict[str, Any] = {
            "ib_positions": len(ib_positions),
            "local_positions": len(portfolio.get("positions", [])),
            "quantity_mismatch_count": detail["quantity_mismatch_count"],
            "positions_missing_locally_count": detail["positions_missing_locally_count"],
            "positions_closed_count": detail["positions_closed_count"],
        }
        if report["needs_attention"]:
            result["error"] = (
                f"position drift vs IB: {detail['quantity_mismatch_count']} quantity "
                f"mismatch(es), {detail['positions_missing_locally_count']} missing "
                f"locally, {detail['positions_closed_count']} closed locally"
            )
            logger.warning("position-reconcile: %s detail=%s", result["error"], detail)
        else:
            logger.debug(
                "position-reconcile: in sync (ib=%d local=%d)",
                result["ib_positions"],
                result["local_positions"],
            )
        return result

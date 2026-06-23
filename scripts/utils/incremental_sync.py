"""Incremental portfolio sync — skip full sync when no position changes detected.

Compares latest Turso portfolio snapshot positions against live IB positions by
(ticker, expiry) key and contract count. If no changes, returns existing
portfolio data without triggering a full sync.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from db.readers import read_latest_portfolio_snapshot

logger = logging.getLogger("incremental_sync")


def _normalize_expiry(raw_expiry: str) -> str:
    """Normalize expiry strings to YYYY-MM-DD format for comparison.

    IB returns YYYYMMDD; portfolio snapshots store YYYY-MM-DD or N/A.
    """
    if not raw_expiry or raw_expiry == "N/A":
        return "N/A"
    # Strip any dashes to get YYYYMMDD, then re-format
    clean = raw_expiry.replace("-", "")
    if len(clean) == 8:
        return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"
    return raw_expiry


def _build_ib_position_map(ib_positions: list) -> Dict[tuple, int]:
    """Build a (ticker, expiry) -> total_abs_position map from IB positions."""
    pos_map: Dict[tuple, int] = {}
    for pos in ib_positions:
        contract = pos.contract
        symbol = contract.symbol
        sec_type = getattr(contract, "secType", "")
        raw_expiry = getattr(contract, "lastTradeDateOrContractMonth", "")

        if sec_type == "STK" or not raw_expiry:
            expiry = "N/A"
        else:
            expiry = _normalize_expiry(raw_expiry)

        key = (symbol, expiry)
        # Sum absolute positions per key (handles multiple legs)
        pos_map[key] = pos_map.get(key, 0) + int(abs(pos.position))
    return pos_map


def _build_portfolio_position_map(positions: List[Dict]) -> Dict[tuple, int]:
    """Build a (ticker, expiry) -> contracts map from portfolio snapshot positions."""
    pos_map: Dict[tuple, int] = {}
    for p in positions:
        ticker = p.get("ticker", p.get("symbol", ""))
        expiry = _normalize_expiry(p.get("expiry", "N/A"))
        contracts = int(p.get("contracts", 0))
        key = (ticker, expiry)
        pos_map[key] = pos_map.get(key, 0) + contracts
    return pos_map


def positions_changed(portfolio_positions: List[Dict], ib_positions: list) -> bool:
    """Compare portfolio snapshot positions against live IB positions.

    Returns True if any differences detected (position added, removed,
    or contract count changed).
    """
    portfolio_map = _build_portfolio_position_map(portfolio_positions)
    ib_map = _build_ib_position_map(ib_positions)

    if portfolio_map.keys() != ib_map.keys():
        logger.info(
            "Position keys differ: portfolio=%s, ib=%s",
            sorted(portfolio_map.keys()),
            sorted(ib_map.keys()),
        )
        return True

    for key in portfolio_map:
        if portfolio_map[key] != ib_map[key]:
            logger.info(
                "Contract count changed for %s: portfolio=%d, ib=%d",
                key, portfolio_map[key], ib_map[key],
            )
            return True

    return False


def incremental_sync(client: Any, portfolio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compare current portfolio against IB and decide if full sync is needed.

    Args:
        client: IBClient instance (must be connected).
        portfolio: Optional preloaded portfolio snapshot for tests or callers
            that already fetched it. If omitted, the latest Turso snapshot is
            read.

    Returns:
        dict with keys:
            - changed (bool): True if positions differ, False if identical.
            - portfolio (dict or None): Existing portfolio data if no changes.
    """
    # Fetch live IB positions
    ib_positions = client.get_positions()

    snapshot = portfolio if portfolio is not None else read_latest_portfolio_snapshot()
    if not snapshot:
        logger.info("No portfolio snapshot found — full sync needed")
        return {"changed": True, "portfolio": None}

    existing_positions = snapshot.get("positions", [])

    if positions_changed(existing_positions, ib_positions):
        logger.info("Position changes detected — full sync needed")
        return {"changed": True, "portfolio": snapshot}

    logger.info("No position changes detected — skipping full sync")
    return {"changed": False, "portfolio": snapshot}

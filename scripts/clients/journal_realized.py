"""Journal average-cost realized P&L for executed option fills.

IB's commission-report ``realizedPNL`` is computed against IB's position
avgCost, which drifts after a partial close (the same drift
``journal_basis.compute_open_basis_for_ticker`` exists to defeat for the
open basis). This module replays the journal's per-fill history for a
contract with the average-cost inventory model used by
``journal_rehydrate._compute_pnl_summary`` and attributes realized P&L to
each closing execution id.

A contract only yields figures when its journal history is COMPLETE: a
close that meets an empty or smaller open position means fills are missing,
and every figure for that contract is dropped rather than trusted (same
principle as the ``ib_sync`` journal-basis guard).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional, Sequence

from .journal_basis import (
    _bucket_key,
    _claim_exec_parts,
    _exec_id_parts,
    _normalize_ticker,
    _payload_from_row,
    _row_value,
    _signed_qty,
)

logger = logging.getLogger(__name__)

OPTION_MULTIPLIER = 100.0
CLOSING_ACTIONS = {"SELL_OPTION", "BUY_TO_CLOSE"}

JournalExecute = Callable[[str, Sequence[Any]], Iterable[Any]]


def realized_pnl_by_exec_id(rows: Iterable[Any]) -> dict[str, float]:
    """Map each single-execution closing journal row to its realized P&L.

    ``rows`` are ``(payload, filled_at, written_at)`` journal rows for any
    number of tickers. Only option rows with a full contract key take part;
    BAG envelopes (``right == "?"``) and stock rows are ignored.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in _ordered(rows):
        entry = _journal_entry(row)
        if entry is None:
            continue
        buckets.setdefault(entry["key"], []).append(entry)

    realized: dict[str, float] = {}
    counted_parts: set[str] = set()
    for key, entries in buckets.items():
        realized.update(_replay_contract(key, entries, counted_parts))
    return realized


def journal_realized_pnl_for_fills(
    execute: JournalExecute, fills: Sequence[dict[str, Any]]
) -> dict[str, float]:
    """Load the journal for the option tickers in ``fills`` and replay it."""
    tickers = sorted({t for t in (_option_fill_ticker(f) for f in fills) if t})
    if not tickers:
        return {}
    placeholders = ", ".join("?" for _ in tickers)
    rows = execute(
        f"""
        SELECT payload, filled_at, written_at
        FROM journal
        WHERE UPPER(COALESCE(
            json_extract(payload, '$.ticker'),
            json_extract(payload, '$.symbol'),
            ''
        )) IN ({placeholders})
        ORDER BY COALESCE(filled_at, written_at) ASC, written_at ASC
        """,
        tuple(tickers),
    )
    return realized_pnl_by_exec_id(rows)


def apply_journal_realized_pnl(
    fills: Sequence[dict[str, Any]], realized: dict[str, float]
) -> None:
    """Replace IB ``realizedPNL`` on closing option fills the journal covers.

    The IB figure is preserved as ``ibRealizedPNL``; ``realizedPNLSource``
    records which one the row now carries.
    """
    for fill in fills:
        if not _option_fill_ticker(fill):
            continue
        journal_value = realized.get(str(fill.get("execId") or ""))
        if journal_value is not None:
            fill["ibRealizedPNL"] = fill.get("realizedPNL")
            fill["realizedPNL"] = journal_value
            fill["realizedPNLSource"] = "journal"
        elif _is_nonzero(fill.get("realizedPNL")):
            fill["realizedPNLSource"] = "ib"


# ---------------------------------------------------------------------------


def _option_fill_ticker(fill: dict[str, Any]) -> str:
    contract = fill.get("contract") or {}
    if str(contract.get("secType") or "").upper() != "OPT":
        return ""
    return _normalize_ticker(contract.get("symbol") or fill.get("symbol"))


def _is_nonzero(value: Any) -> bool:
    try:
        return abs(float(value)) > 0.01
    except (TypeError, ValueError):
        return False


def _ordered(rows: Iterable[Any]) -> list[Any]:
    """Journal ``filled_at`` is date-only; ``execution_time`` orders a day."""

    def sort_key(indexed: tuple[int, Any]) -> tuple:
        index, row = indexed
        payload = _payload_from_row(row)
        day = str(_row_value(row, "filled_at") or _row_value(row, "written_at") or "")[:10]
        exec_time = str(payload.get("execution_time") or "")
        written = str(_row_value(row, "written_at") or "")
        return (day, exec_time, written, index)

    return [row for _, row in sorted(enumerate(rows), key=sort_key)]


def _journal_entry(row: Any) -> Optional[dict[str, Any]]:
    payload = _payload_from_row(row)
    key = _bucket_key(payload)
    if key is None:
        return None
    try:
        qty = abs(float(payload.get("contracts")))
        price = float(payload.get("fill_price"))
    except (TypeError, ValueError):
        return None
    signed_qty = _signed_qty(payload.get("action"), qty)
    if signed_qty == 0:
        return None
    try:
        commission = abs(float(payload.get("commission") or 0.0))
    except (TypeError, ValueError):
        commission = 0.0
    try:
        multiplier = float(payload.get("multiplier") or OPTION_MULTIPLIER)
    except (TypeError, ValueError):
        multiplier = OPTION_MULTIPLIER
    return {
        "key": key,
        "parts": _exec_id_parts(payload),
        "qty": qty,
        "is_buy": signed_qty > 0,
        "is_close": str(payload.get("action") or "").strip().upper() in CLOSING_ACTIONS,
        "notional": qty * price * multiplier,
        "commission": commission,
    }


def _replay_contract(
    key: str, entries: list[dict[str, Any]], counted_parts: set[str]
) -> dict[str, float]:
    """Average-cost inventory replay; empty when the history is incomplete."""
    realized: dict[str, float] = {}
    position_qty = 0.0
    avg_basis_per_unit = 0.0

    for entry in entries:
        if not _claim_exec_parts(counted_parts, entry["parts"], key):
            continue
        qty = entry["qty"]
        is_buy = entry["is_buy"]
        cash = entry["notional"] + entry["commission"] if is_buy else entry["notional"] - entry["commission"]
        signed_qty = qty if is_buy else -qty
        same_direction = position_qty == 0 or (position_qty > 0) == is_buy

        if entry["is_close"] and same_direction:
            logger.warning(
                "journal_realized: %s has a closing fill with no journaled open — "
                "journal incomplete, keeping IB realizedPNL",
                key,
            )
            return {}

        if same_direction:
            current_basis = avg_basis_per_unit * abs(position_qty)
            position_qty += signed_qty
            avg_basis_per_unit = (current_basis + cash) / abs(position_qty)
            continue

        if qty > abs(position_qty):
            logger.warning(
                "journal_realized: %s closes %s against %s open — journal incomplete, "
                "keeping IB realizedPNL",
                key, qty, abs(position_qty),
            )
            return {}

        basis_closed = avg_basis_per_unit * qty
        pnl = cash - basis_closed if position_qty > 0 else basis_closed - cash
        position_qty += signed_qty
        if position_qty == 0:
            avg_basis_per_unit = 0.0
        if len(entry["parts"]) == 1:
            realized[entry["parts"][0]] = round(pnl, 4)

    return realized

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
from typing import Any, Iterable, Optional, Sequence

from .journal_basis import (
    _bucket_key,
    _fetch_journal_rows_for_tickers,
    _claim_exec_parts,
    _exec_id_parts,
    _normalize_ticker,
    _payload_from_row,
    _row_value,
    _signed_qty,
    contract_fill_fingerprint,
)

logger = logging.getLogger(__name__)

OPTION_MULTIPLIER = 100.0
CLOSING_ACTIONS = {"SELL_OPTION", "BUY_TO_CLOSE"}



def realized_pnl_by_exec_id(rows: Iterable[Any]) -> dict[str, float]:
    """Map each single-execution closing journal row to its realized P&L.

    ``rows`` are ``(payload, filled_at, written_at)`` journal rows for any
    number of tickers. Only option rows with a full contract key take part;
    BAG envelopes (``right == "?"``) and stock rows are ignored.
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    unusable: set[str] = set()
    for row in _ordered(rows):
        entry = _journal_entry(row)
        if entry is None:
            key = _unusable_fill_key(row)
            if key is not None:
                unusable.add(key)
            continue
        buckets.setdefault(entry["key"], []).append(entry)

    realized: dict[str, float] = {}
    counted_parts: set[str] = set()
    for key, entries in buckets.items():
        if key in unusable:
            # Both replay guards are quantity-only, and a dropped row removes
            # its quantity and its cost together, so they cancel and the
            # replay silently prices the close against too small a basis.
            # The shortfall is only visible here, where the row was seen.
            logger.warning(
                "journal_realized: %s has a journal row that is not a usable fill — "
                "journal incomplete, keeping IB realizedPNL",
                key,
            )
            continue
        realized.update(_replay_contract(key, entries, counted_parts))
    return realized


def journal_realized_pnl_for_fills(
    db: Any, fills: Sequence[dict[str, Any]]
) -> dict[str, float]:
    """Load the journal for the option tickers in ``fills`` and replay it.

    Reads through ``journal_basis._fetch_journal_rows_for_tickers``, the
    200-row keyset pager. The previous unpaginated ``WHERE ticker IN (...)``
    had no LIMIT and no date bound over a monotonically growing table, on a
    path that runs inside ``service_cycle("orders-sync")``. R-203.
    """
    tickers = sorted({t for t in (_option_fill_ticker(f) for f in fills) if t})
    if not tickers:
        return {}
    return realized_pnl_by_exec_id(_fetch_journal_rows_for_tickers(db, tickers))


class _HranaCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows


class _BoundedJournalReader:
    """``execute(...).fetchall()`` surface backed by the bounded transport.

    Lets the shared keyset pager run over Hrana HTTP, which has a real socket
    timeout, instead of ``db.client.get_db()`` — the sync
    ``libsql_experimental`` connection that exposes no execute timeout and
    holds the GIL while blocked. R-203.
    """

    def execute(self, sql: str, args: Sequence[Any] = ()) -> _HranaCursor:
        from db import hrana_http  # noqa: PLC0415 — lazy; libsql optional

        return _HranaCursor(hrana_http.hrana_query(sql, args))


def overlay_journal_realized_pnl(
    fills: Sequence[dict[str, Any]], *, reader: Any = None
) -> None:
    """Write-time overlay for the ``executed_orders`` writers.

    Best-effort: a journal read failure leaves IB's figures untouched so the
    sync itself never fails on the overlay. ``reader`` exists for injection;
    the default is the bounded transport.
    """
    if not fills:
        return
    try:
        realized = journal_realized_pnl_for_fills(
            reader if reader is not None else _BoundedJournalReader(), fills
        )
    except Exception as exc:  # noqa: BLE001 — overlay is advisory
        logger.warning("journal_realized: overlay skipped: %s", exc)
        return
    apply_journal_realized_pnl(fills, realized)


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
    action = str(payload.get("action") or "").strip().upper()
    # A rehydrated CLOSED row is a round trip, not a directional fill:
    # `_signed_qty` maps it to -qty, which the replay would read as an
    # opening SHORT and then attribute realized P&L to the next BUY (T-124).
    if action == "CLOSED":
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
        "is_close": action in CLOSING_ACTIONS,
        "fingerprint": contract_fill_fingerprint(payload),
        "notional": qty * price * multiplier,
        "commission": commission,
    }


def _unusable_fill_key(row: Any) -> Optional[str]:
    """Bucket key of a row that names a contract but is not a usable fill.

    ``None`` for rows the replay excludes BY DESIGN and whose exclusion keeps
    the inventory balanced: rows with no full contract key (BAG envelopes,
    stock), and rehydrated ``CLOSED`` round trips, which carry no net quantity
    and whose P&L is realized elsewhere (T-124). Everything else — a missing
    or non-numeric ``contracts``/``fill_price``, an action ``_signed_qty``
    does not recognise — is a fill this contract's history is missing, and the
    contract must fall back to IB's own figure. R-198.
    """
    payload = _payload_from_row(row)
    key = _bucket_key(payload)
    if key is None:
        return None
    if str(payload.get("action") or "").strip().upper() == "CLOSED":
        return None
    return key


def _replay_contract(
    key: str, entries: list[dict[str, Any]], counted_parts: set[str]
) -> dict[str, float]:
    """Average-cost inventory replay; empty when the history is incomplete."""
    realized: dict[str, float] = {}
    position_qty = 0.0
    avg_basis_per_unit = 0.0
    seen_fingerprints: dict[tuple, set[str]] = {}

    for entry in entries:
        if not _claim_exec_parts(counted_parts, entry["parts"], key):
            continue
        if _already_journaled_under_other_namespace(seen_fingerprints, entry, key):
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


def _id_namespaces(parts: list[str]) -> set[str]:
    """Flex rehydrate keys rows on the numeric Flex ``tradeID``; the real-time
    daemon and ``executed_orders`` key on the dotted IB API execId."""
    return {"flex" if part.isdigit() else "api" for part in parts}


def _already_journaled_under_other_namespace(
    seen: dict[tuple, set[str]], entry: dict[str, Any], key: str
) -> bool:
    """True when this fill (contract, session date, signed qty) was already
    replayed from a row keyed in the OTHER id namespace — the same close
    written by both writers (T-124). Two equal same-day partials from ONE
    writer share a namespace and both count, as in the backfill."""
    fingerprint = entry.get("fingerprint")
    namespaces = _id_namespaces(entry["parts"])
    if fingerprint is None or not namespaces:
        return False
    prior = seen.setdefault(fingerprint, set())
    if prior and prior.isdisjoint(namespaces):
        logger.info(
            "journal_realized: %s skipped %s — same fill already journaled under %s",
            key, "+".join(entry["parts"]), "/".join(sorted(prior)),
        )
        return True
    prior |= namespaces
    return False


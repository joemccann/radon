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

Completeness is enforced on TWO axes, and one of them is bounded. A row
that cannot name its contract, or that names an impossible one (a
non-positive strike, a right outside ``C``/``P``, an expiry that is not a
real calendar date), poisons its whole ticker so the contract falls back to
IB's figure — R-274, extended by R-320. What this module CANNOT detect is a
contract field corrupted into a different but still-listable contract (a
strike of 600 where the fill was 60, an expiry shifted to another real
Wednesday): that row mints a well-formed key, forms its own bucket, and is
indistinguishable from a legitimate second position held on the same
ticker. Closing that residue needs an authoritative contract set — IB
positions or contract details — which this entry point does not receive.
Callers holding one should cross-check before trusting a figure here.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable, Optional, Sequence

from .journal_basis import (
    _bucket_key,
    _normalize_expiry,
    _normalize_right,
    _normalize_strike,
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

# A BAG envelope marks itself with this right; it is a by-design exclusion.
_BAG_RIGHT = "?"
_OPTION_SHAPE_FIELDS = ("right", "strike", "expiry")
# Poison scopes for a row too corrupt to name its own bucket key (R-274).
_ANY = "*"
_ALL_UNUSABLE = "*|*"



def _is_unusable(key: str, unusable: set[str]) -> bool:
    """Whether ``key``'s contract is covered by any recorded poison scope."""
    if not unusable:
        return False
    return (
        key in unusable
        or _ALL_UNUSABLE in unusable
        or f"{key.split('|')[0]}|{_ANY}" in unusable
    )


def realized_pnl_by_exec_id(rows: Iterable[Any]) -> dict[str, float]:
    """Map each single-execution closing journal row to its realized P&L.

    ``rows`` are ``(payload, filled_at, written_at)`` journal rows for any
    number of tickers. Only option rows whose contract key both normalises
    and describes a contract that could exist take part; BAG envelopes
    (``right == "?"``) and stock rows are ignored, and anything else that
    fails either test poisons its ticker rather than being dropped. See the
    module header for the residual case this cannot see (R-320).
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
        if _is_unusable(key, unusable):
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
    if key is None or not _is_plausible_contract(payload):
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


def _is_option_shaped(payload: dict[str, Any]) -> bool:
    """Whether the row CLAIMS to describe an option contract.

    Shape, not normalisability. A row naming a strike, an expiry or a right
    is asserting it is an option fill even when one of those fields is
    malformed and ``_bucket_key`` therefore refuses it — that is the case
    R-274 is about. The three by-design non-options are excluded first: an
    explicitly declared non-option ``sec_type``, a BAG envelope (which marks
    itself with ``right == "?"``), and a stock row, which names none of the
    three fields at all.
    """
    sec_type = str(payload.get("sec_type") or payload.get("secType") or "").strip().upper()
    if sec_type in {"OPT", "FOP"}:
        return True
    if sec_type in {"STK", "BAG", "FUT", "CASH", "CFD", "BOND"}:
        return False
    if str(payload.get("right") or "").strip() == _BAG_RIGHT:
        return False
    # PRESENCE, not truthiness: a numeric 0 strike and an empty-string right
    # are both malformed VALUES of a field the row chose to name, which is
    # precisely the corruption case. Reading them as "field absent" routed
    # them to the stock exclusion. R-320.
    return any(field in payload for field in _OPTION_SHAPE_FIELDS)


def _is_plausible_contract(payload: dict[str, Any]) -> bool:
    """Whether a key that NORMALISES also describes a contract that can exist.

    ``_bucket_key`` only asks whether each field parses. A strike of ``0``
    parses to ``"0.0"`` and an expiry of ``20261340`` is eight digits, so
    both mint a well-formed key for a contract that cannot be listed — the
    row then forms its own phantom bucket, taking its quantity AND its cost
    out of the real one, and the replay prices the close against a basis
    that is short by exactly the missing open. R-320.

    ``right`` needs no check here: ``_normalize_right`` already collapses
    anything outside ``C``/``P`` to ``""``, which fails ``_bucket_key``.
    """
    strike = _normalize_strike(payload.get("strike"))
    try:
        if strike is None or float(strike) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if _normalize_right(payload.get("right")) not in {"C", "P"}:
        return False
    expiry = _normalize_expiry(payload.get("expiry"))
    try:
        date(int(expiry[0:4]), int(expiry[4:6]), int(expiry[6:8]))
    except ValueError:
        return False
    return True


def _unusable_fill_key(row: Any) -> Optional[str]:
    """Bucket key of a row that names a contract but is not a usable fill.

    ``None`` for the three rows the replay excludes BY DESIGN and whose
    exclusion keeps the inventory balanced: a BAG envelope, a stock leg, and
    a rehydrated ``CLOSED`` round trip, which carries no net quantity and
    whose P&L is realized elsewhere (T-124). Everything else — a missing or
    non-numeric ``contracts``/``fill_price``, an action ``_signed_qty`` does
    not recognise (R-198), or a contract field that does not normalise
    (R-274) — is a fill this contract's history is missing, and the contract
    must fall back to IB's own figure.

    A row whose contract fields are corrupt cannot name its own bucket, so it
    poisons by the widest scope it can still identify: ``TICKER|*`` when the
    ticker survived, ``*|*`` when nothing did. Over-poisoning costs an IB
    fallback; under-poisoning fabricates a P&L figure.
    """
    payload = _payload_from_row(row)
    if str(payload.get("action") or "").strip().upper() == "CLOSED":
        return None
    key = _bucket_key(payload)
    if key is not None and _is_plausible_contract(payload):
        return key
    if not _is_option_shaped(payload):
        return None
    ticker = _normalize_ticker(payload.get("ticker") or payload.get("symbol"))
    return f"{ticker}|{_ANY}" if ticker else _ALL_UNUSABLE


def _replay_contract(
    key: str, entries: list[dict[str, Any]], counted_parts: set[str]
) -> dict[str, float]:
    """Average-cost inventory replay; empty when the history is incomplete."""
    realized: dict[str, float] = {}
    position_qty = 0.0
    avg_basis_per_unit = 0.0
    seen_fingerprints: dict[tuple, set[str]] = {}
    basis_tainted = False
    position_may_be_short = False

    for entry in entries:
        if not _claim_exec_parts(counted_parts, entry["parts"], key):
            continue
        qty = entry["qty"]
        is_buy = entry["is_buy"]
        signed_qty = qty if is_buy else -qty
        same_direction = position_qty == 0 or (position_qty > 0) == is_buy

        if _already_journaled_under_other_namespace(seen_fingerprints, entry, key):
            # T-184: the fingerprint cannot separate a cross-writer duplicate
            # from two genuinely distinct equal-size same-day partials, so the
            # suppression is deliberate — under-count rather than double-count.
            # What it may have cost has to be carried forward: a suppressed
            # OPENING fill takes its cost out of the average along with its
            # quantity, so the basis is unreliable from here on; a suppressed
            # REDUCING fill leaves the per-unit basis intact but the position
            # possibly long, which only misprices figures once a later opening
            # fill blends against that phantom inventory.
            if same_direction:
                basis_tainted = True
            else:
                position_may_be_short = True
            continue

        cash = entry["notional"] + entry["commission"] if is_buy else entry["notional"] - entry["commission"]

        if entry["is_close"] and same_direction:
            logger.warning(
                "journal_realized: %s has a closing fill with no journaled open — "
                "journal incomplete, keeping IB realizedPNL",
                key,
            )
            return {}

        if same_direction:
            if position_may_be_short:
                basis_tainted = True
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
            if basis_tainted:
                logger.warning(
                    "journal_realized: %s withholding %s — a cross-writer fill "
                    "suppression left the replayed basis unreliable, journal "
                    "incomplete, keeping IB realizedPNL",
                    key, entry["parts"][0],
                )
                continue
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


"""Journal-derived open basis helpers for live portfolio sync."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Rows passed to the derivation layer use exactly these columns, in this order.
# Real libsql_experimental cursors return plain tuples, so name-based access
# must fall back to position or every row silently reads as empty (CTA-01,
# layer 2 — the .rows AttributeError was masking this). The paginated DB reader
# selects trade_id as a fourth cursor column and normalizes it back to this
# stable row shape before derivation.
_JOURNAL_COLUMNS = ("payload", "filled_at", "written_at")
_PAGED_JOURNAL_COLUMNS = ("trade_id", *_JOURNAL_COLUMNS)
_JOURNAL_PAGE_SIZE = 200


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if isinstance(row, (tuple, list)):
        try:
            return row[_JOURNAL_COLUMNS.index(key)]
        except (ValueError, IndexError):
            return None
    return getattr(row, key, None)


def _paged_row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if isinstance(row, (tuple, list)):
        try:
            return row[_PAGED_JOURNAL_COLUMNS.index(key)]
        except (ValueError, IndexError):
            return None
    return getattr(row, key, None)


def _payload_from_row(row: Any) -> dict[str, Any]:
    payload = _row_value(row, "payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_expiry(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 6:
        return f"20{digits}"
    if len(digits) == 8:
        return digits
    return ""


_ISO_EXPIRY = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_expiry_compact(value: Any) -> Any:
    """Return an ISO ``YYYY-MM-DD`` expiry as journal-canonical ``YYYYMMDD``.

    Journal option rows must carry compact expiries: the web lot-matcher
    (web/lib/blotter/fromJournal.ts) keys on the raw expiry string, so an
    ISO row silently splits a position into two non-matching groups
    (22 rows hand-normalized in prod on 2026-07-02). Anything that is not
    exactly ISO — already-compact strings, ``None``, futures contract
    months — passes through unchanged.
    """
    if isinstance(value, str) and _ISO_EXPIRY.fullmatch(value):
        return value.replace("-", "")
    return value


def _normalize_right(value: Any) -> str:
    right = str(value or "").strip().upper()
    return right[:1] if right[:1] in {"C", "P"} else ""


def _normalize_strike(value: Any) -> Optional[str]:
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return None


def _signed_qty(action: Any, qty: float) -> float:
    label = str(action or "").strip().upper()
    if qty <= 0:
        return 0.0
    if label.startswith("BUY"):
        return qty
    if label.startswith("SELL") or label.startswith("SHORT") or label == "CLOSED":
        return -qty
    return 0.0


def _bucket_key(payload: dict[str, Any]) -> Optional[str]:
    ticker = _normalize_ticker(payload.get("ticker") or payload.get("symbol"))
    expiry = _normalize_expiry(payload.get("expiry"))
    right = _normalize_right(payload.get("right"))
    strike = _normalize_strike(payload.get("strike"))
    if not ticker or not expiry or not right or strike is None:
        return None
    return f"{ticker}|{expiry}|{right}|{strike}"


def contract_fill_fingerprint(payload: dict[str, Any]) -> Optional[tuple]:
    """Id-namespace-independent identity of a single journaled fill.

    ``ib_exec_id`` is not comparable across writers: Flex rehydrate keys on
    the numeric Flex ``tradeID`` while the real-time daemon and
    ``executed_orders`` key on the IB API execId, so the same fill carries two
    unrelated ids (REL-024 / R-049). This fingerprint — contract, ET session
    date, signed quantity — is what both writers agree on.

    Returns None for rows the fingerprint cannot describe (missing contract or
    date, non-directional labels like ``CLOSED``); callers must treat None as
    "no fallback available" and fall back to exact id matching.
    """
    ticker = _normalize_ticker(payload.get("ticker") or payload.get("symbol"))
    date = str(payload.get("date") or "").strip()
    if not ticker or not date:
        return None

    try:
        qty = abs(float(payload.get("contracts") or payload.get("shares") or 0))
    except (TypeError, ValueError):
        return None
    signed = _signed_qty(payload.get("action"), qty)
    # `_signed_qty` maps CLOSED to a negative, but a CLOSED row is a round
    # trip, not one directional fill — it must not fingerprint-match a fill.
    if signed == 0 or str(payload.get("action") or "").strip().upper() == "CLOSED":
        return None

    contract = _bucket_key(payload) or f"{ticker}|STK"
    return (contract, date, signed)


def _exec_id_parts(payload: dict[str, Any]) -> list[str]:
    """The execution ids a journal row accounts for.

    ``journal_rehydrate`` collapses a contract's fills into ONE row whose
    ``ib_exec_id`` joins every constituent exec id with ``+``; the real-time
    writer appends one row PER fill. So a single row can stand for several
    executions.
    """
    raw = str(payload.get("ib_exec_id") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split("+") if part.strip()]


def _claim_exec_parts(counted: set[str], parts: list[str], contract: str) -> bool:
    """True when this row's quantity has NOT been accounted for yet.

    When both writers have covered the same fills the journal legitimately
    holds A, B and A+B for one contract — summing all three double-counts it
    (8 + 69 + 77 = 154 for a 77-contract position). Rows arrive oldest-first,
    so the first rows to cover an execution win and any later row that adds no
    new execution is skipped.

    A PARTIAL overlap (a composite covering one counted and one uncounted
    execution) cannot be split — the composite carries a single aggregate
    quantity — so it is skipped with a warning: under-counting is recoverable
    (``ib_sync`` rejects a journal basis whose net qty misses the position and
    keeps IB's avgCost) while double-counting silently inflates the basis.

    Rows with no ``ib_exec_id`` (legacy / manual entries) always count.
    """
    if not parts:
        return True
    fresh = [part for part in parts if part not in counted]
    if not fresh:
        return False
    if len(fresh) != len(parts):
        logger.warning(
            "journal_basis: %s skipped partially overlapping row %s — "
            "already counted %s and an aggregate quantity cannot be split",
            contract,
            "+".join(parts),
            "+".join(part for part in parts if part not in fresh),
        )
        return False
    counted.update(parts)
    return True


def _row_is_before_cutoff(row: Any, before: str) -> bool:
    timestamp = _row_value(row, "filled_at") or _row_value(row, "written_at")
    if not timestamp:
        return False
    return str(timestamp)[:10] < str(before)[:10]


def _fetch_journal_rows_for_tickers(db, tickers: Iterable[str]) -> list[Any]:
    """Fetch ticker history in bounded Hrana pages, then restore legacy order."""
    normalized_tickers = tuple(
        sorted({_normalize_ticker(ticker) for ticker in tickers if _normalize_ticker(ticker)})
    )
    if not normalized_tickers:
        return []

    placeholders = ", ".join("?" for _ in normalized_tickers)
    cursor = ""
    accumulated: list[tuple[str, tuple[Any, Any, Any]]] = []

    while True:
        result = db.execute(
            f"""
            SELECT trade_id, payload, filled_at, written_at
            FROM journal
            WHERE trade_id > ?
              AND UPPER(COALESCE(
                  json_extract(payload, '$.ticker'),
                  json_extract(payload, '$.symbol'),
                  ''
              )) IN ({placeholders})
            ORDER BY trade_id ASC
            LIMIT ?
            """,
            (cursor, *normalized_tickers, _JOURNAL_PAGE_SIZE),
        )
        page = list(result.fetchall())
        if not page:
            break

        for row in page:
            trade_id = str(_paged_row_value(row, "trade_id") or "")
            if not trade_id or trade_id <= cursor:
                raise RuntimeError("journal basis pagination returned a non-advancing trade_id")
            accumulated.append(
                (
                    trade_id,
                    (
                        _paged_row_value(row, "payload"),
                        _paged_row_value(row, "filled_at"),
                        _paged_row_value(row, "written_at"),
                    ),
                )
            )

        next_cursor = accumulated[-1][0]
        if next_cursor <= cursor:
            raise RuntimeError("journal basis pagination cursor did not advance")
        cursor = next_cursor
        if len(page) < _JOURNAL_PAGE_SIZE:
            break

    def sort_key(item: tuple[str, tuple[Any, Any, Any]]) -> tuple[Any, ...]:
        trade_id, row = item
        payload = _payload_from_row(row)
        ticker = _normalize_ticker(payload.get("ticker") or payload.get("symbol"))
        filled_at = _row_value(row, "filled_at")
        written_at = _row_value(row, "written_at")
        effective_at = filled_at if filled_at is not None else written_at

        # SQLite sorts NULL before text in ASC order. Preserve the old query's
        # ticker/effective-time/written-time semantics exactly, with trade_id as
        # a deterministic tie-breaker for rows whose timestamps are identical.
        return (
            ticker,
            effective_at is not None,
            str(effective_at or ""),
            written_at is not None,
            str(written_at or ""),
            trade_id,
        )

    accumulated.sort(key=sort_key)
    return [row for _trade_id, row in accumulated]


def _derive_journal_state_from_rows(
    rows: Iterable[Any],
    *,
    basis_tickers: Iterable[str] = (),
    option_net_keys: Iterable[str] = (),
    stock_net_tickers: Iterable[str] = (),
    before: Optional[str] = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Pure row processor shared by the single-target and batched readers."""
    normalized_basis_tickers = {
        _normalize_ticker(ticker) for ticker in basis_tickers if _normalize_ticker(ticker)
    }
    normalized_stock_tickers = {
        _normalize_ticker(ticker)
        for ticker in stock_net_tickers
        if _normalize_ticker(ticker)
    }
    normalized_option_keys = {str(key) for key in option_net_keys if str(key)}

    buckets: dict[str, dict[str, Any]] = {}
    basis_counted_parts: dict[str, set[str]] = {
        ticker: set() for ticker in normalized_basis_tickers
    }
    net_qty_lookup = {key: 0.0 for key in sorted(normalized_option_keys)}
    net_qty_lookup.update(
        {f"{ticker}|STK": 0.0 for ticker in sorted(normalized_stock_tickers)}
    )
    net_counted_parts = {key: set() for key in net_qty_lookup}

    for row in rows:
        payload = _payload_from_row(row)
        ticker = _normalize_ticker(payload.get("ticker") or payload.get("symbol"))
        if not ticker:
            continue

        key = _bucket_key(payload)
        qty_raw = payload.get("contracts")
        if qty_raw is None:
            qty_raw = payload.get("shares")
        try:
            qty = abs(float(qty_raw))
        except (TypeError, ValueError):
            continue

        signed_qty = _signed_qty(payload.get("action"), qty)
        if signed_qty == 0:
            continue
        exec_parts = _exec_id_parts(payload)

        if before is None or _row_is_before_cutoff(row, before):
            net_target: Optional[str] = None
            if key in normalized_option_keys:
                net_target = key
            elif (
                ticker in normalized_stock_tickers
                and payload.get("strike") is None
                and not payload.get("right")
            ):
                net_target = f"{ticker}|STK"

            if net_target is not None and _claim_exec_parts(
                net_counted_parts[net_target],
                exec_parts,
                ticker if net_target.endswith("|STK") else net_target,
            ):
                net_qty_lookup[net_target] += signed_qty

        if ticker not in normalized_basis_tickers or key is None:
            continue

        try:
            total_cost = float(payload.get("total_cost"))
        except (TypeError, ValueError):
            continue

        persisted_open_basis = payload.get("open_basis")
        try:
            persisted_open_basis = (
                float(persisted_open_basis)
                if persisted_open_basis is not None
                else None
            )
        except (TypeError, ValueError):
            persisted_open_basis = None

        if not _claim_exec_parts(basis_counted_parts[ticker], exec_parts, key):
            continue

        bucket = buckets.setdefault(
            key,
            {"net_qty": 0.0, "fills": [], "latest_persisted_open_basis": None},
        )
        bucket["net_qty"] += signed_qty
        bucket["fills"].append(
            {
                "signed_qty": signed_qty,
                "qty": qty,
                "total_cost": total_cost,
            }
        )
        if persisted_open_basis is not None:
            bucket["latest_persisted_open_basis"] = persisted_open_basis

    open_basis_lookup: dict[str, float] = {}
    for key, bucket in buckets.items():
        net_qty = float(bucket["net_qty"])
        if net_qty == 0:
            continue

        if bucket["latest_persisted_open_basis"] is not None:
            open_basis_lookup[key] = round(bucket["latest_persisted_open_basis"], 4)
            continue

        opening_sign = 1 if net_qty > 0 else -1
        opening_qty = 0.0
        opening_cost = 0.0
        for fill in bucket["fills"]:
            if (1 if fill["signed_qty"] > 0 else -1) != opening_sign:
                continue
            opening_qty += fill["qty"]
            opening_cost += fill["total_cost"]

        if opening_qty <= 0:
            continue

        avg_per_contract = opening_cost / opening_qty
        open_basis_lookup[key] = round(avg_per_contract * abs(net_qty), 4)

    return open_basis_lookup, net_qty_lookup


def compute_open_basis_and_net_qty_for_tickers(
    db,
    *,
    tickers: Iterable[str],
    contract_keys: Iterable[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Read current option tickers once and derive basis plus net quantities."""
    normalized_tickers = tuple(
        sorted({_normalize_ticker(ticker) for ticker in tickers if _normalize_ticker(ticker)})
    )
    normalized_contract_keys = tuple(sorted({str(key) for key in contract_keys if str(key)}))
    rows = _fetch_journal_rows_for_tickers(db, normalized_tickers)
    return _derive_journal_state_from_rows(
        rows,
        basis_tickers=normalized_tickers,
        option_net_keys=normalized_contract_keys,
    )


def prior_net_qty_for_contract(
    db,
    *,
    ticker: str,
    sec_type: str,
    strike: Any = None,
    right: Any = None,
    expiry: Any = None,
    before: Optional[str] = None,
) -> float:
    """Return signed net qty for one contract from already-imported journal rows.

    Used by the real-time fill writer to decide whether a SELL closes a long
    (label ``SELL_OPTION``) or opens a short (``SELL_TO_OPEN``). Sums signed
    qty across all matching rows: BUY → +, SELL/SHORT/CLOSED → −. Uses the
    same key normalisation as ``_bucket_key`` so the lookup lines up with
    the open-basis bucket math.

    STK rows are matched on ticker alone (strike/right/expiry ignored).
    OPT/BAG rows require all four to normalise to non-empty values.

    ``before`` bounds the scan for RETROACTIVE backfills (default ``None``
    keeps the live fill writer's unbounded behaviour). Only rows whose
    date — the first 10 chars of ``COALESCE(filled_at, written_at)`` — is
    STRICTLY EARLIER than ``before``'s date are counted. The journal's
    ``filled_at`` convention is date-only ("2026-06-25"), so a row dated
    the fill's own day carries no intra-day order: it is ambiguous and
    EXCLUDED by design (the 2026-07-02 incident needed prior-day rows in
    and same-day-and-later closing rows out; the backfill run sequences
    same-day fills through its in-run prior-state accumulation instead).
    Rows with no timestamp at all are likewise excluded when ``before``
    is given.
    """

    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        return 0.0

    sec_type_upper = (sec_type or "").upper()
    target_key: Optional[str]
    if sec_type_upper == "STK":
        target_key = None  # match all rows for ticker
    else:
        target_payload = {
            "ticker": normalized_ticker,
            "expiry": expiry,
            "right": right,
            "strike": strike,
        }
        target_key = _bucket_key(target_payload)
        if target_key is None:
            return 0.0

    rows = _fetch_journal_rows_for_tickers(db, (normalized_ticker,))
    if target_key is None:
        _, net_qty_lookup = _derive_journal_state_from_rows(
            rows,
            stock_net_tickers=(normalized_ticker,),
            before=before,
        )
        return net_qty_lookup[f"{normalized_ticker}|STK"]

    _, net_qty_lookup = _derive_journal_state_from_rows(
        rows,
        option_net_keys=(target_key,),
        before=before,
    )
    return net_qty_lookup[target_key]


def compute_open_basis_for_ticker(db, ticker: str) -> dict[str, float]:
    """Returns journal-derived open basis dollars keyed by contract.

    Output shape: ``{"TICKER|YYYYMMDD|R|STRIKE": open_basis_dollars}``.
    Returns an empty dict when the journal has no usable rows for the ticker
    or all matching contracts are fully closed.
    """

    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        return {}

    rows = _fetch_journal_rows_for_tickers(db, (normalized_ticker,))
    open_basis_lookup, _ = _derive_journal_state_from_rows(
        rows,
        basis_tickers=(normalized_ticker,),
    )
    return open_basis_lookup

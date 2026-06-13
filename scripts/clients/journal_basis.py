"""Journal-derived open basis helpers for live portfolio sync."""

from __future__ import annotations

import json
from typing import Any, Optional


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
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


def prior_net_qty_for_contract(
    db,
    *,
    ticker: str,
    sec_type: str,
    strike: Any = None,
    right: Any = None,
    expiry: Any = None,
) -> float:
    """Return signed net qty for one contract from already-imported journal rows.

    Used by the real-time fill writer to decide whether a SELL closes a long
    (label ``SELL_OPTION``) or opens a short (``SELL_TO_OPEN``). Sums signed
    qty across all matching rows: BUY → +, SELL/SHORT/CLOSED → −. Uses the
    same key normalisation as ``_bucket_key`` so the lookup lines up with
    the open-basis bucket math.

    STK rows are matched on ticker alone (strike/right/expiry ignored).
    OPT/BAG rows require all four to normalise to non-empty values.
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

    result = db.execute(
        """
        SELECT payload, filled_at, written_at
        FROM journal
        WHERE UPPER(COALESCE(
            json_extract(payload, '$.ticker'),
            json_extract(payload, '$.symbol'),
            ''
        )) = ?
        ORDER BY COALESCE(filled_at, written_at) ASC, written_at ASC
        """,
        (normalized_ticker,),
    )

    net_qty = 0.0
    for row in result.fetchall():
        payload = _payload_from_row(row)
        if _normalize_ticker(payload.get("ticker") or payload.get("symbol")) != normalized_ticker:
            continue

        if target_key is not None:
            if _bucket_key(payload) != target_key:
                continue
        else:
            # STK match: skip rows that look like options (have strike/right).
            if payload.get("strike") is not None or payload.get("right"):
                continue

        qty_raw = payload.get("contracts")
        if qty_raw is None:
            qty_raw = payload.get("shares")
        try:
            qty = abs(float(qty_raw))
        except (TypeError, ValueError):
            continue

        net_qty += _signed_qty(payload.get("action"), qty)

    return net_qty


def compute_open_basis_for_ticker(db, ticker: str) -> dict[str, float]:
    """Returns journal-derived open basis dollars keyed by contract.

    Output shape: ``{"TICKER|YYYYMMDD|R|STRIKE": open_basis_dollars}``.
    Returns an empty dict when the journal has no usable rows for the ticker
    or all matching contracts are fully closed.
    """

    normalized_ticker = _normalize_ticker(ticker)
    if not normalized_ticker:
        return {}

    result = db.execute(
        """
        SELECT payload, filled_at, written_at
        FROM journal
        WHERE UPPER(COALESCE(
            json_extract(payload, '$.ticker'),
            json_extract(payload, '$.symbol'),
            ''
        )) = ?
        ORDER BY COALESCE(filled_at, written_at) ASC, written_at ASC
        """,
        (normalized_ticker,),
    )

    buckets: dict[str, dict[str, Any]] = {}
    for row in result.fetchall():
        payload = _payload_from_row(row)
        if _normalize_ticker(payload.get("ticker") or payload.get("symbol")) != normalized_ticker:
            continue

        key = _bucket_key(payload)
        if key is None:
            continue

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

        try:
            total_cost = float(payload.get("total_cost"))
        except (TypeError, ValueError):
            continue

        # `open_basis` is persisted by journal_rehydrate.py on every row
        # as the basis allocated to the still-open residual after that
        # row. When present, the latest row's value supersedes the
        # bucket-level lot-match recomputation below — that's the whole
        # point of persisting it. Stays None for rows written by the
        # real-time daemon (journal_sync.py), which still need the
        # fallback compute path.
        persisted_open_basis = payload.get("open_basis")
        try:
            persisted_open_basis = (
                float(persisted_open_basis)
                if persisted_open_basis is not None
                else None
            )
        except (TypeError, ValueError):
            persisted_open_basis = None

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
        # Rows arrive ORDER BY filled_at ASC so the last write wins —
        # exactly what we want for "basis as of latest row".
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

    return open_basis_lookup

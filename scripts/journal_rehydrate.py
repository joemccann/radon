#!/usr/bin/env python3
"""
Journal Rehydrate — Backfill data/trade_log.json from the IB Flex Query.

Why this exists:
    ``ib_reconcile.py`` calls ``client.get_fills()`` which only returns the
    *current* socket session's executions (and a ~24h server-side window).
    If the daily reconcile cron skips a day, the gap silently grows until
    we drop trades on the floor. Flex Query is the durable source of truth
    (up to 365 days), so we use it to refill ``trade_log.json`` whenever the
    journal looks stale.

Behavior:
    - Pulls executions via :class:`scripts.trade_blotter.flex_query.FlexQueryFetcher`.
    - Groups executions by contract (per-symbol for stock, per
      symbol/strike/expiry/right for options).
    - Each appended trade carries a stable ``ib_exec_id`` that we dedupe
      against on the next run — append-only, idempotent.
    - Writes via :func:`scripts.utils.atomic_io.atomic_save` so the file is
      always crash-safe.
    - Emits a single JSON object on stdout — the FastAPI route forwards it
      back to the caller.

Failure mode:
    On Flex error / timeout, we abort BEFORE touching ``trade_log.json``
    and surface the error message in the JSON payload. Better a loud
    failure than a silent stale journal.

Usage:
    python3 journal_rehydrate.py [--days 365]

Output (stdout):
    {"imported": N, "skipped": M, "latest_date": "YYYY-MM-DD", "ok": true}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Make imports work whether invoked directly or via FastAPI's run_script().
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(SCRIPT_DIR / "trade_blotter") not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR / "trade_blotter"))

# Load .env so IB_FLEX_TOKEN / IB_FLEX_QUERY_ID resolve when run standalone.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    load_dotenv(PROJECT_ROOT / "web" / ".env")
except ImportError:
    pass

from utils.atomic_io import atomic_save, verified_load  # noqa: E402

DEFAULT_TRADE_LOG = PROJECT_ROOT / "data" / "trade_log.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decimal_to_float(value: Optional[Decimal]) -> float:
    """Cast a ``Decimal`` (or None) to a JSON-friendly float."""
    if value is None:
        return 0.0
    return float(value)


def _structure_label(side: str, sec_type: str, strike: Optional[float], right: Optional[str], expiry_iso: Optional[str]) -> str:
    """Mirror web/lib/journalSync.ts:resolveStructure() so labels match."""
    type_label = {
        "STK": "Stock",
        "OPT": "Option",
        "BAG": "Spread",
    }.get(sec_type, sec_type)
    side_label = "Long" if side == "BUY" else "Closed"

    if sec_type in ("OPT", "BAG") and strike and right:
        right_label = "Call" if right == "C" else "Put" if right == "P" else right
        suffix = f" {expiry_iso}" if expiry_iso else ""
        return f"{side_label} {right_label} ${int(strike) if float(strike).is_integer() else strike}{suffix}"

    return f"{side_label} {type_label} ({sec_type})"


def _expiry_to_iso(expiry: Optional[str]) -> Optional[str]:
    """Convert IB's compact ``YYYYMMDD`` expiry to ``YYYY-MM-DD``."""
    if not expiry:
        return None
    if len(expiry) == 8 and expiry.isdigit():
        return f"{expiry[0:4]}-{expiry[4:6]}-{expiry[6:8]}"
    return expiry


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_key(exec_obj: Any) -> str:
    """Build the dedupe / grouping key for an Execution.

    Stock fills group by symbol; option fills group by full contract.
    """
    sec_type = exec_obj.sec_type.value
    if sec_type == "STK":
        return f"{exec_obj.symbol}|{sec_type}"
    return f"{exec_obj.symbol}|{sec_type}|{exec_obj.strike}|{exec_obj.expiry}|{exec_obj.right}"


def _group_executions(executions: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Aggregate Execution objects into per-contract groups.

    Mirrors the bookkeeping ib_reconcile.py does, but built from Flex
    Query Execution objects rather than ib_insync Fill objects.
    """
    grouped: Dict[str, Dict[str, Any]] = {}

    for exec_obj in sorted(executions, key=lambda e: e.time):
        key = _group_key(exec_obj)
        bucket = grouped.setdefault(
            key,
            {
                "symbol": exec_obj.symbol,
                "sec_type": exec_obj.sec_type.value,
                "strike": float(exec_obj.strike) if exec_obj.strike else None,
                "expiry": exec_obj.expiry,
                "right": exec_obj.right,
                "executions": [],
                "exec_ids": [],
                "first_time": exec_obj.time,
                "buy_qty": Decimal(0),
                "sell_qty": Decimal(0),
                "buy_value": Decimal(0),
                "sell_value": Decimal(0),
                "total_commission": Decimal(0),
            },
        )

        bucket["executions"].append(exec_obj)
        bucket["exec_ids"].append(str(exec_obj.exec_id))

        if exec_obj.side.value == "BOT":
            bucket["buy_qty"] += exec_obj.quantity
            bucket["buy_value"] += exec_obj.quantity * exec_obj.price
        else:
            bucket["sell_qty"] += exec_obj.quantity
            bucket["sell_value"] += exec_obj.quantity * exec_obj.price

        bucket["total_commission"] += exec_obj.commission

    return grouped


def _resolve_action(bucket: Dict[str, Any], prior_qty: float = 0.0) -> Optional[str]:
    """Map (prior_qty, bucket_net, sec_type) → action label.

    Before 2026-05-22 this picked the label from `bucket_net` alone, which
    mislabeled SELLs that closed a prior long as ``SELL_TO_OPEN`` (the
    "open short" label). Consumers of the journal (``fromJournal.ts``
    treats ``SELL_TO_OPEN`` as opening and ``SELL_OPTION`` as closing) saw
    closed longs as new short positions. ``prior_qty`` is the running
    signed position for this contract before this bucket's fills are
    applied, derived from already-imported journal rows.
    """
    sec_type = bucket["sec_type"]
    net = bucket["buy_qty"] - bucket["sell_qty"]

    if net > 0:
        # Net buy. Whether the buys opened a new long or covered a prior
        # short, the consumer-side label is the same — BUY/BUY_OPTION
        # treats the row as adding to the long side. (Distinguishing
        # "buy to cover" would require a separate label that
        # fromJournal.ts doesn't recognise.)
        return "BUY" if sec_type == "STK" else "BUY_OPTION"
    if net < 0:
        # Net sell. If the position was long beforehand, this is a close
        # (SELL_OPTION). Otherwise it's opening / extending a short
        # (SELL_TO_OPEN). prior_qty > 0 → was long.
        if prior_qty > 0:
            return "SELL" if sec_type == "STK" else "SELL_OPTION"
        return "SELL" if sec_type == "STK" else "SELL_TO_OPEN"
    # Net flat — buy and sell sides match. Treat as a closed round-trip;
    # _compute_pnl_summary() does the lot matching that gives us the
    # realized P&L.
    if bucket["buy_qty"] > 0:
        return "CLOSED"
    return None


def _contract_key_from_trade(trade: Dict[str, Any]) -> Optional[str]:
    """Mirror ``_group_key`` for an existing journal row."""
    ticker = trade.get("ticker") or trade.get("symbol")
    if not ticker:
        return None
    structure = (trade.get("structure") or "").lower()
    # Heuristic: stock rows lack strike/right/expiry.
    if "stock" in structure or (trade.get("strike") is None and trade.get("right") is None and trade.get("expiry") is None):
        return f"{ticker}|STK"
    strike = trade.get("strike")
    expiry = trade.get("expiry")
    right = trade.get("right")
    sec_type = "BAG" if "spread" in structure or "combo" in structure else "OPT"
    return f"{ticker}|{sec_type}|{strike}|{expiry}|{right}"


def _prior_state_index(trades: List[Dict[str, Any]]) -> Dict[str, float]:
    """Build per-contract signed position from existing journal rows.

    Sums ±contracts/shares using the action label's sign (BUY → +, SELL/
    SHORT/CLOSED → -). The sign convention matches journal_basis.py's
    ``_signed_qty`` — both fall back to net-qty sign rather than trusting
    the OPEN/CLOSE semantic of the label itself, so this works even when
    older rows are mislabeled.
    """
    state: Dict[str, float] = {}
    for trade in sorted(trades, key=lambda t: str(t.get("date") or "")):
        key = _contract_key_from_trade(trade)
        if not key:
            continue
        action = str(trade.get("action") or "").upper()
        qty_raw = trade.get("contracts") or trade.get("shares") or 0
        try:
            qty = abs(float(qty_raw))
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        if action.startswith("BUY"):
            state[key] = state.get(key, 0.0) + qty
        elif action.startswith("SELL") or action.startswith("SHORT") or action == "CLOSED":
            state[key] = state.get(key, 0.0) - qty
    return state


def _bucket_contract_key(bucket: Dict[str, Any]) -> str:
    """Mirror ``_group_key`` for a bucket so prior_state lookups line up."""
    sec_type = bucket["sec_type"]
    if sec_type == "STK":
        return f"{bucket['symbol']}|{sec_type}"
    return f"{bucket['symbol']}|{sec_type}|{bucket['strike']}|{bucket['expiry']}|{bucket['right']}"


def _compute_pnl_summary(bucket: Dict[str, Any]) -> Dict[str, Decimal]:
    """Lot-match a bucket's executions to derive realized P&L + cost basis.

    Mirrors :class:`scripts.trade_blotter.models.Trade._inventory_summary`
    so a closed round-trip rehydrated from Flex carries the same
    ``realized_pnl`` / ``cost_basis`` / ``proceeds`` numbers the legacy
    Flex 1422766 blotter pipeline produced. Average-cost basis,
    direction-aware (handles both long and short round-trips),
    commission-aware on both sides.

    Returns a dict with Decimal entries keyed:
        ``realized_pnl``    — signed P&L from closed quantity (0 if no closes)
        ``realized_qty``    — total contracts/shares closed
        ``cost_basis``      — sum of buy.notional + buy.commission
        ``proceeds``        — sum of sell.notional - sell.commission
        ``open_basis``      — basis allocated to the still-open residual

    Applies uniformly to STK / OPT / BAG; multiplier is the only thing
    that differs and that lives on the Execution itself.
    """
    sec_type = bucket["sec_type"]
    multiplier = Decimal(100) if sec_type in ("OPT", "BAG") else Decimal(1)

    position_qty = Decimal(0)            # signed: long positive, short negative
    avg_basis_per_unit = Decimal(0)      # already includes opening commissions
    realized_qty = Decimal(0)
    realized_pnl = Decimal(0)
    cost_basis = Decimal(0)
    proceeds = Decimal(0)

    for exec_obj in sorted(bucket["executions"], key=lambda e: e.time):
        qty = exec_obj.quantity
        if qty <= 0:
            continue
        notional = qty * exec_obj.price * multiplier
        commission = exec_obj.commission
        is_buy = exec_obj.side.value == "BOT"

        if is_buy:
            cost_basis += notional + commission
            opening_total = notional + commission
        else:
            proceeds += notional - commission
            opening_total = notional - commission

        signed_qty = qty if is_buy else -qty
        same_direction = (
            position_qty == 0
            or (position_qty > 0 and signed_qty > 0)
            or (position_qty < 0 and signed_qty < 0)
        )

        if same_direction:
            current_basis = avg_basis_per_unit * abs(position_qty)
            position_qty += signed_qty
            avg_basis_per_unit = (
                (current_basis + opening_total) / abs(position_qty)
                if position_qty != 0
                else Decimal(0)
            )
            continue

        close_qty = min(abs(position_qty), qty)
        if close_qty > 0:
            basis_closed = avg_basis_per_unit * close_qty
            realized_qty += close_qty

            if position_qty > 0 and not is_buy:
                close_value_per_unit = (notional - commission) / qty
                realized_pnl += close_value_per_unit * close_qty - basis_closed
            elif position_qty < 0 and is_buy:
                cover_cost_per_unit = (notional + commission) / qty
                realized_pnl += basis_closed - cover_cost_per_unit * close_qty

            remaining_qty = abs(position_qty) - close_qty
            position_qty = (
                (Decimal(1) if position_qty > 0 else Decimal(-1)) * remaining_qty
                if remaining_qty > 0
                else Decimal(0)
            )
            if position_qty == 0:
                avg_basis_per_unit = Decimal(0)

        residual = qty - close_qty
        if residual > 0:
            if is_buy:
                position_qty = residual
                avg_basis_per_unit = (notional + commission) / qty
            else:
                position_qty = -residual
                avg_basis_per_unit = (notional - commission) / qty

    open_basis = avg_basis_per_unit * abs(position_qty)
    return {
        "realized_pnl": realized_pnl,
        "realized_qty": realized_qty,
        "cost_basis": cost_basis,
        "proceeds": proceeds,
        "open_basis": open_basis,
    }


def _composite_exec_id(exec_ids: List[str]) -> str:
    """Stable ``ib_exec_id`` for a contract group.

    Multi-leg combos and multi-fill orders need an identifier that
    persists across re-runs. We sort the underlying Flex execIds and
    join — order-independent + collision-free.
    """
    if len(exec_ids) == 1:
        return exec_ids[0]
    return "+".join(sorted(set(exec_ids)))


def _bucket_to_entry(
    bucket: Dict[str, Any],
    next_id: int,
    prior_qty: float = 0.0,
) -> Dict[str, Any]:
    """Build the trade_log.json row for one grouped contract.

    ``prior_qty`` is the contract's signed position before this bucket's
    fills — used by ``_resolve_action`` to distinguish closing-long from
    opening-short sells.
    """
    sec_type = bucket["sec_type"]
    net_qty = bucket["buy_qty"] - bucket["sell_qty"]
    abs_qty = abs(int(net_qty)) if net_qty != 0 else int(bucket["buy_qty"])

    total_qty = bucket["buy_qty"] + bucket["sell_qty"]
    avg_price = (
        float((bucket["buy_value"] + bucket["sell_value"]) / total_qty)
        if total_qty > 0
        else 0.0
    )

    multiplier = 100 if sec_type in ("OPT", "BAG") else 1
    total_cost = abs_qty * avg_price * multiplier + _decimal_to_float(bucket["total_commission"])
    expiry_iso = _expiry_to_iso(bucket["expiry"])

    action = _resolve_action(bucket, prior_qty=prior_qty)
    if action is None:
        return {}

    side = "BUY" if action in ("BUY", "BUY_OPTION") else "SELL"
    structure = _structure_label(side, sec_type, bucket["strike"], bucket["right"], expiry_iso)

    pnl = _compute_pnl_summary(bucket)
    round_trip_quantity = int(max(bucket["buy_qty"], bucket["sell_qty"]))

    entry: Dict[str, Any] = {
        "id": next_id,
        "date": bucket["first_time"].strftime("%Y-%m-%d"),
        "ticker": bucket["symbol"],
        "structure": structure,
        "decision": "IB_AUTO_IMPORT",
        "action": action,
        "fill_price": round(avg_price, 4),
        "total_cost": round(total_cost, 4),
        "commission": round(_decimal_to_float(bucket["total_commission"]), 4),
        "ib_exec_id": _composite_exec_id(bucket["exec_ids"]),
        "notes": f"Rehydrated from IB Flex Query on {datetime.now().strftime('%Y-%m-%d')}",
        # Lot-matched P&L breakdown (equivalent to scripts/trade_blotter
        # /models.py:Trade._inventory_summary). Persisted on every row,
        # closed or open, so the journal-derived blotter (web/lib
        # /blotter/fromJournal.ts) doesn't have to reconstruct lots from
        # row-level totals it doesn't have access to.
        "cost_basis": round(_decimal_to_float(pnl["cost_basis"]), 4),
        "proceeds": round(_decimal_to_float(pnl["proceeds"]), 4),
        "realized_pnl": round(_decimal_to_float(pnl["realized_pnl"]), 4),
        "realized_quantity": int(pnl["realized_qty"]),
        # Basis allocated to the still-open residual after this bucket.
        # Lets scripts/clients/journal_basis.py read a persisted value
        # instead of re-running the lot matcher on every ib_sync.
        "open_basis": round(_decimal_to_float(pnl["open_basis"]), 4),
        "total_round_trip_quantity": round_trip_quantity,
    }

    if sec_type in ("OPT", "BAG"):
        entry["contracts"] = abs_qty
        if bucket["strike"]:
            entry["strike"] = bucket["strike"]
        if bucket["right"]:
            entry["right"] = bucket["right"]
        if bucket["expiry"]:
            entry["expiry"] = bucket["expiry"]
    else:
        entry["shares"] = abs_qty

    return entry


# ---------------------------------------------------------------------------
# Existing-trade index
# ---------------------------------------------------------------------------


def _existing_exec_ids(trades: List[Dict[str, Any]]) -> set[str]:
    """Collect every ib_exec_id (and split-composite parts) we already have."""
    ids: set[str] = set()
    for trade in trades:
        exec_id = trade.get("ib_exec_id")
        if not exec_id:
            continue
        ids.add(str(exec_id))
        for part in str(exec_id).split("+"):
            if part:
                ids.add(part)
    return ids


def _existing_legacy_keys(trades: List[Dict[str, Any]]) -> set[Tuple[str, str, str]]:
    """Fallback fingerprint for rows imported before ib_exec_id existed.

    Tuple: (ticker, date, structure). Coarser than the Flex execId, but
    enough to keep the pre-rehydrate corpus from being re-appended.
    """
    keys: set[Tuple[str, str, str]] = set()
    for trade in trades:
        ticker = trade.get("ticker")
        date = trade.get("date") or trade.get("close_date")
        structure = trade.get("structure", "")
        if ticker and date:
            keys.add((str(ticker), str(date), str(structure)))
    return keys


def _is_duplicate(
    entry: Dict[str, Any],
    existing_exec_ids: set[str],
    legacy_keys: set[Tuple[str, str, str]],
) -> bool:
    """Prefer execId match, else fall back to (ticker, date, structure)."""
    exec_id = entry.get("ib_exec_id")
    if exec_id and str(exec_id) in existing_exec_ids:
        return True
    if exec_id:
        for part in str(exec_id).split("+"):
            if part and part in existing_exec_ids:
                return True

    legacy_key = (entry["ticker"], entry["date"], entry.get("structure", ""))
    return legacy_key in legacy_keys


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def rehydrate_from_executions(
    executions: List[Any],
    existing: Dict[str, Any],
) -> Tuple[Dict[str, Any], int, int, Optional[str]]:
    """Pure function: merge Flex executions into the existing trade log.

    Args:
        executions: List of ``Execution`` objects from Flex Query.
        existing: Loaded ``trade_log.json`` payload (``{"trades": [...]}``).

    Returns:
        (updated payload, imported count, skipped count, latest date)
    """
    trades = list(existing.get("trades", []))
    exec_ids = _existing_exec_ids(trades)
    legacy_keys = _existing_legacy_keys(trades)
    next_id = max((t.get("id", 0) for t in trades), default=0) + 1

    # Pre-compute per-contract running qty from already-imported rows so
    # this rehydrate batch can label closing sells as SELL_OPTION rather
    # than SELL_TO_OPEN. Trades older than the Flex window contribute
    # too — that's the entire point.
    prior_state = _prior_state_index(trades)

    grouped = _group_executions(executions)
    candidate_entries: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        key = _bucket_contract_key(bucket)
        prior_qty = prior_state.get(key, 0.0)
        entry = _bucket_to_entry(bucket, next_id, prior_qty=prior_qty)
        if not entry:
            continue
        candidate_entries.append(entry)

    candidate_entries.sort(key=lambda e: (e["date"], e["ticker"]))

    imported = 0
    skipped = 0
    for entry in candidate_entries:
        if _is_duplicate(entry, exec_ids, legacy_keys):
            skipped += 1
            continue
        entry["id"] = next_id
        next_id += 1
        trades.append(entry)
        exec_ids.add(str(entry["ib_exec_id"]))
        legacy_keys.add((entry["ticker"], entry["date"], entry.get("structure", "")))
        imported += 1

    latest_date = max((t.get("date") for t in trades if t.get("date")), default=None)

    return {"trades": trades}, imported, skipped, latest_date


def rehydrate(
    days: int = 365,
    trade_log_path: Path = DEFAULT_TRADE_LOG,
    fetcher: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the full rehydrate cycle and persist the result atomically.

    Args:
        days: Lookback window for the Flex Query.
        trade_log_path: Override path (used by tests).
        fetcher: Optional pre-built FlexQueryFetcher for tests/mocking.
    """
    if fetcher is None:
        token = os.environ.get("IB_FLEX_TOKEN")
        query_id = os.environ.get("IB_FLEX_QUERY_ID")
        if not token or not query_id:
            return {
                "ok": False,
                "imported": 0,
                "skipped": 0,
                "error": "IB_FLEX_TOKEN / IB_FLEX_QUERY_ID not configured",
            }
        from trade_blotter.flex_query import FlexQueryFetcher  # local import keeps test-time mocking simple

        fetcher = FlexQueryFetcher(token=token, query_id=query_id)

    try:
        executions = fetcher.fetch_executions(days_back=days)
    except Exception as exc:  # noqa: BLE001 — surface every failure
        return {
            "ok": False,
            "imported": 0,
            "skipped": 0,
            "error": f"Flex Query failed: {exc}",
        }

    try:
        existing = verified_load(str(trade_log_path))
    except FileNotFoundError:
        existing = {"trades": []}
    except (ValueError, json.JSONDecodeError) as exc:
        # Fall back to non-verified read so a missing _checksum doesn't
        # block rehydrate. Atomic save below will add one.
        try:
            with open(trade_log_path, "r") as fh:
                existing = json.load(fh)
        except Exception:
            return {
                "ok": False,
                "imported": 0,
                "skipped": 0,
                "error": f"Failed to read trade_log.json: {exc}",
            }

    if "trades" not in existing or not isinstance(existing["trades"], list):
        existing = {"trades": []}

    updated, imported, skipped, latest_date = rehydrate_from_executions(executions, existing)

    if imported > 0:
        atomic_save(str(trade_log_path), updated)

    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "latest_date": latest_date,
        "executions_seen": len(executions),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=365, help="Flex Query lookback window")
    parser.add_argument(
        "--trade-log",
        type=Path,
        default=DEFAULT_TRADE_LOG,
        help="Override trade_log.json path (testing)",
    )
    args = parser.parse_args()

    result = rehydrate(days=args.days, trade_log_path=args.trade_log)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

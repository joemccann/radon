"""Canonical Turso readers for standalone Python scripts.

These helpers keep source-of-truth reads out of flat files. They intentionally
return plain Python payloads because the surrounding scripts still operate on
the legacy JSON-shaped dictionaries.
"""

from __future__ import annotations

import json
from typing import Any, Optional

try:
    from .client import get_db
except ImportError:  # pragma: no cover
    from db.client import get_db  # type: ignore[no-redef]


def _db(db: Optional[Any] = None) -> Any:
    return db if db is not None else get_db()


def _cell(row: Any, idx: int, name: str | None = None) -> Any:
    if isinstance(row, dict):
        if name and name in row:
            return row[name]
        values = list(row.values())
        return values[idx] if idx < len(values) else None
    return row[idx]


def _json_payload(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _date_part(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else None


def _normalize_expiry(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _float_key(value: Any) -> Optional[str]:
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return None


def read_latest_portfolio_snapshot(db: Optional[Any] = None) -> Optional[dict[str, Any]]:
    rows = _db(db).execute(
        "SELECT payload FROM portfolio_snapshots ORDER BY taken_at DESC LIMIT 1"
    ).fetchall()
    if not rows:
        return None
    return _json_payload(_cell(rows[0], 0, "payload"))


def read_portfolio_positions(db: Optional[Any] = None) -> list[dict[str, Any]]:
    portfolio = read_latest_portfolio_snapshot(db)
    positions = portfolio.get("positions") if portfolio else None
    return [p for p in positions if isinstance(p, dict)] if isinstance(positions, list) else []


def read_open_orders(db: Optional[Any] = None) -> list[dict[str, Any]]:
    rows = _db(db).execute(
        "SELECT payload FROM open_orders ORDER BY updated_at DESC"
    ).fetchall()
    orders: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_payload(_cell(row, 0, "payload"))
        if payload is not None:
            orders.append(payload)
    return orders


def read_executed_orders(db: Optional[Any] = None) -> list[dict[str, Any]]:
    rows = _db(db).execute(
        """
        SELECT exec_id, payload, fill_time
        FROM executed_orders
        ORDER BY fill_time ASC, exec_id ASC
        """
    ).fetchall()
    orders: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_payload(_cell(row, 1, "payload"))
        if payload is None:
            continue
        orders.append(
            {
                "exec_id": _cell(row, 0, "exec_id"),
                "payload": payload,
                "fill_time": _cell(row, 2, "fill_time"),
            }
        )
    return orders


def read_journal_trades(db: Optional[Any] = None) -> list[dict[str, Any]]:
    rows = _db(db).execute(
        """
        SELECT payload
        FROM journal
        ORDER BY COALESCE(filled_at, written_at) ASC, trade_id ASC
        """
    ).fetchall()
    trades: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_payload(_cell(row, 0, "payload"))
        if payload is not None:
            trades.append(payload)
    return trades


def read_next_journal_numeric_id(db: Optional[Any] = None) -> int:
    next_id = 1
    for trade in read_journal_trades(db):
        try:
            next_id = max(next_id, int(trade.get("id", 0)) + 1)
        except (TypeError, ValueError):
            continue
    return next_id


def read_journal_entry_date_maps(
    db: Optional[Any] = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return `(structure_dates, contract_dates)` derived from journal rows.

    `structure_dates` is keyed by `TICKER` and `TICKER|structure`, with later
    rows overwriting earlier rows to match the legacy trade-log behavior.
    `contract_dates` is keyed by `TICKER|YYYY-MM-DD|R|strike` and keeps the
    earliest matching fill date, preserving the existing per-contract entry-date
    invariant for multi-leg options.
    """
    rows = _db(db).execute(
        """
        SELECT payload, filled_at
        FROM journal
        ORDER BY COALESCE(filled_at, written_at) ASC, trade_id ASC
        """
    ).fetchall()
    structure_dates: dict[str, str] = {}
    contract_dates: dict[str, str] = {}

    for row in rows:
        payload = _json_payload(_cell(row, 0, "payload"))
        if payload is None:
            continue
        filled_at = _cell(row, 1, "filled_at")
        ticker = str(payload.get("ticker") or payload.get("symbol") or "").strip().upper()
        date = (
            _date_part(payload.get("date"))
            or _date_part(payload.get("filled_at"))
            or _date_part(payload.get("time"))
            or _date_part(filled_at)
        )
        if not ticker or not date:
            continue

        structure = str(payload.get("structure") or "").strip()
        structure_dates[ticker] = date
        if structure:
            structure_dates[f"{ticker}|{structure}"] = date

        contract = payload.get("contract")
        contract = contract if isinstance(contract, dict) else {}
        right = str(payload.get("right") or contract.get("right") or "").strip().upper()[:1]
        expiry = _normalize_expiry(
            payload.get("expiry")
            or payload.get("expiration")
            or contract.get("expiry")
            or contract.get("lastTradeDateOrContractMonth")
        )
        strike = _float_key(payload.get("strike") or contract.get("strike"))
        if right and expiry and strike:
            key = f"{ticker}|{expiry}|{right}|{strike}"
            if key not in contract_dates or date < contract_dates[key]:
                contract_dates[key] = date

    return structure_dates, contract_dates


def read_watchlist_tickers(db: Optional[Any] = None) -> list[str]:
    rows = _db(db).execute("SELECT ticker FROM watchlist ORDER BY ticker ASC").fetchall()
    tickers = []
    for row in rows:
        ticker = str(_cell(row, 0, "ticker") or "").strip().upper()
        if ticker:
            tickers.append(ticker)
    return tickers


def read_watchlist_items(db: Optional[Any] = None) -> list[dict[str, Any]]:
    rows = _db(db).execute(
        "SELECT ticker, sector, source, payload FROM watchlist ORDER BY ticker ASC"
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(_cell(row, 0, "ticker") or "").strip().upper()
        if not ticker:
            continue
        sector = _cell(row, 1, "sector")
        source = _cell(row, 2, "source")
        payload = _json_payload(_cell(row, 3, "payload")) or {}
        item = {**payload, "ticker": ticker}
        if "sector" not in item and sector:
            item["sector"] = sector
        if "source" not in item and source:
            item["source"] = source
        items.append(item)
    return items

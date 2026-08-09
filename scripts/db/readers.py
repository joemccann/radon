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


# Effective-time ordering for journal reads. Must stay textually identical to
# the idx_journal_effective_at expression (migration 0025) or the reads regress
# to a full scan + filesort that grows with trade history.
JOURNAL_EFFECTIVE_ORDER_SQL = "COALESCE(filled_at, written_at) ASC, trade_id ASC"


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


def read_active_position_return_capital(db: Optional[Any] = None) -> list[dict[str, Any]]:
    """Read active v2 bases whose observation covers the latest event.

    Missing migrations, closed episodes, stale observations, and malformed
    evidence all fail closed by returning no basis for that instance.
    """
    try:
        target = _db(db)
        instance_rows = target.execute(
            """
            SELECT instance_id, account_id
            FROM position_instances
            """
        ).fetchall()
        event_rows = target.execute(
            """
            SELECT event_id, instance_id, effective_at, after_legs
            FROM position_instance_events
            ORDER BY effective_at ASC, event_id ASC
            """
        ).fetchall()
        observation_rows = target.execute(
            """
            SELECT observation_id, instance_id, through_event_id, amount,
                   currency, evidence, recorded_at
            FROM position_capital_observations
            WHERE status = 'VALID' AND quality = 'observed' AND amount > 0
            ORDER BY recorded_at ASC, observation_id ASC
            """
        ).fetchall()
        execution_rows = target.execute(
            "SELECT account_id, exec_id FROM position_execution_facts"
        ).fetchall()
    except Exception:  # noqa: BLE001 — table may not exist pre-migration
        return []

    accounts = {
        str(_cell(row, 0, "instance_id")): str(_cell(row, 1, "account_id") or "")
        for row in instance_rows or []
    }
    latest_events: dict[str, dict[str, Any]] = {}
    for row in event_rows or []:
        instance_id = str(_cell(row, 1, "instance_id") or "")
        try:
            legs = json.loads(_cell(row, 3, "after_legs") or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        latest_events[instance_id] = {
            "event_id": str(_cell(row, 0, "event_id") or ""),
            "effective_at": str(_cell(row, 2, "effective_at") or ""),
            "legs": legs if isinstance(legs, dict) else {},
        }

    latest_observations: dict[str, dict[str, Any]] = {}
    for row in observation_rows or []:
        instance_id = str(_cell(row, 1, "instance_id") or "")
        try:
            evidence = json.loads(_cell(row, 5, "evidence") or "{}")
            amount = float(_cell(row, 3, "amount"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(evidence, dict) or amount <= 0:
            continue
        latest_observations[instance_id] = {
            "observation_id": str(_cell(row, 0, "observation_id") or ""),
            "through_event_id": str(_cell(row, 2, "through_event_id") or ""),
            "amount": amount,
            "currency": str(_cell(row, 4, "currency") or ""),
            "evidence": evidence,
        }

    correction_counts: dict[tuple[str, str], int] = {}
    for row in execution_rows or []:
        account_id = str(_cell(row, 0, "account_id") or "")
        exec_id = str(_cell(row, 1, "exec_id") or "")
        root, separator, _suffix = exec_id.rpartition(".")
        correction_root = root if separator else exec_id
        key = (account_id, correction_root)
        correction_counts[key] = correction_counts.get(key, 0) + 1

    try:
        from ..position_return_capital import build_return_capital_payload
    except ImportError:  # pragma: no cover - flat scripts import mode
        from position_return_capital import build_return_capital_payload  # type: ignore[no-redef]

    result: list[dict[str, Any]] = []
    for instance_id, event in latest_events.items():
        if not event["legs"]:
            continue
        observation = latest_observations.get(instance_id)
        if not observation or observation["through_event_id"] != event["event_id"]:
            continue
        evidence = observation["evidence"]
        account_id = accounts.get(instance_id) or ""
        if any(
            correction_counts.get(
                (account_id, exec_id.rpartition(".")[0] if "." in exec_id else exec_id),
                0,
            ) > 1
            for exec_id in evidence.get("exec_ids", [])
            if isinstance(exec_id, str)
        ):
            continue
        payload = build_return_capital_payload(
            amount=observation["amount"],
            instance={
                "instance_id": instance_id,
                "account_id": accounts.get(instance_id),
                "legs": event["legs"],
            },
            observation={
                **evidence,
                "observation_id": observation["observation_id"],
                "currency": observation["currency"],
            },
        )
        if payload is None:
            continue
        result.append({
            "instance_id": instance_id,
            "account_id": accounts.get(instance_id),
            "legs": event["legs"],
            "payload": payload,
        })
    return result



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
        f"SELECT payload FROM journal ORDER BY {JOURNAL_EFFECTIVE_ORDER_SQL}"
    ).fetchall()
    trades: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_payload(_cell(row, 0, "payload"))
        if payload is not None:
            trades.append(payload)
    return trades


def read_next_journal_numeric_id(db: Optional[Any] = None) -> int:
    """Next numeric journal id as a SQL aggregate — never a full payload read.

    CAST mirrors the legacy Python semantics: non-numeric ids collapse to 0
    and are ignored by MAX; an empty journal yields 1.
    """
    rows = _db(db).execute(
        "SELECT MAX(CAST(json_extract(payload, '$.id') AS INTEGER)) FROM journal"
    ).fetchall()
    max_id = _cell(rows[0], 0) if rows else None
    try:
        return max(1, int(max_id) + 1) if max_id is not None else 1
    except (TypeError, ValueError):
        return 1


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
        f"SELECT payload, filled_at FROM journal ORDER BY {JOURNAL_EFFECTIVE_ORDER_SQL}"
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


# ── Performance TWR (plan §4.2) ──────────────────────────────────────────────


def _is_missing_table_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "no such table" in msg and any(
        token in msg for token in ("nav_snapshots", "external_flows", "twr_subperiods")
    )


def read_nav_snapshots(
    db: Optional[Any] = None,
    *,
    account_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Read NAV snapshots ordered by report_date ASC.

    Filters by account_id when given; otherwise returns all accounts.
    Returns [] if the table hasn't been migrated yet (first-build before 0035).
    """
    try:
        if account_id is not None:
            sql = "SELECT account_id, report_date, total_net_liq, cash, stock, options, accrued_fees FROM nav_snapshots WHERE account_id = ? ORDER BY report_date ASC"
            args: tuple[Any, ...] = (account_id,)
            if limit is not None:
                sql += " LIMIT ?"
                args = (account_id, int(limit))
            rows = _db(db).execute(sql, args).fetchall()
        elif limit is not None:
            rows = _db(db).execute(
                "SELECT account_id, report_date, total_net_liq, cash, stock, options, accrued_fees FROM nav_snapshots ORDER BY report_date ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = _db(db).execute(
                "SELECT account_id, report_date, total_net_liq, cash, stock, options, accrued_fees FROM nav_snapshots ORDER BY report_date ASC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — table may not exist before migration 0035
        if _is_missing_table_error(exc):
            return []
        raise
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "account_id": _cell(row, 0, "account_id"),
                "report_date": _cell(row, 1, "report_date"),
                "total_net_liq": _cell(row, 2, "total_net_liq"),
                "cash": _cell(row, 3, "cash"),
                "stock": _cell(row, 4, "stock"),
                "options": _cell(row, 5, "options"),
                "accrued_fees": _cell(row, 6, "accrued_fees"),
            }
        )
    return result


def read_external_flows(
    db: Optional[Any] = None,
    *,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Read external flows ordered by report_date ASC. Returns [] if table missing."""
    try:
        if account_id is not None:
            rows = _db(db).execute(
                "SELECT account_id, report_date, amount, flow_type, note FROM external_flows WHERE account_id = ? ORDER BY report_date ASC, flow_type ASC",
                (account_id,),
            ).fetchall()
        else:
            rows = _db(db).execute(
                "SELECT account_id, report_date, amount, flow_type, note FROM external_flows ORDER BY report_date ASC, account_id ASC, flow_type ASC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_error(exc):
            return []
        raise
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "account_id": _cell(row, 0, "account_id"),
                "report_date": _cell(row, 1, "report_date"),
                "amount": _cell(row, 2, "amount"),
                "flow_type": _cell(row, 3, "flow_type"),
                "note": _cell(row, 4, "note"),
            }
        )
    return result


def read_twr_subperiods(
    db: Optional[Any] = None,
    *,
    account_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Read TWR subperiods ordered by report_date ASC. Returns [] if table missing."""
    try:
        if account_id is not None:
            rows = _db(db).execute(
                "SELECT account_id, report_date, b, e, c, r, cum_r FROM twr_subperiods WHERE account_id = ? ORDER BY report_date ASC",
                (account_id,),
            ).fetchall()
        else:
            rows = _db(db).execute(
                "SELECT account_id, report_date, b, e, c, r, cum_r FROM twr_subperiods ORDER BY report_date ASC, account_id ASC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        if _is_missing_table_error(exc):
            return []
        raise
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "account_id": _cell(row, 0, "account_id"),
                "report_date": _cell(row, 1, "report_date"),
                "b": _cell(row, 2, "b"),
                "e": _cell(row, 3, "e"),
                "c": _cell(row, 4, "c"),
                "r": _cell(row, 5, "r"),
                "cum_r": _cell(row, 6, "cum_r"),
            }
        )
    return result

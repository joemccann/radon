#!/usr/bin/env python3
"""
Expiry Sweep Handler — post-expiry journal close rows.

Incident 2026-08-09 (SPCX 25x $150C expired worthless Fri 2026-08-07):
expiration emits no execution, so no fill-driven path (fill_monitor,
journal_sync, evening execution sweep) ever closed the journal opener —
the position stayed "open" forever and downstream consumers (position
reconcile, blotter isOpen stats) paged on it.

This handler runs once daily, finds option contracts whose journal net
quantity is still non-zero past their expiry, and writes a deterministic
$0.00 close row with ``ib_codes: "Ep"`` (the IB Flex expired code —
labelling convention in journal_rehydrate.py) under a synthetic exec id.

Key design decisions:
  - Pure Turso read/write — does NOT require IB Gateway.
  - SETTLE_WINDOW_DAYS grace before sweeping: assignment/exercise fills
    and their Flex records can trail the expiry by a session.
  - Guards: any journal or executed_orders activity for the contract
    dated strictly AFTER the expiry (assignment/exercise trace) skips
    the candidate — never overwrite a real settlement path.
  - Idempotent by construction: the synthetic close row zeroes the net
    on the next pass, and a present ep- row blocks a second write even
    when the net is still non-zero (belt-and-suspenders).
  - Heartbeats ok on EVERY cycle with {candidates, closed, skipped_guarded}.
  - Raises on DB unavailability / write failure so BaseHandler does not
    latch last_run (feedback_dont_latch_last_run_on_soft_failure).

Registration: web/lib/serviceHealthWindows.ts and
scripts/watchdog/services.py both carry "journal-expiry-sweep" as a
daily scheduled entry with requires_ib=false.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base import BaseHandler

try:
    from db.writer import upsert_journal_entry  # type: ignore
except Exception:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")

# Calendar days past expiry before a contract becomes a sweep candidate —
# assignment/exercise executions and Flex records can trail by a session.
SETTLE_WINDOW_DAYS = 2

# Per-cycle write cap (Hrana write bounding — a backlog drains over days).
MAX_CLOSES_PER_CYCLE = 20

# Journal look-back window. Listed options live at most ~3 years (LEAPS),
# so this bounds the read (Turso Hrana I/O bounding, scripts/CLAUDE.md)
# while still seeing every opener of a possibly-expired contract.
JOURNAL_LOOKBACK_DAYS = 1100

# Signed contribution of each option action to the contract's net quantity.
_ACTION_SIGNS = {
    "BUY_OPTION": 1,
    "BUY_TO_CLOSE": 1,
    "SELL_OPTION": -1,
    "SELL_TO_OPEN": -1,
}

# Flex rehydrate writes completed round trips as a single row — both sides
# already netted, so it contributes nothing to the open quantity.
_ROUND_TRIP_ACTION = "CLOSED"

# OCC option symbol as the rehydrate writer stores it in ticker
# ("SPCX  260807C00150000"); the real-time daemon stores the bare root.
_OCC_TICKER_RE = re.compile(r"^([A-Z.]{1,6})\s+\d{6}[CP]\d{8}$")


def _normalize_ticker(ticker: Any) -> str:
    """Collapse both writer conventions onto the underlying root so the same
    real contract aggregates under ONE key (first sweep cycle double-closed
    contracts journaled under both)."""
    raw = str(ticker or "").strip()
    match = _OCC_TICKER_RE.match(raw)
    return match.group(1) if match else raw


def _format_strike(strike: Any) -> str:
    """Render a strike for the synthetic id — 150.0 → "150", 152.5 → "152.5"."""
    try:
        return f"{float(strike):g}"
    except (TypeError, ValueError):
        return str(strike)


def synthetic_exec_id(ticker: str, expiry_compact: str, strike: Any, right: str) -> str:
    """Deterministic exec id for an expiration close row (idempotent re-runs)."""
    return f"ep-{ticker}-{expiry_compact}-{_format_strike(strike)}{right}"


def _parse_expiry(expiry: Any) -> date | None:
    """Compact (or ISO) journal expiry → date. None when unparseable."""
    try:
        return datetime.strptime(str(expiry).replace("-", ""), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def _load_option_rows(db: Any, since_date: str) -> list[dict[str, Any]]:
    """Journal option-row payloads over the bounded look-back window.

    Stock rows (payload has ``shares``, no contracts/strike) never expire
    and are skipped here.
    """
    cursor = db.execute(
        """
        SELECT payload, filled_at, written_at
        FROM journal
        WHERE filled_at >= ?
        ORDER BY filled_at ASC
        """,
        (since_date,),
    )
    rows = cursor.fetchall()

    payloads: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0] or {}
        except Exception:
            continue
        if "shares" in payload or "strike" not in payload:
            continue
        if not (payload.get("right") and payload.get("expiry")):
            continue
        payloads.append(payload)
    return payloads


def _contract_key(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _normalize_ticker(payload.get("ticker")).upper(),
        _format_strike(payload.get("strike")),
        str(payload.get("right") or ""),
        "".join(ch for ch in str(payload.get("expiry") or "") if ch.isdigit())[:8],
    )


def _is_rehydrate_row(payload: dict[str, Any]) -> bool:
    """Which writer journaled this row. Flex rehydrate: OCC-symbol ticker,
    or numeric Flex trade ids ('9741721501', '+'-joined composites).
    Real-time daemon: bare-root ticker with dotted-hex IB exec ids."""
    if _OCC_TICKER_RE.match(str(payload.get("ticker") or "").strip()):
        return True
    exec_id = str(payload.get("ib_exec_id") or "")
    return bool(exec_id) and all(part.isdigit() for part in exec_id.split("+"))


def _net_open_quantity(rows: list[dict[str, Any]]) -> tuple[int, bool]:
    """(net signed qty, ambiguous) across BOTH writer conventions.

    The same fills can be journaled twice — per-exec rows by the real-time
    daemon and a composite row by the Flex rehydrate (regardless of how
    either writer labelled the action). Per (date, side): equal composite
    and plain totals are the same fills, counted once; unequal totals on
    both sides cannot be attributed and poison the contract (ambiguous).
    Any action outside the sign map (except CLOSED round trips) is also
    ambiguous — never guess with journal writes.
    """
    groups: dict[tuple, dict[str, int]] = {}
    ambiguous = False
    for payload in rows:
        action = str(payload.get("action") or "").upper()
        if action == _ROUND_TRIP_ACTION:
            continue
        sign = _ACTION_SIGNS.get(action)
        if sign is None:
            ambiguous = True
            continue
        try:
            qty = abs(int(payload.get("contracts") or 0))
        except (TypeError, ValueError):
            ambiguous = True
            continue
        group = groups.setdefault(
            (str(payload.get("date") or "")[:10], sign), {"comp": 0, "plain": 0}
        )
        group["comp" if _is_rehydrate_row(payload) else "plain"] += qty

    net = 0
    unpaired_comp = unpaired_plain = False
    for (_, sign), group in groups.items():
        comp, plain = group["comp"], group["plain"]
        if comp and plain:
            if comp != plain:
                ambiguous = True
            net += sign * max(comp, plain)
        else:
            unpaired_comp = unpaired_comp or bool(comp)
            unpaired_plain = unpaired_plain or bool(plain)
            net += sign * (comp + plain)
    # Both conventions contributed fills that never paired up (e.g. the same
    # sale journaled under different trade dates) — the per-day dedup cannot
    # attribute them, so the net may be doubled. Unattributable = ambiguous.
    if unpaired_comp and unpaired_plain:
        ambiguous = True
    return net, ambiguous


def _aggregate_contracts(rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Per contract key: duplication-aware net qty, exec ids, latest date."""
    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for payload in rows:
        grouped.setdefault(_contract_key(payload), []).append(payload)

    contracts: dict[tuple, dict[str, Any]] = {}
    for key, contract_rows in grouped.items():
        net, ambiguous = _net_open_quantity(contract_rows)
        contracts[key] = {
            "payload": contract_rows[0],
            "ticker": key[0],
            "net": net,
            "ambiguous": ambiguous,
            "exec_ids": {
                str(p.get("ib_exec_id") or "") for p in contract_rows if p.get("ib_exec_id")
            },
            "last_date": max(
                (str(p.get("date") or "")[:10] for p in contract_rows), default=""
            ),
        }
    return contracts


def _executed_orders_since(db: Any, since_date: str) -> list[dict[str, Any]]:
    """executed_orders payloads + fill dates with fill_time >= since_date."""
    cursor = db.execute(
        """
        SELECT exec_id, perm_id, payload, fill_time, recorded_at
        FROM executed_orders
        WHERE fill_time >= ?
        ORDER BY fill_time ASC
        """,
        (since_date,),
    )
    rows = cursor.fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row[2]) if isinstance(row[2], str) else row[2] or {}
        except Exception:
            payload = {}
        items.append({"payload": payload, "fill_date": str(row[3] or "")[:10]})
    return items


def _executed_contract_key(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    contract = payload.get("contract") or {}
    expiry_raw = str(contract.get("expiry") or contract.get("lastTradeDateOrContractMonth") or "")
    return (
        _normalize_ticker(payload.get("symbol") or contract.get("symbol")).upper(),
        _format_strike(contract.get("strike")),
        str(contract.get("right") or ""),
        "".join(ch for ch in expiry_raw if ch.isdigit())[:8],
    )


class ExpirySweepHandler(BaseHandler):
    """Daily sweep that flattens journal positions left open past expiry."""

    name = "expiry_sweep"
    interval_seconds = 86_400
    requires_market_hours = False
    service_name = "journal-expiry-sweep"

    def execute(self) -> dict[str, Any]:
        db = self._open_db()
        if db is None:
            raise RuntimeError("journal-expiry-sweep: DB unavailable")

        today_et = datetime.now(ET_ZONE).date()
        cutoff = today_et - timedelta(days=SETTLE_WINDOW_DAYS)
        lookback = (today_et - timedelta(days=JOURNAL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

        contracts = _aggregate_contracts(_load_option_rows(db, lookback))
        candidates = self._collect_candidates(contracts, cutoff)
        executed = _executed_orders_since(db, lookback) if candidates else []

        counts = {"candidates": len(candidates), "closed": 0, "skipped_guarded": 0}
        cap_hit = False
        for candidate in candidates:
            if self._is_guarded(candidate, executed):
                counts["skipped_guarded"] += 1
                continue
            if counts["closed"] >= MAX_CLOSES_PER_CYCLE:
                cap_hit = True
                break
            self._write_close_row(candidate)
            counts["closed"] += 1

        result: dict[str, Any] = dict(counts)
        if cap_hit:
            result["cap_hit"] = True
        self.record_cycle_health("ok", error=counts)
        return result

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _open_db() -> Any:
        try:
            from db.client import get_db  # noqa: PLC0415 — lazy; libsql optional
            return get_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal-expiry-sweep: DB unavailable: %s", exc)
            return None

    @staticmethod
    def _collect_candidates(
        contracts: dict[tuple, dict[str, Any]],
        cutoff: date,
    ) -> list[dict[str, Any]]:
        """Contracts with net qty != 0 whose expiry is at (or past) the
        settle cutoff. Unparseable expiries stay candidates — they are
        counted, then guarded (never written)."""
        candidates: list[dict[str, Any]] = []
        for state in contracts.values():
            if state["net"] == 0:
                continue
            expiry_date = _parse_expiry(state["payload"].get("expiry"))
            if expiry_date is not None and expiry_date > cutoff:
                continue
            candidates.append({**state, "expiry_date": expiry_date})
        return candidates

    @staticmethod
    def _is_guarded(candidate: dict[str, Any], executed: list[dict[str, Any]]) -> bool:
        if candidate["ambiguous"]:
            logger.warning(
                "journal-expiry-sweep: ambiguous journal history for %s "
                "(unattributable duplicate fills or unknown action) — skipping",
                candidate["ticker"],
            )
            return True

        expiry_date = candidate["expiry_date"]
        if expiry_date is None:
            logger.warning(
                "journal-expiry-sweep: unparseable expiry %r for %s — skipping",
                candidate["payload"].get("expiry"),
                candidate["ticker"],
            )
            return True

        payload = candidate["payload"]
        close_id = synthetic_exec_id(
            candidate["ticker"],
            str(payload.get("expiry") or ""),
            payload.get("strike"),
            str(payload.get("right") or ""),
        )
        if close_id in candidate["exec_ids"]:
            return True

        expiry_iso = expiry_date.strftime("%Y-%m-%d")
        if candidate["last_date"] > expiry_iso:
            return True

        key = _contract_key(payload)
        for item in executed:
            if _executed_contract_key(item["payload"]) == key and item["fill_date"] > expiry_iso:
                return True
        return False

    @staticmethod
    def _write_close_row(candidate: dict[str, Any]) -> None:
        """Upsert the deterministic $0.00 expiration close row.

        A write failure propagates — this sweep failing must not latch
        last_run (the dedup guards make the retry safe)."""
        if upsert_journal_entry is None:
            raise RuntimeError("journal-expiry-sweep: db.writer unavailable")

        payload = candidate["payload"]
        net = candidate["net"]
        expiry_iso = candidate["expiry_date"].strftime("%Y-%m-%d")
        ticker = candidate["ticker"]
        close_id = synthetic_exec_id(
            ticker, str(payload.get("expiry") or ""), payload.get("strike"), str(payload.get("right") or "")
        )

        entry = {
            "ticker": ticker,
            "date": expiry_iso,
            "decision": "IB_AUTO_IMPORT",
            "action": "SELL_OPTION" if net > 0 else "BUY_TO_CLOSE",
            "contracts": abs(net),
            "strike": payload.get("strike"),
            "right": payload.get("right"),
            "expiry": payload.get("expiry"),
            "fill_price": 0.0,
            "total_cost": 0.0,
            "commission": 0.0,
            "ib_exec_id": close_id,
            "ib_codes": "Ep",
            "notes": f"Expired worthless — synthetic close by expiry sweep on {datetime.now(ET_ZONE).strftime('%Y-%m-%d')}",
        }
        upsert_journal_entry(close_id, entry, filled_at=expiry_iso)
        logger.info(
            "journal-expiry-sweep: closed %s x%d %s at $0.00 (%s)",
            ticker, abs(net), close_id, entry["action"],
        )

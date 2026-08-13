#!/usr/bin/env python3
"""
IB Trade Reconciliation Script

Fetches trading history from IB and reconciles with the Turso journal and
latest portfolio snapshot.
Designed to run asynchronously at startup without blocking the UI.

Actions detected:
- BUY: Opening long stock/option position
- SELL: Closing long position (realized P&L)
- SHORT: Opening short stock position
- COVER: Closing short position (realized P&L)
- New positions not in the latest portfolio snapshot
- Closed positions still in the latest portfolio snapshot
"""

import os
import sys
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT
from db.readers import read_journal_trades, read_latest_portfolio_snapshot


def log(msg: str, level: str = "info"):
    """Print log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {"info": "ℹ", "warn": "⚠", "error": "✗", "success": "✓"}.get(level, "•")
    print(f"[{timestamp}] {prefix} {msg}")


def connect_ib(port: int = DEFAULT_GATEWAY_PORT, client_id: int = CLIENT_IDS["ib_reconcile"]) -> Optional[IBClient]:
    """Connect to IB Gateway/TWS."""
    try:
        client = IBClient()
        client.connect(host=DEFAULT_HOST, port=port, client_id=client_id)
        return client
    except Exception as e:
        log(f"IB connection failed: {e}", "error")
        return None

def load_trade_log() -> dict:
    """Return journal trades in the legacy dict shape used by reconciliation."""
    return {"trades": read_journal_trades()}


def load_portfolio_snapshot() -> dict:
    """Return the latest portfolio snapshot in legacy portfolio shape."""
    return read_latest_portfolio_snapshot() or {"positions": []}


def save_reconciliation_report(data: dict) -> str:
    """Persist reconciliation output and return its exact snapshot identity."""
    try:
        from db.writer import upsert_reconciliation_log
    except ImportError:
        raise RuntimeError("reconciliation writer unavailable")
    try:
        snapshot_at = data.get("snapshot_at") or data.get("timestamp") or datetime.now().isoformat()
        upsert_reconciliation_log(snapshot_at, data)
        return str(snapshot_at)
    except Exception as exc:  # noqa: BLE001 — best-effort
        raise RuntimeError(f"reconciliation_log write failed: {exc}") from exc

def get_trade_log_trades(trade_log: dict) -> set:
    """Extract set of (ticker, date, structure_type) tuples from trade log."""
    trades = set()
    for trade in trade_log.get("trades", []):
        ticker = trade.get("ticker")
        date = trade.get("date") or trade.get("close_date")
        structure = trade.get("structure", "")
        trades.add((ticker, date, structure))
    return trades


def get_trade_log_exec_ids(trade_log: dict) -> set:
    """Collect every ib_exec_id in the trade log, including composite parts."""
    ids = set()
    for trade in trade_log.get("trades", []):
        exec_id = trade.get("ib_exec_id")
        if not exec_id:
            continue
        ids.add(str(exec_id))
        for part in str(exec_id).split("+"):
            if part:
                ids.add(part)
    return ids

def fetch_ib_executions(client: IBClient, lookback_days: int = 7) -> list:
    """Fetch executions from IB for the last N days."""
    executions = []
    fills = client.get_fills()

    for fill in fills:
        e = fill.execution
        c = fill.contract
        cr = fill.commissionReport

        executions.append({
            "time": e.time,
            "symbol": c.symbol,
            "sec_type": c.secType,
            "side": e.side,  # BOT or SLD
            "shares": e.shares,
            "price": e.price,
            "exchange": e.exchange,
            "commission": cr.commission if cr else 0,
            "realized_pnl": cr.realizedPNL if cr and cr.realizedPNL else 0,
            "strike": c.strike if c.secType == "OPT" else None,
            "expiry": c.lastTradeDateOrContractMonth if c.secType == "OPT" else None,
            "right": c.right if c.secType == "OPT" else None,
            "exec_id": getattr(e, "execId", None),
        })

    return executions

def fetch_ib_positions(client: IBClient) -> list:
    """Fetch current positions from IB."""
    positions = []
    for p in client.get_positions():
        positions.append({
            "symbol": p.contract.symbol,
            "sec_type": p.contract.secType,
            "quantity": p.position,
            "avg_cost": p.avgCost,
            "strike": p.contract.strike if p.contract.secType == "OPT" else None,
            "expiry": p.contract.lastTradeDateOrContractMonth if p.contract.secType == "OPT" else None,
            "right": p.contract.right if p.contract.secType == "OPT" else None,
        })
    return positions

def _contract_key(e: dict) -> str:
    """Build a grouping key: symbol only for stocks, symbol+strike+expiry+right for options."""
    if e["sec_type"] in ("OPT", "BAG"):
        return f"{e['symbol']}|{e['sec_type']}|{e.get('strike')}|{e.get('expiry')}|{e.get('right')}"
    return f"{e['symbol']}|{e['sec_type']}"


def group_executions_by_symbol(executions: list) -> dict:
    """Group executions by contract (symbol + strike/expiry/right for options) and determine net action."""
    grouped = {}

    for e in executions:
        key = _contract_key(e)
        if key not in grouped:
            grouped[key] = {
                "symbol": e["symbol"],
                "sec_type": e["sec_type"],
                "strike": e.get("strike"),
                "expiry": e.get("expiry"),
                "right": e.get("right"),
                "executions": [],
                "exec_ids": [],
                "net_quantity": 0,
                "total_value": 0,
                "total_commission": 0,
                "total_realized_pnl": 0,
            }

        g = grouped[key]
        g["executions"].append(e)
        if e.get("exec_id"):
            g["exec_ids"].append(str(e["exec_id"]))

        qty = e["shares"] if e["side"] == "BOT" else -e["shares"]
        g["net_quantity"] += qty
        g["total_value"] += e["shares"] * e["price"]
        g["total_commission"] += e["commission"]
        g["total_realized_pnl"] += e["realized_pnl"]

    # Determine action for each group
    for key, g in grouped.items():
        if g["net_quantity"] > 0:
            g["action"] = "BUY" if g["sec_type"] == "STK" else "BUY_OPTION"
        elif g["net_quantity"] < 0:
            # Check if it's a close (has realized P&L) or short opening
            if g["total_realized_pnl"] != 0:
                g["action"] = "SELL" if g["sec_type"] == "STK" else "SELL_OPTION"
            else:
                g["action"] = "SHORT" if g["sec_type"] == "STK" else "SELL_TO_OPEN"
        else:
            # Net zero - could be a day trade or covered
            if g["total_realized_pnl"] != 0:
                g["action"] = "CLOSED"
            else:
                g["action"] = "NEUTRAL"

    return grouped

def find_new_trades(executions: list, trade_log: dict) -> list:
    """Find executions that aren't in the trade log.

    Dedupe priority: ib_exec_id (preferred) → (ticker, date, structure)
    fingerprint (legacy fallback for rows imported before exec_id existed).
    """
    existing_legacy = get_trade_log_trades(trade_log)
    existing_exec_ids = get_trade_log_exec_ids(trade_log)
    grouped = group_executions_by_symbol(executions)

    new_trades = []
    for key, g in grouped.items():
        symbol = g["symbol"]
        if not g["executions"]:
            continue

        trade_date = g["executions"][0]["time"].strftime("%Y-%m-%d")
        composite_exec_id = "+".join(sorted(set(g.get("exec_ids", [])))) if g.get("exec_ids") else None

        already_logged = False
        if composite_exec_id:
            for part in composite_exec_id.split("+"):
                if part and part in existing_exec_ids:
                    already_logged = True
                    break

        if not already_logged:
            for ticker, date, _structure in existing_legacy:
                if ticker == symbol and date == trade_date:
                    already_logged = True
                    break

        if already_logged or g["action"] in ["NEUTRAL"]:
            continue

        entry = {
            "symbol": symbol,
            "date": trade_date,
            "action": g["action"],
            "net_quantity": g["net_quantity"],
            "avg_price": g["total_value"] / sum(e["shares"] for e in g["executions"]) if g["executions"] else 0,
            "commission": g["total_commission"],
            "realized_pnl": g["total_realized_pnl"],
            "sec_type": g["sec_type"],
        }
        if composite_exec_id:
            entry["ib_exec_id"] = composite_exec_id
        if g["sec_type"] in ("OPT", "BAG") and g.get("strike"):
            entry["strike"] = g["strike"]
            entry["expiry"] = g.get("expiry")
            entry["right"] = g.get("right")
        new_trades.append(entry)

    return new_trades

def _normalize_expiry(expiry) -> str:
    """Digits-only expiry so IB '20260618' matches snapshot '2026-06-18'."""
    return "".join(ch for ch in str(expiry or "") if ch.isdigit())


# Equity/index options stop trading by 16:15 ET on expiry day; past this
# cutoff a residual 0-qty row on a same-day expiry is expiration cleanup,
# not live drift (a Friday-evening deploy otherwise reproduces the
# 2026-08-09 SPCX page).
_EXPIRY_SESSION_CUTOFF_ET = time(16, 15)


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _is_past_expiry(normalized_expiry: str, now_et: datetime = None) -> bool:
    """True when the expiry parses and the contract can no longer trade.

    Strictly before today (ET), or today once the session cutoff has
    passed. Only the first 8 digits are parsed (FOP expiries carry a time
    suffix). Unparseable expiry = NOT expired, so it stays real drift.
    """
    try:
        expiry_date = datetime.strptime(normalized_expiry[:8], "%Y%m%d").date()
    except ValueError:
        return False
    now = now_et or _now_et()
    if expiry_date < now.date():
        return True
    return expiry_date == now.date() and now.time() >= _EXPIRY_SESSION_CUTOFF_ET


def _is_expired_only_position(pos: dict, now_et: datetime = None) -> bool:
    """True when every leg of a snapshot position is a past-expiry OPT."""
    keys = list(_local_leg_quantity_keys(pos))
    if not keys:
        return False
    for (_, sec_type, _, _, expiry), _signed in keys:
        if sec_type != "OPT" or not _is_past_expiry(expiry, now_et):
            return False
    return True


def _ib_contract_quantity_key(p: dict):
    """Per-contract key for an IB position row."""
    if p.get("sec_type") == "OPT":
        right = str(p.get("right") or "")[:1].upper()
        return (p["symbol"], "OPT", float(p.get("strike") or 0), right,
                _normalize_expiry(p.get("expiry")))
    return (p["symbol"], "STK", None, None, None)


def _local_leg_quantity_keys(pos: dict):
    """Yield (key, signed_quantity) for each leg of a snapshot position."""
    ticker = pos.get("ticker")
    for leg in pos.get("legs") or []:
        contracts = leg.get("contracts") or 0
        direction = (leg.get("direction") or pos.get("direction") or "LONG").upper()
        signed = contracts if direction == "LONG" else -contracts
        leg_type = str(leg.get("type") or "").upper()
        if leg_type.startswith(("C", "P")):
            right = leg_type[0]
            expiry = _normalize_expiry(leg.get("expiry") or pos.get("expiry"))
            yield (ticker, "OPT", float(leg.get("strike") or 0), right, expiry), signed
        else:
            yield (ticker, "STK", None, None, None), signed


def find_position_discrepancies(ib_positions: list, portfolio: dict) -> dict:
    """Find positions that differ between IB and the latest portfolio snapshot.

    Four classes:
      missing_locally   — in IB, symbol absent from the snapshot
      missing_in_ib     — in the snapshot, symbol absent from IB (closed)
      quantity_mismatch — symbol on BOTH sides but per-contract signed
                          quantities differ (partial close, assignment,
                          manual TWS trade). R-005: this list was
                          initialized but never populated.
      expired_locally   — snapshot still carries an OPT whose expiry is
                          in the past and IB reports 0 (expiration emits
                          no execution). Cleanup surface, not live drift
                          — never pages. 2026-08-09 SPCX incident.
    """
    discrepancies = {
        "missing_locally": [],  # In IB but not in local snapshot
        "missing_in_ib": [],    # In local snapshot but not in IB (closed)
        "quantity_mismatch": [],
        "expired_locally": [],
    }

    # Build lookup of local tickers (just by symbol for simplicity)
    local_tickers = set()
    local_positions = {}
    for pos in portfolio.get("positions", []):
        ticker = pos.get("ticker")
        if ticker:
            local_tickers.add(ticker)
            # Use ticker as key, store the position
            if ticker not in local_positions:
                local_positions[ticker] = []
            local_positions[ticker].append(pos)

    # Build lookup of IB tickers
    ib_tickers = set()
    ib_positions_by_symbol = {}
    for p in ib_positions:
        symbol = p["symbol"]
        ib_tickers.add(symbol)
        if symbol not in ib_positions_by_symbol:
            ib_positions_by_symbol[symbol] = []
        ib_positions_by_symbol[symbol].append(p)

    now_et = _now_et()

    # Find positions in IB not locally (new positions). A 0-qty broker row
    # (expired/closed residual) is never actionable drift.
    for symbol in ib_tickers - local_tickers:
        for p in ib_positions_by_symbol[symbol]:
            if p.get("quantity") or 0:
                discrepancies["missing_locally"].append(p)

    # Find positions locally not in IB (closed positions)
    for ticker in local_tickers - ib_tickers:
        for p in local_positions[ticker]:
            if _is_expired_only_position(p, now_et):
                discrepancies["expired_locally"].append(p)
            else:
                discrepancies["missing_in_ib"].append(p)

    # Per-contract signed-quantity comparison for symbols on BOTH sides.
    for symbol in ib_tickers & local_tickers:
        ib_qty: dict = {}
        for p in ib_positions_by_symbol[symbol]:
            key = _ib_contract_quantity_key(p)
            ib_qty[key] = ib_qty.get(key, 0) + (p.get("quantity") or 0)
        local_qty: dict = {}
        for pos in local_positions[symbol]:
            for key, signed in _local_leg_quantity_keys(pos):
                local_qty[key] = local_qty.get(key, 0) + signed
        for key in set(ib_qty) | set(local_qty):
            ib_n = ib_qty.get(key, 0)
            local_n = local_qty.get(key, 0)
            if ib_n != local_n:
                _, sec_type, strike, right, expiry = key
                entry = {
                    "symbol": symbol,
                    "sec_type": sec_type,
                    "ib_quantity": ib_n,
                    "local_quantity": local_n,
                }
                if sec_type == "OPT":
                    entry.update({"strike": strike, "right": right, "expiry": expiry})
                # Past-expiry OPT the broker no longer holds (residual 0-qty
                # row) is expiration cleanup; nonzero IB qty past expiry is
                # still real drift.
                expired = (
                    sec_type == "OPT" and ib_n == 0 and _is_past_expiry(expiry, now_et)
                )
                bucket = "expired_locally" if expired else "quantity_mismatch"
                discrepancies[bucket].append(entry)

    return discrepancies

def generate_reconciliation_report(new_trades: list, discrepancies: dict) -> dict:
    """Generate a report of what needs to be reconciled."""
    quantity_mismatch = discrepancies.get("quantity_mismatch", [])
    report = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "new_trades": new_trades,
        "positions_missing_locally": discrepancies["missing_locally"],
        "positions_closed": discrepancies["missing_in_ib"],
        "quantity_mismatch": quantity_mismatch,
        # .get(): legacy callers pass 3-key dicts. Cleanup surface only —
        # deliberately excluded from needs_attention.
        "expired_locally": discrepancies.get("expired_locally", []),
        "needs_attention": (
            len(new_trades) > 0
            or len(discrepancies["missing_locally"]) > 0
            or len(discrepancies["missing_in_ib"]) > 0
            or len(quantity_mismatch) > 0
        ),
    }
    return report


SERVICE_NAME = "position-reconcile"


def _record_health(state: str, detail: dict) -> None:
    """Best-effort service_health row so the watchdog sees this run."""
    try:
        from db.writer import record_service_health

        record_service_health(SERVICE_NAME, state, error=detail or None)
    except Exception as exc:  # noqa: BLE001 — health write must not mask the run
        log(f"service_health write failed: {exc}", "warn")


def _health_detail(report: dict) -> dict:
    return {
        "new_trades_count": len(report.get("new_trades", [])),
        "positions_missing_locally_count": len(report.get("positions_missing_locally", [])),
        "positions_closed_count": len(report.get("positions_closed", [])),
        "quantity_mismatch_count": len(report.get("quantity_mismatch", [])),
        "quantity_mismatch": report.get("quantity_mismatch", [])[:10],
        "expired_locally_count": len(report.get("expired_locally", [])),
        "expired_locally": report.get("expired_locally", [])[:10],
    }

def main():
    """Main reconciliation routine."""
    log("Starting IB trade reconciliation...")

    # Connect to IB. R-005: a silent skip here previously exited 0 and the
    # /journal/reconcile route reported {"ok": true} — fail loud instead.
    client = connect_ib()
    if not client:
        log("Cannot connect to IB Gateway - reconciliation NOT run", "error")
        _record_health("error", {"reason": "ib_connect_failed"})
        sys.exit(2)

    try:
        # Load canonical local state
        trade_log = load_trade_log()
        portfolio = load_portfolio_snapshot()

        # Fetch IB data
        log("Fetching executions from IB...")
        executions = fetch_ib_executions(client)
        log(f"Found {len(executions)} executions")

        log("Fetching positions from IB...")
        positions = fetch_ib_positions(client)
        log(f"Found {len(positions)} positions")
        
        # Find discrepancies
        log("Checking for new trades...")
        new_trades = find_new_trades(executions, trade_log)
        
        log("Checking position discrepancies...")
        discrepancies = find_position_discrepancies(positions, portfolio)
        
        # Generate report
        report = generate_reconciliation_report(new_trades, discrepancies)
        
        # Save reconciliation report
        snapshot_at = save_reconciliation_report(report)
        print(f"RADON_RECONCILIATION_SNAPSHOT={snapshot_at}")

        # Heartbeat for the watchdog: error when attention is needed so the
        # error bucket pages; ok otherwise.
        _record_health(
            "error" if report["needs_attention"] else "ok",
            _health_detail(report),
        )

        # Log summary
        if report["needs_attention"]:
            log(f"⚠️  Reconciliation needed:", "warn")
            if new_trades:
                log(f"   • {len(new_trades)} new trades to log", "warn")
                for t in new_trades:
                    log(f"     - {t['action']} {t['symbol']}: {t['net_quantity']} @ ${t['avg_price']:.2f}", "info")
            if discrepancies["missing_locally"]:
                log(f"   • {len(discrepancies['missing_locally'])} positions missing locally", "warn")
            if discrepancies["missing_in_ib"]:
                log(f"   • {len(discrepancies['missing_in_ib'])} positions may be closed", "warn")
            if discrepancies["quantity_mismatch"]:
                log(f"   • {len(discrepancies['quantity_mismatch'])} quantity mismatches vs IB", "warn")
                for m in discrepancies["quantity_mismatch"]:
                    log(
                        f"     - {m['symbol']}: IB {m['ib_quantity']} vs local {m['local_quantity']}",
                        "warn",
                    )
        else:
            log("✓ Trade log and portfolio are in sync", "success")

        if discrepancies.get("expired_locally"):
            log(
                f"   • {len(discrepancies['expired_locally'])} expired local "
                "option(s) awaiting snapshot cleanup (not paging)",
                "warn",
            )
            for e in discrepancies["expired_locally"]:
                label = e.get("symbol") or e.get("ticker")
                log(f"     - {label}: expiry {e.get('expiry')}", "info")

    finally:
        client.disconnect()
        log("Disconnected from IB")

if __name__ == "__main__":
    main()

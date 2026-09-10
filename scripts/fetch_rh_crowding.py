#!/usr/bin/env python3
"""Robinhood retail-crowding overlay — popular-watchlist membership + scan hits.

Reads the official Robinhood trading MCP (read-only; execution stays on IB)
and stores one row per (date, symbol): the symbol's 1-based rank in the
popular watchlists, the watchlist names carrying it, and how many Robinhood
scans surfaced it today. Dual-written to Turso ``rh_crowding`` (migration
0066) + ``data/rh_crowding.json``.

⛔ These are DESCRIPTIVE crowding features only. They must never feed the
Four Gates: crowding is not a dark-pool/OTC edge (Gate 2), plays no part in
convexity math (Gate 1), and never enters Kelly sizing (Gate 3). Pinned by
scripts/tests/test_rh_crowding.py::TestCrowdingCannotTripGates.

Unconfigured hosts (no ROBINHOOD_MCP_TOKEN) skip cleanly with exit 0 —
operator-invoked, no timer, same as the theta scan (no auto-timer).

Usage:
    python3.13 scripts/fetch_rh_crowding.py          # human summary (stderr)
    python3.13 scripts/fetch_rh_crowding.py --json   # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
except Exception:
    pass

RH_CROWDING_JSON = _PROJECT_DIR / "data" / "rh_crowding.json"
MAX_SCANS_PER_RUN = 5  # bound the tool calls; scans beyond this are ignored
# Bounds against degenerate/hostile payloads: the popular watchlists carry
# ~100 names, so anything near these limits is garbage, and an unbounded
# row set would violate the Turso Hrana write-bounding rules downstream.
MAX_WATCHLISTS = 50        # popular-watchlist payload iteration cap
MAX_ROWS_PER_LIST = 2000   # per-watchlist / per-scan-result iteration cap
MAX_CROWDING_ROWS = 1000   # total rows persisted per run

_SYMBOL_KEYS = ("symbol", "ticker", "display_symbol")
_NAME_KEYS = ("name", "display_name", "title", "id")
_ITEM_LIST_KEYS = ("items", "instruments", "entries", "symbols")


def _log(message: str) -> None:
    print(f"[rh-crowding] {message}", file=sys.stderr)


def _row_symbol(row: Any) -> Optional[str]:
    if isinstance(row, str):
        return row.upper() or None
    if isinstance(row, dict):
        for key in _SYMBOL_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                return value.upper()
    return None


def _watchlist_name(watchlist: dict) -> str:
    for key in _NAME_KEYS:
        value = watchlist.get(key)
        if isinstance(value, str) and value:
            return value
    return "popular"


def _watchlist_symbols(watchlist: dict) -> list[str]:
    """Ordered symbols in one watchlist payload (schema probed, not assumed)."""
    rows: list[Any] = []
    for key in _ITEM_LIST_KEYS:
        value = watchlist.get(key)
        if isinstance(value, list):
            rows = value
            break
    symbols: list[str] = []
    for row in rows[:MAX_ROWS_PER_LIST]:
        symbol = _row_symbol(row)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def build_rows(
    popular_watchlists: list[dict],
    scan_results: dict[str, list[dict]],
    date_str: str,
) -> list[dict[str, Any]]:
    """Fold watchlist membership + scan hits into rh_crowding rows.

    ``popular_rank`` is the 1-based position of the symbol's FIRST appearance
    across the popular watchlists (rank 1 = most crowded). ``scan_hits`` is
    the number of distinct scans whose results carry the symbol.
    """
    rank: dict[str, int] = {}
    watchlists: dict[str, list[str]] = {}
    position = 0
    for watchlist in popular_watchlists[:MAX_WATCHLISTS]:
        name = _watchlist_name(watchlist)
        for symbol in _watchlist_symbols(watchlist):
            if symbol not in rank:
                position += 1
                rank[symbol] = position
            watchlists.setdefault(symbol, [])
            if name not in watchlists[symbol]:
                watchlists[symbol].append(name)

    scan_hits: dict[str, int] = {}
    for _scan_id, rows in scan_results.items():
        seen: set[str] = set()
        for row in rows[:MAX_ROWS_PER_LIST]:
            symbol = _row_symbol(row)
            if symbol:
                seen.add(symbol)
        for symbol in seen:
            scan_hits[symbol] = scan_hits.get(symbol, 0) + 1

    symbols = sorted(set(rank) | set(scan_hits))
    if len(symbols) > MAX_CROWDING_ROWS:
        # Keep the ranked (most crowded) names first, then scan-only names
        # alphabetically, and drop the rest — bounded writes, not best-effort.
        _log(
            f"crowding payload carried {len(symbols)} symbols; "
            f"keeping the {MAX_CROWDING_ROWS} most crowded"
        )
        symbols = sorted(
            symbols,
            key=lambda s: (rank.get(s) is None, rank.get(s) or 0, s),
        )[:MAX_CROWDING_ROWS]
        symbols = sorted(symbols)

    return [
        {
            "date": date_str,
            "symbol": symbol,
            "popular_rank": rank.get(symbol),
            "watchlists": watchlists.get(symbol, []),
            "scan_hits": scan_hits.get(symbol, 0),
        }
        for symbol in symbols
    ]


def fetch_crowding() -> tuple[list[dict], dict[str, list[dict]]]:
    """Pull popular watchlists + bounded scan results from the MCP."""
    from clients.robinhood_client import RobinhoodClient, RobinhoodClientError

    with RobinhoodClient() as rh:
        popular = rh.get_popular_watchlists()
        scan_results: dict[str, list[dict]] = {}
        try:
            scans = rh.get_scans()
        except RobinhoodClientError as exc:
            _log(f"get_scans failed (watchlists only): {exc}")
            scans = []
        for scan in scans[:MAX_SCANS_PER_RUN]:
            scan_id = scan.get("id") or scan.get("scan_id")
            if not scan_id:
                continue
            try:
                scan_results[str(scan_id)] = rh.run_scan(str(scan_id))
            except RobinhoodClientError as exc:
                _log(f"run_scan {scan_id} failed: {exc}")
    return popular, scan_results


def persist(rows: list[dict[str, Any]], scan_time: str) -> None:
    from db import writer

    writer.ensure_no_replica_for_writers()
    writer.upsert_rh_crowding_rows(rows, recorded_at=scan_time)
    payload = {"scan_time": scan_time, "count": len(rows), "rows": rows}
    RH_CROWDING_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = RH_CROWDING_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, RH_CROWDING_JSON)


def run() -> Optional[dict[str, Any]]:
    """One cycle. None = unconfigured clean skip (exit 0, nothing written)."""
    from clients.robinhood_client import robinhood_configured

    if not robinhood_configured():
        _log(
            "no Robinhood credentials (access or refresh token); "
            "skipping cleanly (no fetch, no write)"
        )
        return None

    now = datetime.now(timezone.utc)
    # REL-175 (R-483): the docstring and .env.example promise a clean skip on
    # any Robinhood failure; the fetch and persist sat outside every try.
    try:
        popular, scan_results = fetch_crowding()
        rows = build_rows(popular, scan_results, now.strftime("%Y-%m-%d"))
        scan_time = now.isoformat().replace("+00:00", "Z")
        if rows:
            persist(rows, scan_time)
        else:
            _log("no crowding rows parsed; nothing written")
    except Exception as exc:  # noqa: BLE001 — overlay job: skip cleanly, say why
        _log(f"crowding cycle failed; skipping cleanly: {exc}")
        return None
    return {"scan_time": scan_time, "count": len(rows), "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Robinhood popular-watchlist / scan crowding overlay (read-only)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if payload is None:
        return
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _log(f"{payload['count']} crowded symbols recorded")


if __name__ == "__main__":
    main()

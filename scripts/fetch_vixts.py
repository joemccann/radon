#!/usr/bin/env python3
"""VIX TS Indicator — VIX / VIX3M term-structure ratio.

Descriptive regime read, not a forecast: below 1.00 the volatility curve is
in contango and near-term vol is priced under 3-month vol; above 1.00 it is
in backwardation and stress has moved into the front of the curve.

Source: Cboe CDN daily-price CSVs (VIX_History.csv + VIX3M_History.csv, with
SPX_History.csv as the chart overlay). VIX3M history starts 2009-09-18 and is
the binding constraint. Cboe CDN only: IB and UW cannot serve this series
cleanly, so the plausibility guard in lib/vixts_math.py is the sole
protection against a bad source. Spec: docs/indicators/vixts.md.

Output is dual-written to Turso vixts_history + data/vixts.json.

Usage:
    python3 scripts/fetch_vixts.py            # human summary (stderr)
    python3 scripts/fetch_vixts.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

try:
    from db import writer  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

from lib.vixts_math import (  # noqa: E402
    build_current,
    compute_stats,
    ensure_plausible_series,
    join_series,
)

# ── constants ─────────────────────────────────────────────────────
VIXTS_JSON = _PROJECT_DIR / "data" / "vixts.json"

SERVICE = "vixts"

# SPX_History.csv genuinely publishes its close under an "SPX" column, not
# "CLOSE" (same quirk as VVIX_History.csv), so the column is per-symbol.
_SYMBOLS = ("VIX", "VIX3M", "SPX")
_VALUE_COLUMN = {"VIX": "CLOSE", "VIX3M": "CLOSE", "SPX": "SPX"}

# Mirrors radon-vixts.timer (OnCalendar=*-*-* 02:45:00 UTC) so heartbeat copy
# can name the next attempt instead of hardcoding cadence text.
TIMER_HOUR_UTC = 2
TIMER_MINUTE_UTC = 45


# ── Cboe CSV parsing ──────────────────────────────────────────────

def parse_index_csv(text: str, value_column: str) -> list[dict[str, Any]]:
    """Cboe daily-prices CSV → ascending [{date: "YYYY-MM-DD", value: float}].

    Dates arrive MM/DD/YYYY; header and malformed rows (bad date, missing
    or empty value) are skipped.
    """
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        try:
            date = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y")
            value = float(raw[value_column])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"date": date.date().isoformat(), "value": value})
    rows.sort(key=lambda r: r["date"])
    return rows


# ── persistence ───────────────────────────────────────────────────

def _read_json_cache() -> Optional[dict[str, Any]]:
    """Last dual-written payload from data/vixts.json, if present."""
    try:
        return json.loads(VIXTS_JSON.read_text())
    except (OSError, ValueError):
        return None


def _write_json_cache(payload: dict[str, Any]) -> None:
    """Atomic write of data/vixts.json — fallback only; Turso is the truth."""
    VIXTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = VIXTS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, VIXTS_JSON)


def _write_db(
    payload: dict[str, Any],
    scan_time: str,
    *,
    rows_changed: bool,
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Snapshot + heartbeat every cycle; row upserts only when the source moved.

    The snapshot and heartbeat run even on the 304 path so a silently dead
    writer trips the staleness banner (feedback_service_health_heartbeat).
    """
    if writer is None:
        return
    row_error: Optional[dict[str, Any]] = None
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_vixts_rows(payload["series"], recorded_at=scan_time)
    except Exception as exc:  # noqa: BLE001
        # R-192: see fetch_vixcor._write_db — a failed row upsert must not
        # take the snapshot and the heartbeat down with it and still exit 0.
        print(f"[vixts] row upsert failed: {exc}", file=sys.stderr)
        row_error = {
            "message": f"vixts row upsert failed: {exc}",
            "class": "db_write_failed",
        }
    try:
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        writer.record_service_health(
            SERVICE,
            "ok" if (health_error is None and row_error is None) else "error",
            finished_at=scan_time,
            error=health_error or row_error,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[vixts] db cache non-fatal: {exc}", file=sys.stderr)


# ── payload ───────────────────────────────────────────────────────

def build_payload(
    series: list[dict[str, Any]],
    *,
    scan_time: str,
    source_last_modified: dict[str, Optional[str]],
) -> dict[str, Any]:
    """Full API contract for the vixts snapshot / JSON fallback."""
    return {
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "data_date": series[-1]["date"] if series else None,
        "count": len(series),
        "current": build_current(series),
        "stats": compute_stats(series),
        "series": series,
    }


# ── orchestration ─────────────────────────────────────────────────

def _fetch_all(client: Any, cached_stamps: dict[str, Any]) -> tuple[dict, dict]:
    """One conditional GET per symbol, keyed by the cached per-file stamp."""
    texts: dict[str, Optional[str]] = {}
    stamps: dict[str, Optional[str]] = {}
    for symbol in _SYMBOLS:
        text, last_modified = client.fetch_history(
            symbol, if_modified_since=cached_stamps.get(symbol.lower())
        )
        texts[symbol] = text
        stamps[symbol.lower()] = last_modified
    return texts, stamps


def _refetch_unchanged(client: Any, texts: dict, stamps: dict) -> None:
    """Re-pull unconditionally: a rebuild needs all three texts, 304 or not."""
    for symbol in _SYMBOLS:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)


def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch all three Cboe legs, compute, dual-write, and return the payload.

    Conditional GET on every file using the cached per-file stamps: when all
    three come back 304 the cached payload is reused with a fresh scan_time
    and only the snapshot + heartbeat refresh. Any change rebuilds the whole
    series, which is idempotent because Cboe serves full history every pull.
    """
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if client is None:
        from clients.cboe_client import CboeClient
        client = CboeClient()

    cached = _read_json_cache()
    texts, stamps = _fetch_all(client, (cached or {}).get("source_last_modified") or {})

    if cached and all(text is None for text in texts.values()):
        print("[vixts] all sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        _write_db(payload, scan_time, rows_changed=False)
        _write_json_cache(payload)
        return payload

    _refetch_unchanged(client, texts, stamps)
    parsed = {
        symbol: parse_index_csv(texts[symbol], _VALUE_COLUMN[symbol]) for symbol in _SYMBOLS
    }
    series = join_series(parsed["VIX"], parsed["VIX3M"], parsed["SPX"])
    ensure_plausible_series(series)

    payload = build_payload(series, scan_time=scan_time, source_last_modified=stamps)
    print(
        f"[vixts] {payload['count']} joined sessions through {payload['data_date']} "
        f"(ratio {payload['current']['ratio']}, {payload['current']['regime']})",
        file=sys.stderr,
    )
    _write_db(payload, scan_time, rows_changed=True)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    print(
        f"\nVIX TS — {payload['count']} sessions through {payload.get('data_date')}",
        file=sys.stderr,
    )
    print(
        f"  ratio      {current.get('ratio')}  [{current.get('regime')}]",
        file=sys.stderr,
    )
    print(
        f"  vix/vix3m  {current.get('vix')} / {current.get('vix3m')}",
        file=sys.stderr,
    )
    if stats:
        print(
            f"  range      {stats['min']:.4f} .. {stats['max']:.4f}  "
            f"median {stats['median']:.4f}",
            file=sys.stderr,
        )
        print(
            f"  backwardation  {stats['days_backwardation']} days "
            f"({stats['pct_backwardation']:.2f}%), last {stats['last_backwardation_date']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VIX TS — VIX / VIX3M term-structure ratio (Cboe CDN)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

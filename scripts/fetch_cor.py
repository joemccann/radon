#!/usr/bin/env python3
"""COR Indicator — SPX implied correlation (Cboe COR1M/COR3M/COR6M/COR1Y).

Daily closes of the Cboe SPX implied correlation index family: the
market-implied average pairwise correlation of the top-50 SPX constituents,
backed out from index option prices vs single-stock option prices, per
tenor. Values are index points (percent, 0-100). Low readings mark a
dispersion regime; spikes mark systemic stress already in the price.

Source: Cboe CDN daily-price CSVs (one per tenor). CLOSE only — pre-2022
rows are close-only backfill with synthetic OHLC. The merged series is the
union of dates across tenors, null where a tenor misses a date (COR3M has
15 scattered gaps). Spec: docs/indicators/cor.md.

Output is dual-written to Turso cor_history + data/cor.json.

Usage:
    python3 scripts/fetch_cor.py            # human summary (stderr)
    python3 scripts/fetch_cor.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
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

# ── constants ─────────────────────────────────────────────────────
COR_JSON = _PROJECT_DIR / "data" / "cor.json"

_TENORS = ("COR1M", "COR3M", "COR6M", "COR1Y")
_TENOR_KEYS = tuple(symbol.lower() for symbol in _TENORS)


# ── Cboe CSV parsing ──────────────────────────────────────────────

def parse_index_csv(text: str) -> list[dict[str, Any]]:
    """Cboe daily-prices CSV → ascending [{date: "YYYY-MM-DD", value: float}].

    Dates arrive MM/DD/YYYY; header and malformed rows (bad date, missing
    or empty CLOSE) are skipped. CLOSE only — pre-2022 OHLC is synthetic.
    """
    rows: list[dict[str, Any]] = []
    for raw in csv.DictReader(io.StringIO(text)):
        try:
            date = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y")
            value = float(raw["CLOSE"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"date": date.date().isoformat(), "value": value})
    rows.sort(key=lambda r: r["date"])
    return rows


# ── merge + stats ─────────────────────────────────────────────────

def merge_series(by_tenor: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Per-tenor parsed rows → union-of-dates series, ascending.

    Each output row carries every input tenor key; None where that tenor
    is missing the date (COR3M's scattered gaps).
    """
    values_by_tenor = {
        tenor: {row["date"]: row["value"] for row in rows}
        for tenor, rows in by_tenor.items()
    }
    dates = sorted({date for values in values_by_tenor.values() for date in values})
    return [
        {"date": date, **{tenor: values.get(date) for tenor, values in values_by_tenor.items()}}
        for date in dates
    ]


def _tenor_closes(series: list[dict[str, Any]], tenor: str) -> list[float]:
    return [row[tenor] for row in series if row.get(tenor) is not None]


def compute_stats(series: list[dict[str, Any]]) -> dict[str, Optional[dict[str, float]]]:
    """Per-tenor {high, low, avg, stddev (population), percentile}.

    percentile = share of the tenor's non-null closes STRICTLY below the
    latest close (all-equal history → 0.0). None for an all-null tenor.
    """
    stats: dict[str, Optional[dict[str, float]]] = {}
    for tenor in _TENOR_KEYS:
        closes = _tenor_closes(series, tenor)
        if not closes:
            stats[tenor] = None
            continue
        latest = closes[-1]
        stats[tenor] = {
            "high": max(closes),
            "low": min(closes),
            "avg": statistics.fmean(closes),
            "stddev": statistics.pstdev(closes),
            "percentile": sum(1 for close in closes if close < latest) / len(closes),
        }
    return stats


def _build_current(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Latest-session card: per-tenor closes, term spread, null-skipping 1d changes."""
    if not series:
        return None
    latest = series[-1]
    current: dict[str, Any] = {"date": latest["date"]}
    for tenor in _TENOR_KEYS:
        current[tenor] = latest.get(tenor)
    cor1m, cor1y = current.get("cor1m"), current.get("cor1y")
    current["term_spread"] = (
        cor1y - cor1m if cor1m is not None and cor1y is not None else None
    )
    changes: dict[str, Optional[float]] = {}
    for tenor in _TENOR_KEYS:
        closes = _tenor_closes(series, tenor)
        changes[tenor] = closes[-1] - closes[-2] if len(closes) > 1 else None
    current["change_1d"] = changes
    return current


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(
    payload: dict[str, Any], scan_time: str, *, rows_changed: bool
) -> None:
    """Best-effort Turso mirror; data/cor.json is the disk fallback.

    Two writes: the durable per-session cor_history rows (skipped when all
    four Cboe files are unchanged — ~5,180 sessions is the expensive part)
    and the full payload snapshot the Next.js route dbFirstReads. The
    snapshot + heartbeat run EVERY cycle so the staleness banner notices a
    silent writer (cf. feedback_service_health_heartbeat).
    """
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        if rows_changed:
            writer.upsert_cor_rows(payload["series"], recorded_at=scan_time)
        writer.upsert_scan_snapshot("cor", scan_time, payload)
        writer.record_service_health("cor", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[cor] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    COR_JSON.parent.mkdir(parents=True, exist_ok=True)
    COR_JSON.write_text(json.dumps(payload, indent=2))


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(COR_JSON.read_text())
    except (OSError, ValueError):
        return None


# ── orchestration ─────────────────────────────────────────────────

def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch all four Cboe tenors, compute, cache, and return the COR payload.

    Conditional GET on ALL four files using the cached per-file stamps: when
    all four come back 304 the cached payload is reused with a fresh
    scan_time and only the snapshot + heartbeat refresh (rows unchanged ⇒
    skip the row upserts). Any change rebuilds the full series.
    """
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.cboe_client import CboeClient
        client = CboeClient()

    cached = _read_json_cache()
    cached_stamps = (cached or {}).get("source_last_modified") or {}
    texts: dict[str, Optional[str]] = {}
    stamps: dict[str, Optional[str]] = {}
    for symbol in _TENORS:
        text, last_modified = client.fetch_history(
            symbol, if_modified_since=cached_stamps.get(symbol.lower())
        )
        texts[symbol] = text
        stamps[symbol.lower()] = last_modified

    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if cached and all(text is None for text in texts.values()):
        print("[cor] all sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        _write_db_cache(payload, scan_time, rows_changed=False)
        _write_json_cache(payload)
        return payload

    for symbol in _TENORS:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)

    series = merge_series(
        {symbol.lower(): parse_index_csv(texts[symbol]) for symbol in _TENORS}
    )
    if not series:
        raise ValueError("cor series computed to zero rows")

    payload = {
        "scan_time": scan_time,
        "source_last_modified": stamps,
        "count": len(series),
        "current": _build_current(series),
        "stats": compute_stats(series),
        "series": series,
    }
    _write_db_cache(payload, scan_time, rows_changed=True)
    _write_json_cache(payload)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    print(f"\nCOR — {payload['count']} sessions through {current.get('date')}", file=sys.stderr)
    for tenor in _TENOR_KEYS:
        value = current.get(tenor)
        line = f"  {tenor}  {value:.2f}" if value is not None else f"  {tenor}  n/a"
        tenor_stats = stats.get(tenor)
        if tenor_stats:
            line += f"  (pctile {tenor_stats['percentile'] * 100:.1f}%)"
        print(line, file=sys.stderr)
    spread = current.get("term_spread")
    if spread is not None:
        print(f"  1Y-1M spread  {spread:+.2f}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPX implied correlation (Cboe COR1M/COR3M/COR6M/COR1Y)"
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

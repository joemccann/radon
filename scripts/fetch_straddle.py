#!/usr/bin/env python3
"""STRADDLE Indicator — SPX realized vs implied 1-day straddle ratio.

Daily signed ratio of the SPX close-to-close move to the option-implied
1-day straddle at the prior close (Brenner-Subrahmanyam: straddle ≈ 0.8 σ√T,
σ from the prior session's VIX1D close, de-annualized on the 252-trading-day
convention). |ratio| > 1 means the realized move beat the straddle breakeven
— a long 1-day straddle bought at the prior close paid off.

Source: Cboe CDN daily-price CSVs (SPX_History.csv + VIX1D_History.csv).
VIX1D history starts 2022-05-13 and is the binding constraint; the series
covers the common dates of both files. Spec: docs/indicators/straddle.md.

Output is dual-written to Turso straddle_history + data/straddle.json.

Usage:
    python3 scripts/fetch_straddle.py            # human summary (stderr)
    python3 scripts/fetch_straddle.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
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
STRADDLE_JSON = _PROJECT_DIR / "data" / "straddle.json"

# Brenner-Subrahmanyam ATM-straddle approximation: straddle ≈ 0.8 σ√T of spot.
_SQRT_2_OVER_PI = math.sqrt(2.0 / math.pi)
_TRADING_DAYS_PER_YEAR = 252

_SPX_SYMBOL = "SPX"
_VIX1D_SYMBOL = "VIX1D"
_SYMBOLS = (_SPX_SYMBOL, _VIX1D_SYMBOL)


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


# ── straddle math ─────────────────────────────────────────────────

def implied_straddle_pct(vix1d: float) -> float:
    """Implied 1-day ATM straddle as % of spot, from an annualized VIX1D close."""
    return _SQRT_2_OVER_PI * (vix1d / 100.0) * math.sqrt(1.0 / _TRADING_DAYS_PER_YEAR) * 100.0


def compute_series(
    spx_rows: list[dict[str, Any]], vix1d_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ratio series over the common dates of both legs, ascending.

    ratio_t = move_pct_t / implied_straddle_pct(VIX1D_{t-1}) where t-1 is the
    prior COMMON session (Friday's VIX1D covers the weekend gap to Monday by
    construction). The first common date has no prior session → ratio None.
    SPX-only dates (pre-2022, calendar mismatches) are excluded.
    """
    spx_by_date = {row["date"]: row["value"] for row in spx_rows}
    vix1d_by_date = {row["date"]: row["value"] for row in vix1d_rows}
    common = sorted(set(spx_by_date) & set(vix1d_by_date))

    series: list[dict[str, Any]] = []
    for prior, date in zip([None, *common[:-1]], common):
        ratio = None
        if prior is not None:
            move_pct = (spx_by_date[date] / spx_by_date[prior] - 1.0) * 100.0
            ratio = move_pct / implied_straddle_pct(vix1d_by_date[prior])
        series.append(
            {
                "date": date,
                "spx_close": spx_by_date[date],
                "vix1d_close": vix1d_by_date[date],
                "ratio": ratio,
            }
        )
    return series


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, float]]:
    """{high, low, avg, stddev (population), hit_rate} over non-null ratios."""
    ratios = [row["ratio"] for row in series if row["ratio"] is not None]
    if not ratios:
        return None
    return {
        "high": max(ratios),
        "low": min(ratios),
        "avg": statistics.fmean(ratios),
        "stddev": statistics.pstdev(ratios),
        "hit_rate": sum(1 for r in ratios if abs(r) > 1.0) / len(ratios),
    }


def _build_current(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Latest-session card: the move, the breakeven it faced, and the ratio."""
    if not series:
        return None
    latest = series[-1]
    prior = series[-2] if len(series) > 1 else None
    return {
        "date": latest["date"],
        "ratio": latest["ratio"],
        "move_pct": (
            (latest["spx_close"] / prior["spx_close"] - 1.0) * 100.0 if prior else None
        ),
        "implied_straddle_pct": (
            implied_straddle_pct(prior["vix1d_close"]) if prior else None
        ),
        "spx_close": latest["spx_close"],
        "vix1d_prior": prior["vix1d_close"] if prior else None,
    }


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(
    payload: dict[str, Any], scan_time: str, *, rows_changed: bool
) -> None:
    """Best-effort Turso mirror; data/straddle.json is the disk fallback.

    Two writes: the durable per-session straddle_history rows (skipped when
    both Cboe files are unchanged — ~1,060 sessions is the expensive part)
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
            writer.upsert_straddle_rows(payload["series"], recorded_at=scan_time)
        writer.upsert_scan_snapshot("straddle", scan_time, payload)
        writer.record_service_health("straddle", "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[straddle] db cache non-fatal: {exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    STRADDLE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STRADDLE_JSON.write_text(json.dumps(payload, indent=2))


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(STRADDLE_JSON.read_text())
    except (OSError, ValueError):
        return None


# ── orchestration ─────────────────────────────────────────────────

def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch both Cboe legs, compute, cache, and return the straddle payload.

    Conditional GET on BOTH files using the cached per-file stamps: when both
    come back 304 the cached payload is reused with a fresh scan_time and
    only the snapshot + heartbeat refresh (rows unchanged ⇒ skip the row
    upserts). Any change rebuilds the full series.
    """
    now = now or datetime.now(timezone.utc)
    if client is None:
        from clients.cboe_client import CboeClient
        client = CboeClient()

    cached = _read_json_cache()
    cached_stamps = (cached or {}).get("source_last_modified") or {}
    texts: dict[str, Optional[str]] = {}
    stamps: dict[str, Optional[str]] = {}
    for symbol in _SYMBOLS:
        text, last_modified = client.fetch_history(
            symbol, if_modified_since=cached_stamps.get(symbol.lower())
        )
        texts[symbol] = text
        stamps[symbol.lower()] = last_modified

    scan_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    if cached and all(text is None for text in texts.values()):
        print("[straddle] both sources unchanged (304); refreshing snapshot only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        _write_db_cache(payload, scan_time, rows_changed=False)
        _write_json_cache(payload)
        return payload

    for symbol in _SYMBOLS:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)

    series = compute_series(
        parse_index_csv(texts[_SPX_SYMBOL], "SPX"),
        parse_index_csv(texts[_VIX1D_SYMBOL], "CLOSE"),
    )
    if not series:
        raise ValueError("straddle series computed to zero rows")

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
    ratio = current.get("ratio")
    print(f"\nSTRADDLE — {payload['count']} sessions through {current.get('date')}", file=sys.stderr)
    print(f"  ratio  {ratio:+.2f}" if ratio is not None else "  ratio  n/a", file=sys.stderr)
    if stats:
        print(
            f"  avg {stats['avg']:+.3f}  pstdev {stats['stddev']:.3f}  "
            f"hit rate {stats['hit_rate'] * 100:.1f}%",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SPX realized vs implied 1-day straddle ratio (Cboe SPX + VIX1D)"
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

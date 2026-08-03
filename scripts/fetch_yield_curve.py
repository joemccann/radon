#!/usr/bin/env python3
"""10Y-2Y Treasury Yield Curve Indicator — official CMT par yield spread.

Builds the daily 10Y minus 2Y constant-maturity spread series (US Treasury
daily par yield curve, 1990+) with an S&P 500 overlay for the CURVE regime
chart. Inversion (spread below zero) is the classic late-cycle regime marker.

Source is the treasury.gov per-year CSV — the official CMT curve is a
government statistic, not a tradable instrument, so IB/UW have no equivalent
and treasury.gov is the primary source, not a fallback skip. The SPX overlay
comes from Yahoo daily closes and is strictly best-effort.

Daily runs fetch only the current year and merge over the data/yield_curve.json
cache; --backfill refetches every year 1990..current. Output is dual-written
to Turso yield_curve_history + data/yield_curve.json.

Usage:
    python3 scripts/fetch_yield_curve.py             # human summary (stderr)
    python3 scripts/fetch_yield_curve.py --json      # JSON to stdout
    python3 scripts/fetch_yield_curve.py --backfill  # refetch 1990..current
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO
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
except Exception:
    pass

from db import writer

# ── constants ─────────────────────────────────────────────────────
YIELD_CURVE_JSON = _PROJECT_DIR / "data" / "yield_curve.json"

FIRST_YEAR = 1990

TREASURY_CSV_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&_format=csv"
)

_TENOR_COLUMNS = {"3 Mo": "y3m", "2 Yr": "y2", "10 Yr": "y10"}
_SPX_PERIOD1 = int(datetime(FIRST_YEAR, 1, 1, tzinfo=timezone.utc).timestamp())


# ── Treasury CSV ──────────────────────────────────────────────────

def fetch_year_csv(year: int, session: Optional[Any] = None) -> str:
    import requests

    http = session or requests
    resp = http.get(
        TREASURY_CSV_URL.format(year=year),
        headers={"User-Agent": "radon/2.0"},
        timeout=30,
    )
    resp.raise_for_status()
    if not resp.text.strip():
        raise ValueError(f"Treasury CSV for {year} is empty")
    return resp.text


def _iso_date(mmddyyyy: str) -> str:
    month, day, year = mmddyyyy.split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def _tenor_value(record: dict[str, str], header: str) -> Optional[float]:
    cell = (record.get(header) or "").strip()
    return float(cell) if cell else None


def parse_treasury_csv(text: str) -> list[dict[str, Any]]:
    """Parse a treasury.gov per-year CSV into ascending {date, y3m, y2, y10}.

    Columns are mapped by header NAME — the tenor set varies by year, so
    positional parsing would silently misread older files. An absent column
    or empty cell reads as None; rows missing y2 or y10 are skipped.
    """
    rows: list[dict[str, Any]] = []
    for record in csv.DictReader(StringIO(text)):
        row: dict[str, Any] = {"date": _iso_date(record["Date"])}
        for header, key in _TENOR_COLUMNS.items():
            row[key] = _tenor_value(record, header)
        if row["y2"] is None or row["y10"] is None:
            continue
        rows.append(row)

    rows.sort(key=lambda r: r["date"])
    return rows


def compute_spread(row: dict[str, Any]) -> float:
    return row["y10"] - row["y2"]


# ── SPX overlay (best-effort) ─────────────────────────────────────

def fetch_spx_daily_closes() -> Optional[dict[str, float]]:
    """Daily ^GSPC closes from Yahoo, keyed YYYY-MM-DD. None on any failure.

    Explicit period1/period2 epochs — range=max silently degrades to coarse
    bars. Browser UA — the plain UA gets 429.
    """
    import requests

    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC"
        f"?period1={_SPX_PERIOD1}"
        f"&period2={int(datetime.now(timezone.utc).timestamp())}&interval=1d"
    )
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as exc:  # noqa: BLE001 — overlay is optional
        print(f"[yield-curve] SPX fetch failed (non-fatal): {exc}", file=sys.stderr)
        return None

    by_date: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        by_date[stamp.strftime("%Y-%m-%d")] = float(close)
    return by_date


# ── series assembly ───────────────────────────────────────────────

def build_series(
    rows: list[dict[str, Any]], spx_closes: Optional[dict[str, float]]
) -> list[dict[str, Any]]:
    closes = spx_closes or {}
    return [
        {**row, "spread": compute_spread(row), "spx_close": closes.get(row["date"])}
        for row in rows
    ]


def merge_series(
    cached_series: list[dict[str, Any]], fresh_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fresh wins per date; keeps the cached backfill (daily runs fetch only
    the current year)."""
    by_date = {row["date"]: row for row in cached_series}
    by_date.update({row["date"]: row for row in fresh_rows})
    return [by_date[d] for d in sorted(by_date)]


def _db_fields(row: dict[str, Any]) -> tuple:
    return (row["date"], row.get("y3m"), row["y2"], row["y10"], row["spread"])


def diff_new_rows(
    cached_series: list[dict[str, Any]], series: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rows absent from or different in the cache — drives the DB row upserts.

    Compared on the persisted yield fields only, so an SPX overlay hiccup
    can't masquerade as fresh Treasury data.
    """
    cached_by_date = {row["date"]: row for row in cached_series}
    return [
        row
        for row in series
        if row["date"] not in cached_by_date
        or _db_fields(cached_by_date[row["date"]]) != _db_fields(row)
    ]


def build_output(
    series: list[dict[str, Any]], scan_time: Optional[str] = None
) -> dict[str, Any]:
    stamp = scan_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    latest = series[-1] if series else None
    current = (
        {key: latest[key] for key in ("date", "y3m", "y2", "y10", "spread")}
        if latest
        else None
    )
    return {
        "scan_time": stamp,
        "source": "treasury",
        "count": len(series),
        "current": current,
        "series": series,
    }


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    YIELD_CURVE_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = YIELD_CURVE_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, YIELD_CURVE_JSON)


def persist_result(
    payload: dict[str, Any], rows_changed_rows: list[dict[str, Any]]
) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    Refuses an empty series entirely — a bad fetch must never clobber the
    cache or heartbeat as healthy. The snapshot + heartbeat run EVERY cycle
    so the staleness banner notices a silent writer
    (cf. feedback_service_health_heartbeat).
    """
    if not payload["series"]:
        print("[yield-curve] refusing to persist empty series", file=sys.stderr)
        return

    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows_changed_rows:
        writer.upsert_yield_curve_rows(rows_changed_rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot("yield-curve", scan_time, payload)
    writer.record_service_health("yield-curve", "ok", finished_at=scan_time)
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────

def _read_cached_series() -> list[dict[str, Any]]:
    try:
        return json.loads(YIELD_CURVE_JSON.read_text())["series"]
    except (OSError, ValueError, KeyError):
        return []


def _fetch_years(years: list[int]) -> list[dict[str, Any]]:
    import requests

    rows: list[dict[str, Any]] = []
    with requests.Session() as session:
        for year in years:
            print(f"[yield-curve] fetching {year}", file=sys.stderr)
            rows.extend(parse_treasury_csv(fetch_year_csv(year, session)))
    return rows


def run(backfill: bool = False) -> dict[str, Any]:
    current_year = datetime.now(timezone.utc).year
    years = list(range(FIRST_YEAR, current_year + 1)) if backfill else [current_year]

    fresh = build_series(_fetch_years(years), fetch_spx_daily_closes())
    cached = _read_cached_series()
    series = merge_series(cached, fresh)
    new_rows = diff_new_rows(cached, series)

    if not new_rows:
        print("[yield-curve] source unchanged; refreshing snapshot only", file=sys.stderr)

    payload = build_output(series)
    persist_result(payload, new_rows)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nYIELD CURVE — {payload['count']} sessions", file=sys.stderr)
    if current:
        print(f"  latest {current['date']}", file=sys.stderr)
        print(f"  10Y-2Y {current['spread']:+.2f}%", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="10Y-2Y Treasury yield-curve spread series")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--backfill", action="store_true", help="Refetch every year 1990..current")
    args = parser.parse_args()

    payload = run(backfill=args.backfill)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

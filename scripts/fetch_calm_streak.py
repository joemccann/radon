#!/usr/bin/env python3
"""CALM STREAK: consecutive SPX sessions without a >1% intraday band.

Spec: docs/indicators/calm-streak.md. Source: Cboe official delayed-quotes
historical _SPX.json (daily OHLC from 1975). IB SPX daily bars start 2016 and
UW has no SPX index history, so Cboe is the documented source; no Yahoo rung.

    band_pct(t) = 100 * (high(t) - low(t)) / close(t-1)
    streak(t)   = 0 if band_pct(t) > 1.0 else streak(t-1) + 1

Dual-written to Turso calm_streak_history + scan_snapshots and
data/calm_streak.json.

Usage:
    python3 scripts/fetch_calm_streak.py            # summary (stderr)
    python3 scripts/fetch_calm_streak.py --json     # payload to stdout
    python3 scripts/fetch_calm_streak.py --no-db    # skip Turso writes
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from db import writer  # noqa: E402
from utils.atomic_io import atomic_save  # noqa: E402
from utils.market_calendar import last_completed_session_date  # noqa: E402

SERVICE = "calm-streak"
CBOE_SPX_URL = "https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_SPX.json"
THRESHOLD_PCT = 1.0
SERIES_START = "1985-01-01"
WINDOW_START = "1996-01-01"
WINDOW_END = "2016-12-31"
CALM_STREAK_JSON = _PROJECT_DIR / "data" / "calm_streak.json"
SCHEMA_VERSION = 1
FETCH_TIMEOUT_S = 30
USER_AGENT = "radon/2.0"


# ── pure transforms ───────────────────────────────────────────────

def parse_cboe_history(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Cboe string rows -> float OHLC rows, ascending. open <= 0 -> None;
    rows with a non-positive or unparseable high/low/close are dropped."""
    rows: list[dict[str, Any]] = []
    for raw in (payload or {}).get("data") or []:
        try:
            high, low, close = float(raw["high"]), float(raw["low"]), float(raw["close"])
            open_ = float(raw.get("open") or 0)
            day = str(raw["date"])[:10]
        except (KeyError, TypeError, ValueError):
            continue
        if high <= 0 or low <= 0 or close <= 0:
            continue
        rows.append({"date": day, "open": open_ if open_ > 0 else None,
                     "high": high, "low": low, "close": close})
    rows.sort(key=lambda r: r["date"])
    return rows


def completed_sessions(rows: list[dict[str, Any]], last_completed: str) -> list[dict[str, Any]]:
    return [r for r in rows if r["date"] <= last_completed]


def compute_streaks(rows: list[dict[str, Any]], threshold_pct: float = THRESHOLD_PCT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    streak = 0
    for prev, cur in zip(rows, rows[1:]):
        band = 100.0 * (cur["high"] - cur["low"]) / prev["close"]
        streak = 0 if band > threshold_pct else streak + 1
        out.append({**cur, "band_pct": round(band, 4), "streak": streak})
    return out


def weekly_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    last_week = None
    for r in rows:
        week = _date.fromisoformat(r["date"]).isocalendar()[:2]
        if week != last_week:
            out.append({"date": r["date"], "streak": r["streak"], "close": r["close"]})
            last_week = week
        else:
            peak = out[-1]
            peak["date"], peak["close"] = r["date"], r["close"]
            peak["streak"] = max(peak["streak"], r["streak"])
    return out


def _max_row(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    best = None
    for r in rows:  # ascending, so >= makes the latest date win a tie
        if best is None or r["streak"] >= best["streak"]:
            best = r
    return best


def compute_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    published = [r for r in rows if r["date"] >= SERIES_START]
    best = _max_row(published)
    win = _max_row([r for r in published if WINDOW_START <= r["date"] <= WINDOW_END])
    percentile = None
    if published:
        current = published[-1]["streak"]
        below = sum(1 for r in published if r["streak"] < current)
        percentile = round(100.0 * below / len(published), 1)
    return {
        "max": {"streak": best["streak"], "date": best["date"]} if best else None,
        "window": {"start": WINDOW_START, "end": WINDOW_END,
                   "streak": win["streak"] if win else None,
                   "date": win["date"] if win else None},
        "percentile": percentile,
    }


def rows_to_upsert(rows: list[dict[str, Any]], latest_stored_date: Optional[str]) -> list[dict[str, Any]]:
    return [r for r in rows
            if r["date"] >= SERIES_START and (latest_stored_date is None or r["date"] > latest_stored_date)]


def build_output(rows: list[dict[str, Any]], *, scan_time: str,
                 source_last_modified: Optional[str]) -> dict[str, Any]:
    published = [r for r in rows if r["date"] >= SERIES_START]
    base = {
        "schema_version": SCHEMA_VERSION,
        "scan_time": scan_time,
        "source_last_modified": source_last_modified,
        "source": {"name": "cboe", "url": CBOE_SPX_URL},
        "threshold_pct": THRESHOLD_PCT,
    }
    if len(published) < 2:
        return {**base, "data_date": None, "current": None, "stats": None, "series": [],
                "missing": True, "reason": "insufficient_history"}
    latest = published[-1]
    return {
        **base,
        "data_date": latest["date"],
        "current": {"date": latest["date"], "streak": latest["streak"],
                    "band_pct": latest["band_pct"], "close": latest["close"]},
        "stats": compute_stats(published),
        "series": weekly_peaks(published),
        "missing": False,
    }


# ── IO seams ──────────────────────────────────────────────────────

def _fetch_source(if_modified_since: Optional[str]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """(payload, Last-Modified); payload None means HTTP 304."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    try:
        with urlopen(Request(CBOE_SPX_URL, headers=headers), timeout=FETCH_TIMEOUT_S) as resp:
            return json.load(resp), resp.headers.get("Last-Modified")
    except HTTPError as exc:
        if exc.code == 304:
            return None, if_modified_since
        raise


def _latest_stored_date() -> Optional[str]:
    try:
        row = writer.get_db().execute("SELECT MAX(date) FROM calm_streak_history").fetchone()
    except Exception as exc:  # noqa: BLE001 — unknown max means backfill everything
        print(f"[calm-streak] stored max-date probe non-fatal: {exc}", file=sys.stderr)
        return None
    return row[0] if row and row[0] else None


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        cached = json.loads(Path(CALM_STREAK_JSON).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or cached.get("missing"):
        return None
    cached.pop("_checksum", None)
    return cached


def _write_json_cache(payload: dict[str, Any]) -> None:
    Path(CALM_STREAK_JSON).parent.mkdir(parents=True, exist_ok=True)
    atomic_save(str(CALM_STREAK_JSON), dict(payload))


def _record_error(scan_time: str, message: str) -> None:
    try:
        writer.record_service_health(SERVICE, "error", finished_at=scan_time, error={"message": message[:500]})
    except Exception as exc:  # noqa: BLE001
        print(f"[calm-streak] error heartbeat non-fatal: {exc}", file=sys.stderr)


# ── persistence + orchestration ───────────────────────────────────

def persist_result(payload: dict[str, Any], rows: list[dict[str, Any]], *, use_db: bool = True) -> None:
    scan_time = payload["scan_time"]
    if use_db:
        writer.ensure_no_replica_for_writers()
        if rows:
            writer.upsert_calm_streak_rows(rows, recorded_at=scan_time)
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    _write_json_cache(payload)


def run(*, now: Optional[datetime] = None, use_db: bool = True) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    scan_time = now.astimezone(timezone.utc).isoformat()

    cached = _read_json_cache()
    ims = cached.get("source_last_modified") if cached else None
    try:
        source, last_modified = _fetch_source(ims)
        if source is None and not cached:
            source, last_modified = _fetch_source(None)
    except Exception as exc:  # noqa: BLE001 — keep last-good snapshot
        if use_db:
            _record_error(scan_time, f"fetch failed: {exc}")
        raise

    if source is None:
        print("[calm-streak] source unchanged (304); heartbeat only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        persist_result(payload, [], use_db=use_db)
        return payload

    rows = compute_streaks(completed_sessions(parse_cboe_history(source),
                                              last_completed_session_date(now)))
    payload = build_output(rows, scan_time=scan_time, source_last_modified=last_modified)
    if payload["missing"]:
        if use_db:
            _record_error(scan_time, payload["reason"])
        return payload

    new_rows = rows_to_upsert(rows, _latest_stored_date() if use_db else None)
    persist_result(payload, new_rows if use_db else [], use_db=use_db)
    print(f"[calm-streak] {payload['data_date']} streak={payload['current']['streak']} "
          f"rows_upserted={len(new_rows) if use_db else 0}", file=sys.stderr)
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CALM STREAK: SPX sessions without a >1% intraday band")
    parser.add_argument("--json", action="store_true", help="payload JSON to stdout")
    parser.add_argument("--no-db", action="store_true", help="skip Turso writes")
    args = parser.parse_args(argv)
    try:
        payload = run(use_db=not args.no_db)
    except Exception as exc:  # noqa: BLE001
        print(f"[calm-streak] run failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, indent=2))
    if payload.get("missing"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

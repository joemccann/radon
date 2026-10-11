#!/usr/bin/env python3
"""Descriptive TradingView SPX breadth variant; not exact StockCharts parity.

S5TH is published directly. Net highs/lows is explicitly normalized per
500, an unverified denominator. See docs/indicators/record-high-breadth-source-parity.md.
No historical analogues, all-time-high claims, or binary trading signals.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from clients.tradingview_client import TradingViewClient
from db import writer
from db.service_cycle import service_cycle
from utils.atomic_io import atomic_save
from utils.market_calendar import last_completed_session_date

SERVICE = "record-high-breadth"
CACHE_PATH = _PROJECT_DIR / "data" / "record_high_breadth.json"
SYMBOLS = {"spx_close": "SP:SPX", "above_200_pct": "INDEX:S5TH",
           "new_highs": "INDEX:MAHP", "new_lows": "INDEX:MALP"}
NY = ZoneInfo("America/New_York")
MAX_BARS = 5000
COLLECTION_TIMEOUT = 180
NORMALIZATION_BASE = 500


def _number(value, *, count=False, percentage=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("breadth source must contain finite numeric closes")
    if value < 0 or (count and value != int(value)) or (percentage and value > 100):
        raise ValueError("breadth close has invalid count or percentage units")
    return int(value) if count else float(value)


def session_date(timestamp):
    """Keep the provider's session anchor while joining on New York dates."""
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp != int(timestamp):
        raise ValueError("invalid provider timestamp")
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(NY).date().isoformat()


def normalize_source(payload, field, now, invalid_observations=None):
    if payload.get("symbol") != SYMBOLS[field] or payload.get("interval") != "1D":
        raise ValueError("unexpected breadth source identity or interval")
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars or len(bars) > MAX_BARS:
        raise ValueError("missing or oversized breadth history")
    today = now.astimezone(NY)
    rows = {}
    for bar in bars:
        day = session_date(bar["timestamp"])
        value = None
        try:
            value = _number(bar["close"], count=field in {"new_highs", "new_lows"},
                            percentage=field == "above_200_pct")
        except ValueError as exc:
            if invalid_observations is not None:
                raw = bar.get("close")
                invalid_observations.append({"date": day, "field": field,
                    "timestamp": int(bar["timestamp"]),
                    "raw_value": raw if isinstance(raw, (int, float)) and math.isfinite(raw) else repr(raw),
                    "reason": str(exc)})
        if day > today.date().isoformat():
            raise ValueError("future source session")
        # Conservative availability guard, not a claim of provider finality.
        if day == today.date().isoformat() and today.hour < 18:
            continue
        if day in rows:
            raise ValueError("duplicate source session")
        rows[day] = {"value": value, "timestamp": int(bar["timestamp"])}
    return rows


def build_output(sources, *, now):
    if now.tzinfo is None:
        raise ValueError("collection time must be timezone-aware")
    invalid_observations = []
    normalized = {key: normalize_source(sources[key], key, now, invalid_observations) for key in SYMBOLS}
    dates = sorted(set().union(*(set(rows) for rows in normalized.values())))
    series = []
    joined = []
    missing_sessions = []
    for day in dates:
        point = {"date": day}
        timestamps = {}
        missing = []
        for field, values in normalized.items():
            row = values.get(day)
            point[field] = row["value"] if row else None
            timestamps[field] = row["timestamp"] if row else None
            if row is None or row["value"] is None:
                missing.append(field)
        point["source_timestamps"] = timestamps
        point["net_high_low_per_500"] = (
            100.0 * (point["new_highs"] - point["new_lows"]) / NORMALIZATION_BASE
            if point["new_highs"] is not None and point["new_lows"] is not None else None
        )
        series.append(point)
        if missing:
            missing_sessions.append({"date": day, "fields": missing})
        else:
            joined.append(point)
    coverage = {}
    for field, rows in normalized.items():
        days = sorted(rows)
        coverage[field] = {"symbol": SYMBOLS[field], "first_date": days[0] if days else None,
                           "last_date": days[-1] if days else None,
                           "returned_count": len(sources[field]["bars"]),
                           "accepted_count": sum(row["value"] is not None for row in rows.values()),
                           "fetched_at": sources[field].get("fetched_at")}
    reference = {"spx_close": 7650.50, "above_200_pct": 52.0, "net_high_low_pct": -4.4}
    reference_point = next((point for point in joined if point["date"] == "2026-09-18"), None)
    observed = ({field: reference_point[field] for field in ("spx_close", "above_200_pct", "net_high_low_per_500")}
                if reference_point else None)
    parity_status = ("readings_match" if observed and observed["spx_close"] == reference["spx_close"]
                     and observed["above_200_pct"] == reference["above_200_pct"]
                     and observed["net_high_low_per_500"] == reference["net_high_low_pct"]
                     else "mismatch" if observed else "unavailable")
    expected = last_completed_session_date(now - timedelta(hours=2))
    lagging = not joined or joined[-1]["date"] < expected
    return {
        "degraded": lagging,
        "schema_version": 1, "scan_time": now.astimezone(timezone.utc).isoformat(),
        "data_date": joined[-1]["date"] if joined else None,
        "current": joined[-1] if joined else None, "series": series, "missing": not joined,
        "source": {"provider": "tradingview", "symbols": dict(SYMBOLS),
                   "history_complete": False, "denominator": NORMALIZATION_BASE,
                   "denominator_verified": False, "source_finality": "unverified",
                   "session_cutoff": "18:00 America/New_York", "coverage": coverage,
                   "latest_observed_date": dates[-1] if dates else None,
                   "expected_session_date": expected, "lagging": lagging,
                   "missing_sessions": missing_sessions,
                   "invalid_observations": invalid_observations,
                   "parity": {"status": parity_status, "reference_date": "2026-09-18",
                              "reference": reference, "tradingview": observed,
                              "count_universe_verified": False,
                              "receipt": "docs/indicators/record-high-breadth-source-probe.json"}},
    }


async def collect(client=None, *, now=None):
    client = client or TradingViewClient()
    now = now or datetime.now(timezone.utc)
    # Sequential calls avoid concurrent OAuth refresh and credential writes.
    sources = {}
    async with asyncio.timeout(COLLECTION_TIMEOUT):
        for field, symbol in SYMBOLS.items():
            sources[field] = await client.get_ohlcv(symbol, interval="1D", count=MAX_BARS)
    return build_output(sources, now=now)


def persist_result(payload):
    if payload["missing"]:
        raise RuntimeError("no aligned finalized-session observations; retaining prior snapshot")
    writer.ensure_no_replica_for_writers()
    complete_rows = [row for row in payload["series"] if all(row[field] is not None for field in SYMBOLS)]
    writer.upsert_record_high_breadth_rows(complete_rows, recorded_at=payload["scan_time"])
    writer.upsert_scan_snapshot(SERVICE, payload["scan_time"], payload)
    atomic_save(str(CACHE_PATH), payload)


def run(*, client=None):
    with service_cycle(SERVICE, market_hours_class="daily") as cycle:
        payload = asyncio.run(collect(client))
        persist_result(payload)
        cycle.finished_at = payload["scan_time"]
        if payload["degraded"]:
            raise RuntimeError("latest expected session is incomplete; retained observations are degraded")
        return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = run()
    except Exception as exc:
        print(f"[{SERVICE}] {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"[{SERVICE}] {payload['data_date']}: {len(payload['series'])} sessions; parity {payload['source']['parity']['status']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Daily USI ASK minus BID trade-count imbalance.

TradingView titles these NUMBER OF TRADES, not share volume. BATD is a
parity check only and is never substituted for ASK-BID. See
docs/indicators/imbalance.md.
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

SERVICE = "imbalance"
CACHE_PATH = _PROJECT_DIR / "data" / "imbalance.json"
SYMBOLS = {
    "ask": "USI:TRDS.ASK.US",
    "bid": "USI:TRDS.BID.US",
    "batd": "USI:BATD.US",
    "spx": "SP:SPX",
}
SHARE_VOLUME_SYMBOLS = ("USI:VOL.ASK.US", "USI:VOL.BID.US", "USI:BAVD.US")
NY = ZoneInfo("America/New_York")
MAX_BARS = 5000
COLLECTION_TIMEOUT = 180
EMA_PERIOD = 10
EMA_K = 2 / (EMA_PERIOD + 1)
COUNT_FIELDS = {"ask", "bid", "batd"}
NON_NEGATIVE_FIELDS = {"ask", "bid", "spx"}


def _number(value, *, count=False, non_negative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("imbalance source must contain finite numeric closes")
    if non_negative and value < 0:
        raise ValueError("imbalance close has invalid sign")
    if count and value != int(value):
        raise ValueError("imbalance close has invalid count units")
    if not count and value <= 0:
        raise ValueError("imbalance close has invalid price units")
    return int(value) if count else float(value)


def session_date(timestamp):
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or timestamp != int(timestamp):
        raise ValueError("invalid provider timestamp")
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(NY).date().isoformat()


def ema10_series(values):
    """TradingView EMA: SMA seed of the first 10 finite values, then k=2/11.

    Missing observations stay None and do not enter the window as zero.
    """
    out = [None] * len(values)
    seed = []
    ema = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if ema is None:
            seed.append(value)
            if len(seed) == EMA_PERIOD:
                ema = sum(seed) / EMA_PERIOD
                out[i] = ema
        else:
            ema = (value - ema) * EMA_K + ema
            out[i] = ema
    return out


def normalize_source(payload, field, now, invalid_observations=None):
    if payload.get("symbol") != SYMBOLS[field] or payload.get("interval") != "1D":
        raise ValueError("unexpected imbalance source identity or interval")
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars or len(bars) > MAX_BARS:
        raise ValueError("missing or oversized imbalance history")
    today = now.astimezone(NY)
    rows = {}
    for bar in bars:
        day = session_date(bar["timestamp"])
        value = None
        try:
            value = _number(
                bar["close"],
                count=field in COUNT_FIELDS,
                non_negative=field in NON_NEGATIVE_FIELDS,
            )
        except ValueError as exc:
            if invalid_observations is not None:
                raw = bar.get("close")
                invalid_observations.append({
                    "date": day, "field": field, "timestamp": int(bar["timestamp"]),
                    "raw_value": raw if isinstance(raw, (int, float)) and math.isfinite(raw) else repr(raw),
                    "reason": str(exc),
                })
        if day > today.date().isoformat():
            raise ValueError("future source session")
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
    parity_mismatches = []
    for day in dates:
        point = {"date": day}
        timestamps = {}
        missing = []
        for field, values in normalized.items():
            row = values.get(day)
            key = "spx_close" if field == "spx" else field
            point[key] = row["value"] if row else None
            timestamps[field] = row["timestamp"] if row else None
            if row is None or row["value"] is None:
                missing.append(field)
        point["source_timestamps"] = timestamps
        if point["ask"] is not None and point["bid"] is not None:
            point["imbalance"] = point["ask"] - point["bid"]
            if point["batd"] is not None and point["batd"] != point["imbalance"]:
                parity_mismatches.append({
                    "date": day, "ask": point["ask"], "bid": point["bid"],
                    "imbalance": point["imbalance"], "batd": point["batd"],
                })
        else:
            point["imbalance"] = None
        point["ema10"] = None
        series.append(point)
        if missing:
            missing_sessions.append({"date": day, "fields": missing})
        if point["imbalance"] is not None:
            joined.append(point)
    for index, value in enumerate(ema10_series([row["imbalance"] for row in series])):
        series[index]["ema10"] = value
    coverage = {}
    for field, rows in normalized.items():
        days = sorted(rows)
        coverage[field] = {
            "symbol": SYMBOLS[field],
            "first_date": days[0] if days else None,
            "last_date": days[-1] if days else None,
            "returned_count": len(sources[field]["bars"]),
            "accepted_count": sum(row["value"] is not None for row in rows.values()),
            "fetched_at": sources[field].get("fetched_at"),
        }
    expected = last_completed_session_date(now - timedelta(hours=2))
    lagging = not joined or joined[-1]["date"] < expected
    return {
        "degraded": lagging,
        "schema_version": 1,
        "scan_time": now.astimezone(timezone.utc).isoformat(),
        "data_date": joined[-1]["date"] if joined else None,
        "current": joined[-1] if joined else None,
        "series": series,
        "missing": not joined,
        "source": {
            "provider": "tradingview",
            "symbols": dict(SYMBOLS),
            "units": "trade_count",
            "units_verified": True,
            "share_volume_symbols_not_used": list(SHARE_VOLUME_SYMBOLS),
            "history_complete": False,
            "source_finality": "unverified",
            "universe_verified": False,
            "tick_rule_verified": False,
            "session_coverage_verified": False,
            "session_cutoff": "18:00 America/New_York",
            "ema_period": EMA_PERIOD,
            "coverage": coverage,
            "latest_observed_date": dates[-1] if dates else None,
            "expected_session_date": expected,
            "lagging": lagging,
            "missing_sessions": missing_sessions,
            "invalid_observations": invalid_observations,
            "parity_mismatches": parity_mismatches,
            "citation": "https://x.com/benkizemchuk/status/2119045970805604478",
        },
    }


async def collect(client=None, *, now=None):
    client = client or TradingViewClient()
    now = now or datetime.now(timezone.utc)
    sources = {}
    async with asyncio.timeout(COLLECTION_TIMEOUT):
        for field, symbol in SYMBOLS.items():
            sources[field] = await client.get_ohlcv(symbol, interval="1D", count=MAX_BARS)
    return build_output(sources, now=now)


def persist_result(payload):
    if payload["missing"]:
        raise RuntimeError("no aligned completed-session observations; retaining prior snapshot")
    writer.ensure_no_replica_for_writers()
    complete_rows = [row for row in payload["series"] if row.get("ask") is not None and row.get("bid") is not None]
    writer.upsert_imbalance_rows(complete_rows, recorded_at=payload["scan_time"])
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
        current = payload["current"] or {}
        print(
            f"[{SERVICE}] {payload['data_date']}: imbalance={current.get('imbalance')} "
            f"ema10={current.get('ema10')} sessions={len(payload['series'])}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

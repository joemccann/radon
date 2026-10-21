#!/usr/bin/env python3
"""NYSE common-stock High-Low Line from approved exact StockCharts symbols.

IB and UW do not certify this specialist universe. Published fractional breadth
values are preserved. The zero baseline precedes the first 2003-01-02 reading;
SAR uses daily close-only or weekly daily-cumulative-range OHLC, not vendor parity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from clients.high_low_source_client import HighLowSourceClient, HISTORY_URL, MAX_RESPONSE_BYTES, SYMBOLS
from db import writer
from utils.atomic_io import atomic_save, verified_load
from utils.market_calendar import last_completed_session_date

SERVICE = "high-low-line"
CACHE_PATH = SCRIPT_DIR.parent / "data/high_low_line.json"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
SNAPSHOT_CHUNK_CHARS = 128 * 1024
ET = ZoneInfo("America/New_York")
HISTORY_START = "2003-01-02"
CLOSURES = {"2012-10-29", "2012-10-30"}
NAMES = dict(zip(SYMBOLS, ("NYSE Common Stock Only New Highs", "NYSE Common Stock Only New Lows")))
SETTINGS = {"rsi_period": 14, "stoch_period": 14, "smoothing": "Wilder", "sar_start": 1,
            "sar_increment": 1, "sar_max": 1, "daily_ohlc": "close_only",
            "weekly_ohlc": "daily_cumulative_range", "default_interval": "weekly"}


def _now(now=None):
    value = now or datetime.now(timezone.utc)
    if value.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    return value.astimezone(timezone.utc)


def _date(value):
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise ValueError("Noncanonical session date")
    return result


def _decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Breadth must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Breadth must be numeric") from exc
    if not result.is_finite() or result < 0 or not math.isfinite(float(result)):
        raise ValueError("Breadth must be finite and nonnegative")
    return result


def _timestamp(value):
    if not isinstance(value, dict) or value.get("zone") != "America/New_York":
        raise ValueError("Unexpected source time zone")
    stamp = datetime.strptime(value["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
    if isinstance(value.get("millis"), bool) or value.get("millis") != int(stamp.timestamp() * 1000):
        raise ValueError("Inconsistent source timestamp")
    return stamp


def parse_stockcharts_history(raw, symbol, *, now=None):
    now = _now(now)
    if symbol not in SYMBOLS or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Unsupported source identity or body size")
    data = json.loads(raw, parse_float=Decimal)
    history, quote = data["history"], data["quote"]
    if (history.get("symbol") != symbol or history.get("period") != "D"
            or quote.get("symbol") != symbol or quote.get("name") != NAMES[symbol]):
        raise ValueError("Unexpected common-stock source identity")
    intervals = history.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("Empty source history")
    rows, excluded, seen = [], [], set()
    today = now.astimezone(ET).date()
    for item in intervals:
        start, end = _timestamp(item["start"]), _timestamp(item["end"])
        day = end.date()
        key = day.isoformat()
        if start.date() != day or start >= end or day > today or key in seen:
            raise ValueError("Duplicate, future or inconsistent source date")
        seen.add(key)
        values = [item.get(field) for field in ("open", "high", "low", "close")]
        if all(value is None for value in values) and key in CLOSURES and item.get("source") is None:
            excluded.append(key)
            continue
        if item.get("source") == "OTHER":
            continue  # Intraday rows never become daily completed observations.
        if item.get("source") != "SQL_DAILY" or day.weekday() >= 5 or key in CLOSURES:
            raise ValueError("Unexpected source interval")
        numbers = [_decimal(value) for value in values]
        if len(set(numbers)) != 1 or end.time() != time(16):
            raise ValueError("Daily breadth OHLC or close label changed")
        if now < datetime.combine(day, time(18), ET):
            continue
        rows.append({"date": key, "value": float(numbers[-1])})
    rows.sort(key=lambda row: row["date"])
    if not rows:
        raise ValueError("No completed common-stock observations")
    return {"rows": rows, "non_observations": sorted(excluded)}


def _finite_values(values):
    result = [float(value) for value in values]
    if any(isinstance(value, bool) for value in values) or not all(math.isfinite(x) for x in result):
        raise ValueError("Indicator inputs must be finite")
    return result


def wilder_rsi(values, period=14):
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        raise ValueError("Invalid RSI period")
    values = _finite_values(values)
    result = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [b - a for a, b in zip(values, values[1:])]
    gain = sum(max(x, 0) for x in changes[:period]) / period
    loss = sum(max(-x, 0) for x in changes[:period]) / period
    for index in range(period, len(values)):
        if index > period:
            change = changes[index - 1]
            gain = ((period - 1) * gain + max(change, 0)) / period
            loss = ((period - 1) * loss + max(-change, 0)) / period
        result[index] = 50.0 if gain == loss == 0 else 100 * gain / (gain + loss)
    return result


def stoch_rsi(values, period=14):
    rsi = wilder_rsi(values, period)
    result = [None] * len(rsi)
    for index in range(2 * period - 1, len(rsi)):
        window = rsi[index - period + 1:index + 1]
        low, high = min(window), max(window)
        if high > low:
            result[index] = (rsi[index] - low) / (high - low)
    return result


def parabolic_sar(ohlc, start=1, increment=1, maximum=1):
    params = _finite_values([start, increment, maximum])
    if min(params) <= 0 or start > maximum:
        raise ValueError("Invalid SAR acceleration")
    rows = [{key: value for key, value in zip(("high", "low", "close"),
            _finite_values([row["high"], row["low"], row["close"]]))} for row in ohlc]
    if any(not row["low"] <= row["close"] <= row["high"] for row in rows):
        raise ValueError("Invalid OHLC range")
    result = [(None, None)] * len(rows)
    if len(rows) < 2:
        return result
    rising = rows[1]["close"] >= rows[0]["close"]
    sar = rows[0]["low" if rising else "high"]
    extreme = rows[0]["high" if rising else "low"]
    acceleration = start
    for index in range(1, len(rows)):
        row = rows[index]
        candidate = sar + acceleration * (extreme - sar)
        bounds = rows[max(0, index - 2):index]
        candidate = min(candidate, *(r["low"] for r in bounds)) if rising else max(candidate, *(r["high"] for r in bounds))
        reversal = row["low"] < candidate if rising else row["high"] > candidate
        if reversal:
            sar = extreme
            rising = not rising
            extreme = row["high" if rising else "low"]
            acceleration = start
        else:
            sar = candidate
            next_extreme = row["high" if rising else "low"]
            if (next_extreme > extreme if rising else next_extreme < extreme):
                extreme = next_extreme
                acceleration = min(maximum, acceleration + increment)
        result[index] = (sar, "rising" if rising else "falling")
    return result


def _parsed(values, now):
    rows, seen = [], set()
    for row in values["rows"]:
        day = _date(row["date"])
        key = day.isoformat()
        if (key in seen or day.weekday() >= 5 or key in CLOSURES
                or now < datetime.combine(day, time(18), ET)):
            raise ValueError("Invalid completed session")
        seen.add(key)
        rows.append({"date": key, "value": _decimal(row["value"])})
    rows.sort(key=lambda row: row["date"])
    if not rows or rows[0]["date"] != HISTORY_START:
        raise ValueError("Missing verified source history start")
    excluded = values.get("non_observations", [])
    if not isinstance(excluded, list) or len(set(excluded)) != len(excluded) or not set(excluded) <= CLOSURES:
        raise ValueError("Unknown non-observation")
    return rows, sorted(excluded)


def _oscillators(rows, ohlc):
    values = [row["cumulative"] for row in rows]
    rsi, stoch, sar = wilder_rsi(values), stoch_rsi(values), parabolic_sar(ohlc)
    previous = None
    for row, r, s, (stop, direction) in zip(rows, rsi, stoch, sar):
        row.update(rsi14=r, stoch_rsi14=s, sar=stop, sar_direction=direction,
                   reset_to_zero=s == 0 and previous is not None and previous > 0)
        if s is not None:
            previous = s


def _zone(row):
    value = row["stoch_rsi14"]
    return "unavailable" if value is None else "oversold" if value <= .2 else "overbought" if value >= .8 else "neutral"


def build_output(highs, lows, *, now=None):
    now = _now(now)
    high, excluded = _parsed(highs, now)
    low, low_excluded = _parsed(lows, now)
    if [r["date"] for r in high] != [r["date"] for r in low] or excluded != low_excluded:
        raise ValueError("High/low dates do not match exactly")
    cumulative, daily, weeks = Decimal(0), [], {}
    fractional = 0
    normalized = []
    for hi, lo in zip(high, low):
        key, h, l = hi["date"], hi["value"], lo["value"]
        net = h - l
        cumulative += net
        is_fractional = h != h.to_integral() or l != l.to_integral()
        fractional += int(h != h.to_integral()) + int(l != l.to_integral())
        normalized.append([key, str(h.normalize()), str(l.normalize())])
        row = {"date": key, "period_start": key, "period_end": key, "period_complete": True,
               "highs": float(h), "lows": float(l), "net": float(net),
               "cumulative": float(cumulative), "source_fractional": is_fractional}
        daily.append(row)
        day = _date(key)
        monday = day - timedelta(days=day.weekday())
        weeks.setdefault(monday, []).append((row, h, l))
    weekly, weekly_ohlc = [], []
    for monday, entries in weeks.items():
        friday = monday + timedelta(days=4)
        closes = [entry[0]["cumulative"] for entry in entries]
        h = sum((entry[1] for entry in entries), Decimal(0))
        l = sum((entry[2] for entry in entries), Decimal(0))
        weekly.append({"date": entries[-1][0]["date"], "period_start": monday.isoformat(),
                       "period_end": friday.isoformat(),
                       "period_complete": now >= datetime.combine(friday, time(18), ET),
                       "highs": float(h), "lows": float(l), "net": float(h - l),
                       "cumulative": closes[-1],
                       "source_fractional": any(entry[0]["source_fractional"] for entry in entries)})
        weekly_ohlc.append({"open": weekly[-2]["cumulative"] if len(weekly) > 1 else closes[0],
                            "high": max(closes), "low": min(closes), "close": closes[-1]})
    _oscillators(daily, [{"high": row["cumulative"], "low": row["cumulative"], "close": row["cumulative"]} for row in daily])
    _oscillators(weekly, weekly_ohlc)
    expected = last_completed_session_date(now - timedelta(hours=2))
    source_hash = hashlib.sha256(json.dumps([normalized, excluded], separators=(",", ":")).encode()).hexdigest()
    result = {"missing": False, "degraded": daily[-1]["date"] < expected,
              "scan_time": now.isoformat(), "source_last_modified": None,
              "data_date": daily[-1]["date"], "expected_session_date": expected,
              "count": len(daily), "weekly_count": len(weekly),
              "current": {**daily[-1], "zone": _zone(daily[-1])},
              "weekly_current": {**weekly[-1], "zone": _zone(weekly[-1])},
              "series": daily, "weekly_series": weekly, "source_hash": source_hash,
              "source": {"provider": "StockCharts", "name": "NYSE Common Stock Only New Highs and New Lows",
                         "url": HISTORY_URL, "symbols": {"highs": SYMBOLS[0], "lows": SYMBOLS[1]},
                         "history_start": HISTORY_START, "history_end": daily[-1]["date"],
                         "fractional_policy": "preserved", "fractional_observations": fractional,
                         "excluded_non_sessions": excluded},
              "baseline": {"date": HISTORY_START, "value": 0, "convention": "before_first_observation"},
              "settings": dict(SETTINGS)}
    json.dumps(result, allow_nan=False)
    return result


def collect(client=None, now=None):
    now = _now(now)
    if client is None:
        with HighLowSourceClient() as source:
            return collect(source, now)
    histories = [parse_stockcharts_history(client.fetch_history(symbol, now=now), symbol, now=now) for symbol in SYMBOLS]
    return build_output(*histories, now=now)


def _validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("missing") is not False:
        raise ValueError("Refusing missing snapshot")
    rows = payload.get("series")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Refusing empty snapshot")
    excluded = payload["source"]["excluded_non_sessions"]
    inputs = [{"rows": [{"date": row["date"], "value": row[field]} for row in rows], "non_observations": excluded}
              for field in ("highs", "lows")]
    expected = build_output(*inputs, now=datetime.fromisoformat(payload["scan_time"]))
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Snapshot disagrees with source levels or settings")
    if len(json.dumps(payload, allow_nan=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot exceeds bounded storage size")


def persist_result(payload, previous=None):
    _validate_payload(payload)
    if previous is not None:
        # Retention is checked before calling any writer, even for damaged prior data.
        lost = {row["date"] for row in previous["series"]} - {row["date"] for row in payload["series"]}
        if lost:
            raise ValueError(f"Source lost prior session {min(lost)}")
        _validate_payload(previous)
    changed = previous is None or any(previous[key] != payload[key] for key in ("series", "weekly_series"))
    prior_replica = os.environ.get("RADON_DB_NO_REPLICA")
    try:
        writer.ensure_no_replica_for_writers()
        if changed:
            for interval, field in (("daily", "series"), ("weekly", "weekly_series")):
                writer.upsert_high_low_line_rows(payload[field], interval=interval, recorded_at=payload["scan_time"])
        writer.upsert_scan_snapshot(SERVICE, payload["scan_time"], payload, bounded=True)
        writer.record_service_health(SERVICE, "ok", finished_at=payload["scan_time"])
        atomic_save(str(CACHE_PATH), payload)
    finally:
        if prior_replica is None:
            os.environ.pop("RADON_DB_NO_REPLICA", None)
        else:
            os.environ["RADON_DB_NO_REPLICA"] = prior_replica


def _read_previous():
    from db.hrana_http import hrana_query
    read_error = None
    try:
        rows = hrana_query("SELECT scan_time, length(payload) FROM scan_snapshots WHERE service = ? ORDER BY scan_time DESC LIMIT 1", (SERVICE,))
        if rows:
            stamp, size = rows[0]
            size = int(size)
            if not 0 < size <= MAX_SNAPSHOT_BYTES:
                raise ValueError("Previous snapshot exceeds bounded read size")
            chunks = []
            for offset in range(0, size, SNAPSHOT_CHUNK_CHARS):
                part = hrana_query(
                    "SELECT substr(payload, ?, ?) FROM scan_snapshots WHERE service = ? AND scan_time = ? LIMIT 1",
                    (offset + 1, SNAPSHOT_CHUNK_CHARS, SERVICE, stamp),
                )
                if not part or not isinstance(part[0][0], str) or len(part[0][0]) != min(SNAPSHOT_CHUNK_CHARS, size - offset):
                    raise ValueError("Previous snapshot changed or was truncated during read")
                chunks.append(part[0][0])
            text = "".join(chunks)
            if len(text.encode()) > MAX_SNAPSHOT_BYTES:
                raise ValueError("Previous snapshot exceeds bounded read size")
            result = json.loads(text)
            _validate_payload(result)
            return result
    except Exception as exc:
        read_error = exc
        print(f"[{SERVICE}] previous DB snapshot unavailable: {exc}", file=sys.stderr)
    try:
        result = verified_load(str(CACHE_PATH))
        _validate_payload(result)
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if read_error is not None:
            raise RuntimeError("Cannot enforce prior-session retention: no readable previous DB or disk snapshot") from read_error
        return None


def _record_failure(exc):
    try:
        writer.record_service_health(SERVICE, "error", finished_at=_now().isoformat(),
                                     error={"class": "high_low_line_failed", "message": str(exc)})
    except Exception as health_exc:
        print(f"[{SERVICE}] error heartbeat failed: {health_exc}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        previous = _read_previous()
        result = collect()
        persist_result(result, previous=previous)
    except Exception as exc:
        print(f"[{SERVICE}] {exc}", file=sys.stderr)
        _record_failure(exc)
        return 1
    print(f"HIGH LOW: {result['data_date']}; Data Freshness: sampled {result['scan_time']}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

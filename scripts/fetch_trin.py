#!/usr/bin/env python3
"""TRIN Indicator — NYSE Arms Index on 60-minute bars with a 10-period MA.

No source serves hourly TRIN history (IB's generated NYSE indices return HMDS
162 for intraday bars; StockCharts quotebrain collapses every period to daily),
so the hourly series is BUILT BY SAMPLING: every five minutes during RTH the
job snapshots TRIN-NYSE (plus AD-NYSE and VOL-NYSE for validation), stores the
sample, and derives hourly bars (last sample per RTH hour bucket, ET) plus
MA(10) across sessions at read time. Daily closes come from StockCharts $TRIN
for long-history context only.

Sources, in order:
  1. Interactive Brokers — live snapshot (RTH only, auth-gated via /health)
  2. Unusual Whales — nothing applicable
  3. StockCharts quotebrain — $TRIN daily closes (reuses breadth's helpers)

Output is dual-written to Turso trin_samples + trin_daily + scan_snapshots and
data/trin.json.

Usage:
    python3 scripts/fetch_trin.py          # human summary (stderr)
    python3 scripts/fetch_trin.py --json   # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from breadth_scan import _fetch_stockcharts_daily
from db import writer
from utils.ib_preflight import IB_REQUEST_TIMEOUT_S, ib_auth_state as _ib_auth_state
from utils.market_calendar import is_market_open_et, last_completed_session_date

# ── constants ─────────────────────────────────────────────────────
SERVICE = "trin"
TRIN_JSON = _PROJECT_DIR / "data" / "trin.json"

ZONE_LOW = 0.60
ZONE_NEAR = 0.65
ZONE_HIGH = 1.50
MA_PERIOD = 10
HOURLY_PAYLOAD_CAP = 400

STOCKCHARTS_SYMBOL = "$TRIN"
SAMPLE_LOOKBACK_DAYS = 200
SAMPLE_READ_PAGE_ROWS = 500
DAILY_READ_PAGE_ROWS = 500  # R-176: trin_daily is append-only and unbounded
SNAPSHOT_SETTLE_S = 2
IB_MARKET_DATA_TYPES = (1, 3, 4)

_ET = ZoneInfo("America/New_York")
_RTH_OPEN_MIN = 9 * 60 + 30
_RTH_CLOSE_MIN = 16 * 60

Sample = dict[str, Any]
DailyRow = tuple[str, float]


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


# ── time helpers ──────────────────────────────────────────────────

def _parse_utc(ts_iso: str) -> datetime:
    parsed = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _et_minutes(moment_et: datetime) -> int:
    return moment_et.hour * 60 + moment_et.minute


def session_date(ts_iso: str) -> str:
    return _parse_utc(ts_iso).astimezone(_ET).strftime("%Y-%m-%d")


def hourly_bucket(ts_iso: str) -> Optional[str]:
    """Bucket START (ET, ``YYYY-MM-DDTHH:MM``) for an RTH sample; None outside RTH."""
    moment_et = _parse_utc(ts_iso).astimezone(_ET)
    minutes = _et_minutes(moment_et)
    if minutes < _RTH_OPEN_MIN or minutes >= _RTH_CLOSE_MIN:
        return None
    return _bucket_label(moment_et, minutes)


def _bucket_label(moment_et: datetime, minutes: int) -> str:
    start_min = _RTH_OPEN_MIN + ((minutes - _RTH_OPEN_MIN) // 60) * 60
    return f"{moment_et:%Y-%m-%d}T{start_min // 60:02d}:{start_min % 60:02d}"


# ── pure computation ──────────────────────────────────────────────

def bucket_hourly(samples: list[Sample]) -> list[dict[str, Any]]:
    """Last sample per RTH hour bucket, sorted by bucket; off-session samples dropped."""
    last_by_bucket: dict[str, Sample] = {}
    for sample in sorted(samples, key=lambda s: s["ts"]):
        bucket = hourly_bucket(sample["ts"])
        if bucket is not None:
            last_by_bucket[bucket] = sample
    return [
        {"bucket": bucket, "ts": sample["ts"], "trin": sample["trin"]}
        for bucket, sample in sorted(last_by_bucket.items())
    ]


def moving_average(values: list[float], n: int = MA_PERIOD) -> Optional[float]:
    if len(values) < n:
        return None
    window = values[-n:]
    return sum(window) / n


def classify_state(ma10: Optional[float], source: Optional[str] = None) -> Optional[str]:
    """R-099: a delayed or frozen print may not promote the badge to
    `in_zone`. The zone is a live-market call; a 15-minute-old TRIN reading
    is not evidence for it.

    R-160: no MA(10) yet is `None`, not `"neutral"`. `moving_average` returns
    None below 10 hourly bars — a first install, a post-retention truncation
    or a cache rebuild from a short data/trin.json all land there — and
    `current.state` is the field the tab colours on, so "no reading yet" and
    "mid-range, no signal" used to render identically as an all-clear.
    """
    if ma10 is None:
        return None
    if source is not None and source not in LIVE_SOURCES:
        return "neutral" if ma10 <= ZONE_NEAR else _elevated_or_neutral(ma10)
    if ma10 <= ZONE_LOW:
        return "in_zone"
    if ma10 <= ZONE_NEAR:
        return "near_zone"
    if ma10 >= ZONE_HIGH:
        return "elevated"
    return "neutral"


def _elevated_or_neutral(ma10: float) -> str:
    return "elevated" if ma10 >= ZONE_HIGH else "neutral"


def _field(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _positive_finite(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    return numeric


def extract_index_value(ticker: Any) -> Optional[float]:
    """First finite positive of last -> close -> bid when bid == ask.

    R-189: the bid/ask fallback used to average the two fields, but on a NYSE
    generated index they are not a quote around a price — `_bid_ask` below
    encodes the actual convention (`adv, dec = bid, ask` for AD-NYSE,
    `up_vol, down_vol = bid, ask` for VOL-NYSE): two unrelated quantities.
    Their midpoint is a value of nothing. The AD-NYSE precedent the old
    docstring cited is the DEGENERATE case — one value published in both
    fields — so that, and only that, is what the fallback accepts now.
    """
    for name in ("last", "close"):
        value = _positive_finite(_field(ticker, name))
        if value is not None:
            return value
    bid = _positive_finite(_field(ticker, "bid"))
    ask = _positive_finite(_field(ticker, "ask"))
    if bid is not None and ask is not None and bid == ask:
        return bid
    return None


def index_has_data(ticker: Any) -> bool:
    """Readiness for the snapshot ladder, which is NOT the same question.

    AD-NYSE and VOL-NYSE legitimately answer with two DIFFERENT counts, and
    `_snapshot_ticker` used `extract_index_value(...) is not None` as its
    readiness test — which only worked because the midpoint of two counts was
    finite. Tightening the value fallback would otherwise have made every
    counts-only ticker look unpriceable.
    """
    if ticker is None:
        return False
    if extract_index_value(ticker) is not None:
        return True
    bid, ask = _bid_ask(ticker)
    return bid is not None or ask is not None


def _hourly_with_ma(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values: list[float] = []
    out: list[dict[str, Any]] = []
    for bar in bars:
        values.append(bar["trin"])
        out.append({"ts": bar["ts"], "bucket": bar["bucket"], "trin": bar["trin"], "ma10": moving_average(values)})
    return out[-HOURLY_PAYLOAD_CAP:]


def _latest_sample(samples: list[Sample]) -> Optional[Sample]:
    if not samples:
        return None
    return max(samples, key=lambda s: s["ts"])


def _current(samples: list[Sample], hourly: list[dict[str, Any]], daily: list[DailyRow]) -> dict[str, Any]:
    latest = _latest_sample(samples) or {}
    ma10 = hourly[-1]["ma10"] if hourly else None
    last_daily = daily[-1] if daily else (None, None)
    return {
        "ts": latest.get("ts"),
        "session_date": latest.get("session_date"),
        "trin": latest.get("trin"),
        "ma10": ma10,
        "state": classify_state(ma10),
        "adv": latest.get("adv"),
        "dec": latest.get("dec"),
        "up_vol": latest.get("up_vol"),
        "down_vol": latest.get("down_vol"),
        "daily_close": last_daily[1],
        "daily_date": last_daily[0],
        "zone_low": ZONE_LOW,
        "zone_near": ZONE_NEAR,
        "zone_high": ZONE_HIGH,
    }


def build_output(
    samples: list[Sample],
    daily_rows: list[DailyRow],
    scan_time: Optional[str] = None,
    source: str = "ib+stockcharts",
) -> dict[str, Any]:
    stamp = scan_time or _iso_utc(datetime.now(timezone.utc))
    daily = sorted(daily_rows)
    hourly = _hourly_with_ma(bucket_hourly(samples))
    return {
        "scan_time": stamp,
        "source": source,
        "current": _current(samples, hourly, daily),
        "hourly": hourly,
        "daily": [{"date": date, "close": close} for date, close in daily],
    }


# ── live sampling (IB) ────────────────────────────────────────────

def _ib_gateway_unavailable() -> bool:
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        _log(f"IB skipped: gateway auth_state={auth_state}")
        return True
    return False


# R-099: the ladder happily settles on type 3 (delayed) or 4
# (delayed-frozen). A 15-minute-old print stamped with `datetime.now()` lands
# in the CURRENT hour bucket, feeds MA(10) and reaches the UI as a live
# IN ZONE badge. The `NOT NULL source` column exists to carry exactly this.
IB_DATA_TYPE_SOURCE = {1: "ib", 2: "ib-frozen", 3: "ib-delayed", 4: "ib-delayed-frozen"}
LIVE_SOURCES = frozenset({"ib"})


def _snapshot_ticker(ib: Any, contract: Any) -> tuple[Optional[Any], Optional[int]]:
    """Snapshot an IB index, trying live -> delayed -> delayed-frozen feeds.

    Returns ``(ticker, market_data_type)`` so the caller can record WHICH
    feed answered. snapshot=True MUST pass "" genericTickList
    (feedback_ib_snapshot_no_generic_ticks).
    """
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        return None, None
    contract = qualified[0]
    for data_type in IB_MARKET_DATA_TYPES:
        try:
            ib.reqMarketDataType(data_type)
            ticker = ib.reqMktData(contract, "", True, False)
            ib.sleep(SNAPSHOT_SETTLE_S)
            ib.cancelMktData(contract)
        except Exception as exc:
            _log(f"IB: {contract.symbol} snapshot (type {data_type}) failed: {exc}")
            continue
        if index_has_data(ticker):
            return ticker, data_type
    return None, None


def _bid_ask(ticker: Any) -> tuple[Optional[float], Optional[float]]:
    if ticker is None:
        return None, None
    return _positive_finite(_field(ticker, "bid")), _positive_finite(_field(ticker, "ask"))


def _build_sample(
    trin: float,
    ad_ticker: Any,
    vol_ticker: Any,
    now: datetime,
    market_data_type: Optional[int] = None,
) -> Sample:
    adv, dec = _bid_ask(ad_ticker)
    up_vol, down_vol = _bid_ask(vol_ticker)
    stamp = _iso_utc(now)
    return {
        "ts": stamp,
        "session_date": session_date(stamp),
        "trin": trin,
        "adv": None if adv is None else int(adv),
        "dec": None if dec is None else int(dec),
        "up_vol": up_vol,
        "down_vol": down_vol,
        "source": IB_DATA_TYPE_SOURCE.get(market_data_type or 1, "ib"),
    }


def _snapshot_indices(ib: Any) -> Optional[Sample]:
    from ib_insync import Index

    trin_ticker, data_type = _snapshot_ticker(ib, Index("TRIN-NYSE", "NYSE"))
    trin = extract_index_value(trin_ticker) if trin_ticker is not None else None
    if trin is None:
        _log("IB: TRIN-NYSE unpriceable")
        return None
    ad_ticker, _ad_type = _snapshot_ticker(ib, Index("AD-NYSE", "NYSE"))
    vol_ticker, _vol_type = _snapshot_ticker(ib, Index("VOL-NYSE", "NYSE"))
    return _build_sample(
        trin, ad_ticker, vol_ticker, datetime.now(timezone.utc),
        market_data_type=data_type,
    )


def sample_live() -> tuple[Optional[Sample], str]:
    """One IB sample during RTH; (None, reason) when skipped or unavailable."""
    if not is_market_open_et():
        return None, "ib-skipped"
    if _ib_gateway_unavailable():
        return None, "ib-skipped"
    from clients.ib_client import IBClient

    client = IBClient()
    try:
        client.connect(client_id="auto", timeout=IB_REQUEST_TIMEOUT_S)
        sample = _snapshot_indices(client.ib)
    except Exception as exc:
        _log(f"IB: sample failed: {exc}")
        return None, "ib-failed"
    finally:
        client.disconnect()
    if sample is None:
        return None, "ib-failed"
    _log(f"IB: TRIN {sample['trin']} adv {sample['adv']} dec {sample['dec']}")
    return sample, "ib"


# ── daily history (StockCharts) ───────────────────────────────────

def fetch_daily() -> list[DailyRow]:
    return sorted(_fetch_stockcharts_daily(STOCKCHARTS_SYMBOL))


def daily_needs_refresh(cached: list[DailyRow]) -> bool:
    """True when the cached daily series is missing the last closed session.

    The sampler runs every five minutes, but StockCharts publishes ONE new
    $TRIN close per session — scraping on every cycle was 108 hits/day for
    at most one new row, against a third-party endpoint the breadth scan
    shares.
    """
    if not cached:
        return True
    return max(date for date, _close in cached) < last_completed_session_date()


def fetch_daily_if_stale(cached: list[DailyRow]) -> list[DailyRow]:
    """`fetch_daily()` only when a session is actually missing."""
    return fetch_daily() if daily_needs_refresh(cached) else []


# ── cache (Turso first, JSON fallback) ────────────────────────────

def _json_cache() -> dict[str, Any]:
    try:
        cached = json.loads(TRIN_JSON.read_text())
    except (OSError, ValueError):
        return {}
    return cached if isinstance(cached, dict) else {}


def _sample_row(row: Any) -> Sample:
    return {
        "ts": row[0],
        "session_date": row[1],
        "trin": float(row[2]),
        "adv": row[3],
        "dec": row[4],
        "up_vol": row[5],
        "down_vol": row[6],
        "source": row[7],
    }


def _turso_samples() -> list[Sample]:
    """Keyset-paginated on ts (Hrana I/O bounding), bounded to the lookback window."""
    from db.client import get_db

    db = get_db()
    cursor = _iso_utc(datetime.now(timezone.utc) - timedelta(days=SAMPLE_LOOKBACK_DAYS))
    rows: list[Sample] = []
    while True:
        page = db.execute(
            "SELECT ts, session_date, trin, adv, dec, up_vol, down_vol, source "
            "FROM trin_samples WHERE ts > ? ORDER BY ts LIMIT ?",
            (cursor, SAMPLE_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend(_sample_row(row) for row in page)
        cursor = page[-1][0]
        if len(page) < SAMPLE_READ_PAGE_ROWS:
            break
    return rows


def _json_samples() -> list[Sample]:
    """Hourly bars are the last sample per bucket, so they rebuild the bars losslessly."""
    return [
        {"ts": bar["ts"], "session_date": session_date(bar["ts"]), "trin": bar["trin"], "source": "cache"}
        for bar in _json_cache().get("hourly") or []
        if bar.get("ts") and bar.get("trin") is not None
    ]


def load_cached_samples() -> list[Sample]:
    try:
        samples = _turso_samples()
        if samples:
            return samples
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        _log(f"turso samples rehydrate non-fatal: {exc}")
    return _json_samples()


def _turso_daily(db: Any = None) -> list[DailyRow]:
    """Keyset-paginated on date (Hrana I/O bounding), like `_turso_samples`.

    R-176: this was a full-table SELECT on an append-only table that grows one
    row per session forever, on the direct-to-cloud HTTP pipeline the sibling
    read is already paginated against.
    """
    if db is None:
        from db.client import get_db

        db = get_db()
    cursor = ""
    rows: list[DailyRow] = []
    while True:
        page = db.execute(
            "SELECT date, close FROM trin_daily WHERE date > ? ORDER BY date LIMIT ?",
            (cursor, DAILY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend((row[0], float(row[1])) for row in page)
        cursor = page[-1][0]
        if len(page) < DAILY_READ_PAGE_ROWS:
            break
    return rows


def _json_daily() -> list[DailyRow]:
    return [
        (row["date"], float(row["close"]))
        for row in _json_cache().get("daily") or []
        if row.get("date") and row.get("close") is not None
    ]


def load_cached_daily() -> list[DailyRow]:
    try:
        daily = _turso_daily()
        if daily:
            return daily
    except Exception as exc:  # noqa: BLE001 — JSON fallback still works
        _log(f"turso daily rehydrate non-fatal: {exc}")
    return _json_daily()


# ── merge ─────────────────────────────────────────────────────────

def merge_samples(cached: list[Sample], fresh: Optional[Sample]) -> list[Sample]:
    by_ts = {s["ts"]: s for s in cached}
    if fresh is not None:
        by_ts[fresh["ts"]] = fresh
    return [by_ts[ts] for ts in sorted(by_ts)]


def merge_daily(cached: list[DailyRow], fresh: list[DailyRow]) -> list[DailyRow]:
    by_date = dict(cached)
    by_date.update(fresh)
    return sorted(by_date.items())


def diff_new_daily(cached: list[DailyRow], merged: list[DailyRow]) -> list[DailyRow]:
    cached_by_date = dict(cached)
    return [row for row in merged if cached_by_date.get(row[0]) != row[1]]


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    TRIN_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRIN_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, TRIN_JSON)


def _nothing_to_persist(payload: dict[str, Any], new_samples: list[Sample], new_daily: list[DailyRow]) -> bool:
    return not new_samples and not new_daily and not payload["hourly"] and not payload["daily"]


def persist_result(
    payload: dict[str, Any],
    new_samples: list[Sample],
    new_daily_rows: list[DailyRow],
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.
    Snapshot + heartbeat run EVERY cycle so the staleness banner notices a
    silent writer; an entirely empty run is refused.

    R-098: the heartbeat used to be an unconditional `ok`, so a total IB
    outage re-served the cached print behind a green banner every 5 minutes
    all day. The degradation lived only in `payload["source"]`, which nothing
    consumes. `fetch_ivrank.py` is the reference shape.
    """
    if _nothing_to_persist(payload, new_samples, new_daily_rows):
        _log("refusing to persist: no samples and no daily rows")
        return
    # R-125 (writer half): a cycle that added NOTHING — IB unreachable,
    # off-hours, the source unchanged — used to upsert a snapshot carrying a
    # bumped `scan_time` and heartbeat `ok`. Every freshness judgement
    # downstream reads that timestamp, so the writer itself is what made a
    # re-serialised cache indistinguishable from a live sample. A degraded
    # run still records its error (REL-049 / R-098); it just does not claim
    # a fresh scan.
    if not new_samples and not new_daily_rows:
        # The heartbeat still fires EVERY cycle — that is the only way the
        # banner notices a silent writer (feedback_service_health_heartbeat)
        # — but the SNAPSHOT is not rewritten, so `scan_time` keeps naming
        # the last cycle that actually produced data.
        writer.ensure_no_replica_for_writers()
        if health_error is None:
            writer.record_service_health(
                SERVICE, "ok", finished_at=payload["scan_time"]
            )
        else:
            writer.record_service_health(
                SERVICE, "error", finished_at=payload["scan_time"], error=health_error,
            )
        _log("no new samples or daily rows; leaving the stored scan_time untouched")
        _write_json_cache(payload)
        return
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if new_samples:
        writer.upsert_trin_samples(new_samples, recorded_at=scan_time)
    if new_daily_rows:
        writer.upsert_trin_daily_rows(new_daily_rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    if health_error is None:
        writer.record_service_health(SERVICE, "ok", finished_at=scan_time)
    else:
        writer.record_service_health(
            SERVICE, "error", finished_at=scan_time, error=health_error,
        )
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────

def run() -> dict[str, Any]:
    cached_samples = load_cached_samples()
    cached_daily = load_cached_daily()
    sample, live_source = sample_live()
    fresh_daily = fetch_daily_if_stale(cached_daily)
    daily = merge_daily(cached_daily, fresh_daily)
    new_daily = diff_new_daily(cached_daily, daily)
    samples = merge_samples(cached_samples, sample)
    daily_source = "stockcharts" if fresh_daily else "cache"
    payload = build_output(samples, daily, source=f"{live_source}+{daily_source}")
    # `ib-skipped` is off-hours and expected; `ib-failed` is the live source
    # being down while the panel keeps rendering a cached print (R-098).
    health_error = (
        {
            "message": (
                f"trin: live IB sample unavailable ({live_source}); serving the "
                f"cached print through {payload.get('as_of') or 'the last sample'}"
            ),
            "class": "source_down",
        }
        if live_source == "ib-failed"
        else None
    )
    persist_result(payload, [sample] if sample else [], new_daily, health_error)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nTRIN: {len(payload['hourly'])} hourly bars, {len(payload['daily'])} daily closes", file=sys.stderr)
    print(f"  latest {current['ts']} trin {current['trin']} ma10 {current['ma10']}", file=sys.stderr)
    print(f"  state {current['state']} daily close {current['daily_close']} ({current['daily_date']})", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TRIN 60-minute Arms Index sampler")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args(argv)

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

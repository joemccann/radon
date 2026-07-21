#!/usr/bin/env python3
"""RV Ratio Scanner — per-asset 252-session realized vol relative to SPY.

Computes the asset/SPY realized-vol ratio over up to ~10 years of aligned
daily closes, with full-history stats, a +/-1 sigma band, and a tiered
divergence regime. Contract: tasks/rv-ratio-indicator-plan.md (payload is
schema_version 1, section 6).

Scan math (plan step 3): ``align_sessions``, ``compute_ratio``,
``compute_ratio_stats``, ``classify_divergence``, ``build_payload``.
History layer (step 4): ``ensure_history`` — durable Turso close store,
Yahoo cold backfill, IB -> UW -> Yahoo incremental chain, splice
validation. CLI (step 5): ``main`` / ``run_scan`` — exactly one JSON
document on stdout, progress on stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Load env before any db.* import so TURSO_DB_URL resolves.
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

from utils.atomic_io import atomic_save
from utils.ib_preflight import ib_auth_state as _ib_auth_state
from utils.index_symbols import is_index_symbol
from utils.market_calendar import last_completed_session_date
from utils.realized_vol import compute_realized_vol_series

# ── constants (plan section 4) ────────────────────────────────────
RV_WINDOW = 252                                    # returns per RV point; needs 253 closes
RATIO_SESSIONS_TARGET = 2520                       # ~10 years of ratio points
CLOSES_TARGET = RATIO_SESSIONS_TARGET + RV_WINDOW  # 2772 aligned closes
BACKFILL_CALENDAR_YEARS = 12                       # fetch margin over 2772 sessions
MIN_RATIO_SESSIONS = 1                             # below -> missing:true, no writes
BENCHMARK = "SPY"
# Deliberately looser than utils.ib_preflight.IB_REQUEST_TIMEOUT_S (15s, the
# quote-path default): a 1-year daily-bar history pull is plan-sanctioned at
# 45s. Distinct name so the two budgets can never be conflated.
IB_HISTORY_TIMEOUT_S = 45

# Scanner range 50-69, fixed-ID convention (CRI precedent). The plan's 62-63
# collide with breadth_scan (62-66); 67-68 are the free slots.
RV_RATIO_IB_CLIENT_IDS = (67, 68)

INCREMENTAL_YAHOO_YEARS = 2
BACKFILL_SKIP_AGE_YEARS = 11   # earliest row older than this: store is as deep as history gets
DEEP_BACKFILL_RECHECK_S = 86_400  # young-listing re-deepen probe cadence (daily-close data)
SPLICE_REL_TOLERANCE = 0.005   # per-date |new - stored| / stored
SPLICE_MAX_MISMATCHES = 3      # more than this many mismatched overlap dates -> re-backfill
YAHOO_COURTESY_SLEEP_S = 0.5

# IB index contracts (UW cannot serve indices; Yahoo needs its own symbols).
IB_INDEX_CONTRACTS = {"SPX": "CBOE", "NDX": "NASDAQ", "RUT": "RUSSELL"}
YAHOO_SYMBOLS = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT"}

SCHEMA_VERSION = 1
RATIO_DECIMALS = 4
RV_DECIMALS = 2

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


# ── scan math ─────────────────────────────────────────────────────

def align_sessions(
    asset: dict[str, float], bench: dict[str, float]
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Intersect the two date-indexed close series on shared sessions.

    Returns (dates, asset_closes, bench_closes) sorted ascending and
    truncated to the trailing CLOSES_TARGET sessions, so every ratio
    point shares an identical session set (a halt day in one series must
    not desynchronize the return windows).
    """
    shared = sorted(asset.keys() & bench.keys())[-CLOSES_TARGET:]
    asset_closes = np.array([asset[d] for d in shared], dtype=float)
    bench_closes = np.array([bench[d] for d in shared], dtype=float)
    return shared, asset_closes, bench_closes


def compute_ratio(asset_rv: np.ndarray, bench_rv: np.ndarray) -> np.ndarray:
    """Elementwise asset_rv / bench_rv with degenerate points as NaN.

    A point is degenerate when either RV is NaN or the benchmark RV is
    not strictly positive — guard-and-drop, never emit Inf.
    """
    valid = ~np.isnan(asset_rv) & ~np.isnan(bench_rv) & (bench_rv > 0)
    ratio = np.full(len(asset_rv), np.nan)
    ratio[valid] = asset_rv[valid] / bench_rv[valid]
    return ratio


def classify_divergence(last: float, avg: float, stddev: float) -> str:
    """Tiered regime from the last ratio vs the full-history band.

    Boundaries are strict: exactly +/-1 sigma stays in_band, exactly
    +2 sigma stays elevated.
    """
    if stddev <= 0:
        return "in_band"
    if last > avg + 2 * stddev:
        return "decoupled"
    if last > avg + stddev:
        return "elevated"
    if last < avg - stddev:
        return "compressed"
    return "in_band"


def compute_ratio_stats(ratio: np.ndarray) -> dict[str, Any]:
    """Stats over the FULL loaded ratio series (never a visible slice)."""
    avg = float(np.mean(ratio))
    stddev = float(np.std(ratio, ddof=1)) if len(ratio) > 1 else 0.0
    last = float(ratio[-1])
    return {
        "high": round(float(np.max(ratio)), RATIO_DECIMALS),
        "low": round(float(np.min(ratio)), RATIO_DECIMALS),
        "avg": round(avg, RATIO_DECIMALS),
        "last": round(last, RATIO_DECIMALS),
        "stddev": round(stddev, RATIO_DECIMALS),
        "last_percentile": round(float(np.mean(ratio <= last)), RATIO_DECIMALS),
        "band_upper": round(avg + stddev, RATIO_DECIMALS),
        "band_lower": round(avg - stddev, RATIO_DECIMALS),
        "divergence": classify_divergence(last, avg, stddev),
    }


def build_payload(
    *,
    symbol: str,
    dates: list[str],
    asset_closes: np.ndarray,
    bench_closes: np.ndarray,
    scan_time: str,
    sources: dict[str, list[str]],
) -> dict[str, Any]:
    """Assemble the schema_version 1 payload from aligned closes.

    Fewer than MIN_RATIO_SESSIONS valid ratio points yields the missing
    variant (insufficient_history) — the caller must not write a
    snapshot for it (feedback_dont_cache_empty_results).
    """
    asset_rv = compute_realized_vol_series(np.asarray(asset_closes, dtype=float), RV_WINDOW)
    bench_rv = compute_realized_vol_series(np.asarray(bench_closes, dtype=float), RV_WINDOW)
    ratio = compute_ratio(asset_rv, bench_rv)

    valid = ~np.isnan(ratio)
    if int(np.count_nonzero(valid)) < MIN_RATIO_SESSIONS:
        return {
            "schema_version": SCHEMA_VERSION,
            "symbol": symbol,
            "missing": True,
            "reason": "insufficient_history",
            "scan_time": scan_time,
        }

    valid_dates = [d for d, keep in zip(dates, valid) if keep]
    valid_ratio = ratio[valid]
    sessions = len(valid_dates)
    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "benchmark": BENCHMARK,
        "window_sessions": RV_WINDOW,
        "scan_time": scan_time,
        "sources": sources,
        "dates": valid_dates,
        "ratio": [round(float(r), RATIO_DECIMALS) for r in valid_ratio],
        "asset_rv": [round(float(v), RV_DECIMALS) for v in asset_rv[valid]],
        "benchmark_rv": [round(float(v), RV_DECIMALS) for v in bench_rv[valid]],
        "stats": compute_ratio_stats(valid_ratio),
        "coverage": {
            "first_date": valid_dates[0],
            "last_date": valid_dates[-1],
            "sessions": sessions,
            "complete": sessions >= RATIO_SESSIONS_TARGET,
        },
        "units": f"x {BENCHMARK} realized vol",
        "missing": False,
    }


# ── history layer (plan section 5.2) ──────────────────────────────

def ensure_history(symbol: str) -> tuple[dict[str, float], list[str]]:
    """Durable close series for ``symbol``, trailing CLOSES_TARGET sessions.

    Incremental IB -> UW -> Yahoo refresh first (the recent segment always
    goes through the priority chain), then a Yahoo deep backfill when the
    store can plausibly be deepened; both paths are splice-validated with a
    lineage reset on corporate-action mismatch. Only bars up to the last
    COMPLETED ET session are ever stored. All rows persist in Turso
    ``price_history_daily`` so SPY is shared across asset scans. Returns
    (date-indexed closes, source labels used this run).
    """
    stored = _read_stored_history(symbol)
    sources: list[str] = []

    if _needs_incremental_refresh(stored):
        stored = _incremental_refresh(symbol, stored, sources)

    if _needs_deep_backfill(symbol, stored):
        stored = _deep_backfill(symbol, stored, sources)

    trailing = sorted(stored)[-CLOSES_TARGET:]
    return {d: stored[d] for d in trailing}, sources


def _read_stored_history(symbol: str) -> dict[str, float]:
    import db.client as db_client

    cursor = db_client.get_db().execute(
        "SELECT date, close FROM price_history_daily WHERE symbol = ? ORDER BY date",
        (symbol,),
    )
    return {row[0]: float(row[1]) for row in cursor.fetchall()}


def _needs_deep_backfill(symbol: str, stored: dict[str, float]) -> bool:
    if len(stored) >= CLOSES_TARGET:
        return False
    if not stored:
        return True
    cutoff = (date.today() - timedelta(days=365 * BACKFILL_SKIP_AGE_YEARS)).isoformat()
    if min(stored) <= cutoff:
        return False  # as deep as public history gets
    return _deep_backfill_recheck_due(symbol)


def _deep_backfill_recheck_due(symbol: str) -> bool:
    """Throttle young-listing deepen probes to once per DEEP_BACKFILL_RECHECK_S.

    A listing younger than CLOSES_TARGET sessions can never satisfy the depth
    check, so depth alone would re-fetch 12 calendar years from Yahoo on every
    scan. The earliest stored row's ``fetched_at`` records the last deepen
    attempt (deep backfill is the only writer that touches it)."""
    import db.client as db_client

    row = db_client.get_db().execute(
        "SELECT fetched_at FROM price_history_daily WHERE symbol = ? ORDER BY date LIMIT 1",
        (symbol,),
    ).fetchone()
    last_attempt = _parse_iso_utc(row[0]) if row else None
    if last_attempt is None:
        return True
    return (datetime.now(timezone.utc) - last_attempt).total_seconds() >= DEEP_BACKFILL_RECHECK_S


def _parse_iso_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _deep_backfill(symbol: str, stored: dict[str, float], sources: list[str]) -> dict[str, float]:
    fetched = _fetch_yahoo_daily(symbol, BACKFILL_CALENDAR_YEARS)
    if not fetched:
        print(f"  Yahoo deep backfill returned nothing for {symbol}", file=sys.stderr)
        return stored
    if _has_splice_mismatch(stored, fetched):
        return _reset_lineage(symbol, fetched, sources)
    _record_source(sources, "yahoo")
    merged = _store_new_dates(symbol, fetched, "yahoo", stored)
    _touch_earliest_row(symbol, merged)
    return merged


def _touch_earliest_row(symbol: str, merged: dict[str, float]) -> None:
    """Stamp the deepen attempt on the earliest row: stored rows win in
    _store_new_dates, so a no-op backfill would otherwise never refresh the
    re-check marker and every subsequent scan would refetch 12 years."""
    if not merged:
        return
    from db.writer import touch_price_history_row

    touch_price_history_row(symbol, min(merged))


def _needs_incremental_refresh(stored: dict[str, float]) -> bool:
    return bool(stored) and max(stored) < last_completed_session_date()


def _incremental_refresh(symbol: str, stored: dict[str, float], sources: list[str]) -> dict[str, float]:
    fetched, source = _fetch_incremental(symbol)
    if not fetched:
        print(f"  Incremental sources empty for {symbol}; serving stored history", file=sys.stderr)
        return stored
    if _has_splice_mismatch(stored, fetched):
        return _rebackfill_after_splice(symbol, stored, sources)
    _record_source(sources, source)
    return _store_new_dates(symbol, fetched, source, stored)


def _fetch_incremental(symbol: str) -> tuple[dict[str, float], str]:
    """Priority chain for the trailing segment: IB, then UW, then Yahoo."""
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        print(f"  IB skipped: gateway auth_state={auth_state}", file=sys.stderr)
    else:
        bars = _fetch_ib_daily(symbol)
        if bars:
            return bars, "ib"
    if is_index_symbol(symbol):
        print(f"  UW skipped: {symbol} is an index symbol", file=sys.stderr)
    else:
        bars = _fetch_uw_daily(symbol)
        if bars:
            return bars, "uw"
    return _fetch_yahoo_daily(symbol, INCREMENTAL_YAHOO_YEARS), "yahoo"


def _has_splice_mismatch(stored: dict[str, float], fetched: dict[str, float]) -> bool:
    """Corporate-action detector on the deliberately overlapping window."""
    overlap = stored.keys() & fetched.keys()
    mismatches = sum(
        1
        for d in overlap
        if stored[d] and abs(fetched[d] - stored[d]) / abs(stored[d]) > SPLICE_REL_TOLERANCE
    )
    if mismatches > SPLICE_MAX_MISMATCHES:
        print(
            f"  Splice mismatch on {mismatches} overlapping dates; corporate action suspected",
            file=sys.stderr,
        )
        return True
    return False


def _rebackfill_after_splice(symbol: str, stored: dict[str, float], sources: list[str]) -> dict[str, float]:
    """Corporate action against the incremental window: re-fetch the full
    series and reset the lineage."""
    fresh = _fetch_yahoo_daily(symbol, BACKFILL_CALENDAR_YEARS)
    if not fresh:
        print(f"  Re-backfill fetch failed; keeping stored lineage for {symbol}", file=sys.stderr)
        return stored
    return _reset_lineage(symbol, fresh, sources)


def _reset_lineage(symbol: str, fresh: dict[str, float], sources: list[str]) -> dict[str, float]:
    """Delete the symbol's rows and store ``fresh`` as the new lineage."""
    from db.writer import delete_price_history

    delete_price_history(symbol)
    sources.clear()
    _record_source(sources, "yahoo")
    return _store_new_dates(symbol, fresh, "yahoo", {})


def _store_new_dates(
    symbol: str, fetched: dict[str, float], source: str, stored: dict[str, float]
) -> dict[str, float]:
    """Upsert only genuinely-new COMPLETED-session dates; splice-validated
    stored rows win. Bars dated after the last completed ET session are
    in-progress prices, never daily closes — storing one would poison the
    durable store for 252 sessions because stored rows win on later runs."""
    from db.writer import upsert_price_history_rows

    latest_complete = last_completed_session_date()
    new_rows = [
        {"date": d, "close": close, "source": source}
        for d, close in sorted(fetched.items())
        if d not in stored and d <= latest_complete
    ]
    upsert_price_history_rows(symbol, new_rows)
    merged = dict(stored)
    merged.update({row["date"]: float(row["close"]) for row in new_rows})
    return merged


def _record_source(sources: list[str], source: str) -> None:
    if source not in sources:
        sources.append(source)


# ── fetchers (network; monkeypatched in tests) ────────────────────

def _fetch_yahoo_daily(symbol: str, years: int) -> dict[str, float]:
    """Daily closes from the Yahoo v8 chart API.

    Reads indicators.quote[0].close, which is split-adjusted and
    dividend-UNadjusted; NOT adjclose, which is also dividend-adjusted
    and would mismatch IB at the splice (plan section 4).
    """
    from urllib.request import Request, urlopen

    time.sleep(YAHOO_COURTESY_SLEEP_S)
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=int(years * 365.25))).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO_SYMBOLS.get(symbol, symbol)}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            result = json.load(resp)["chart"]["result"][0]
    except Exception as exc:
        print(f"  Yahoo: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    bars = {
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): float(c)
        for ts, c in zip(timestamps, closes)
        if c is not None
    }
    print(f"  Yahoo: {symbol} returned {len(bars)} bars", file=sys.stderr)
    return bars


def _fetch_ib_daily(symbol: str) -> dict[str, float]:
    """Trailing-year daily TRADES closes from IB; every await bounded
    (feedback_ib_insync_no_request_timeouts)."""
    try:
        from ib_insync import IB, Index, Stock
    except ImportError:
        return {}
    import asyncio

    if symbol in IB_INDEX_CONTRACTS:
        contract = Index(symbol, IB_INDEX_CONTRACTS[symbol])
    else:
        contract = Stock(symbol, "SMART", "USD")

    ib = IB()
    if not _connect_ib(ib):
        return {}

    async def _qualify_and_fetch():
        await asyncio.wait_for(ib.qualifyContractsAsync(contract), timeout=IB_HISTORY_TIMEOUT_S)
        return await asyncio.wait_for(
            ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr="1 Y",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            ),
            timeout=IB_HISTORY_TIMEOUT_S,
        )

    try:
        bars = ib.run(_qualify_and_fetch())
        result = {str(b.date): float(b.close) for b in bars or []}
        print(f"  IB: {symbol} returned {len(result)} bars", file=sys.stderr)
        return result
    except Exception as exc:
        print(f"  IB: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    finally:
        ib.disconnect()


def _connect_ib(ib: Any) -> bool:
    from clients.ib_client import DEFAULT_HOST

    for client_id in RV_RATIO_IB_CLIENT_IDS:
        for port in (4001, 7497):
            try:
                ib.connect(DEFAULT_HOST, port, clientId=client_id, timeout=8)
                return True
            except Exception as exc:
                print(
                    f"  IB connect failed on port {port} clientId {client_id}: {exc}",
                    file=sys.stderr,
                )
    return False


def _fetch_uw_daily(symbol: str) -> dict[str, float]:
    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}
    try:
        with UWClient() as uw:
            data = uw.get_stock_ohlc(symbol, candle_size="1d")
    except Exception as exc:
        print(f"  UW: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    bars = {
        b["date"]: float(b["close"])
        for b in (data.get("data") or [])
        if b.get("date") and b.get("close") is not None
    }
    print(f"  UW: {symbol} returned {len(bars)} bars", file=sys.stderr)
    return bars


# ── CLI (plan step 5) ─────────────────────────────────────────────

class BenchmarkHistoryError(RuntimeError):
    """Raised when the SPY benchmark has no usable close history."""


def run_scan(symbol: str) -> dict[str, Any]:
    """Full scan pipeline: history, alignment, payload, persistence."""
    print(f"RV RATIO SCAN: {symbol} vs {BENCHMARK}", file=sys.stderr)
    asset_closes, asset_sources = ensure_history(symbol)
    bench_closes, bench_sources = ensure_history(BENCHMARK)
    if not bench_closes:
        raise BenchmarkHistoryError(
            f"benchmark {BENCHMARK} has no close history; "
            "a ratio without a benchmark is meaningless"
        )

    dates, asset_arr, bench_arr = align_sessions(asset_closes, bench_closes)
    print(f"  Aligned sessions: {len(dates)}", file=sys.stderr)

    payload = build_payload(
        symbol=symbol,
        dates=dates,
        asset_closes=asset_arr,
        bench_closes=bench_arr,
        scan_time=_now_iso(),
        sources={"asset": asset_sources, "benchmark": bench_sources},
    )
    if payload.get("missing"):
        print(
            f"  Insufficient history for {symbol}; no snapshot written "
            "(feedback_dont_cache_empty_results)",
            file=sys.stderr,
        )
        return payload

    _persist_snapshot(symbol, payload)
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _persist_snapshot(symbol: str, payload: dict[str, Any]) -> None:
    """Turso snapshot (best-effort, cri precedent) + atomic disk write."""
    try:
        from db.writer import ensure_no_replica_for_writers, upsert_rv_ratio_snapshot

        ensure_no_replica_for_writers()
        upsert_rv_ratio_snapshot(symbol, payload["scan_time"], payload)
        print(f"  Turso snapshot upserted for {symbol}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[rv-ratio] snapshot db write non-fatal: {exc}", file=sys.stderr)

    disk_path = _disk_snapshot_path(symbol)
    atomic_save(str(disk_path), payload)
    print(f"  Snapshot saved: {disk_path}", file=sys.stderr)


def _disk_snapshot_path(symbol: str) -> Path:
    return _PROJECT_DIR / "data" / "rv_ratio" / f"{symbol}.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RV Ratio Scanner: asset 252-session realized vol relative to SPY",
    )
    parser.add_argument("symbol", help="Asset symbol, e.g. SMH")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        print(f"[rv-ratio] invalid symbol: {args.symbol!r}", file=sys.stderr)
        sys.exit(2)

    try:
        payload = run_scan(symbol)
    except BenchmarkHistoryError as exc:
        print(f"[rv-ratio] {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(payload))


if __name__ == "__main__":
    main()

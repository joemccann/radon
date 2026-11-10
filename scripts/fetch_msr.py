#!/usr/bin/env python3
"""MSR Indicator — Move Surprise Ratio for SPX, NDX and RUT.

msr_t = |1-day % move of the index| / prior-close Cboe vol index (VIX for
SPX, VXN for NDX, RVX for RUT). Ranks how badly the vol market underpriced
each session's move since the Goldman chart start (2007-11-08).

Sources: Cboe CDN daily-price CSVs for SPX/VIX/VXN/RVX (conditional GET).
NDX/RUT closes live in Turso price_history_daily, refreshed IB first, then
Cboe (RUT only), then Yahoo as last resort. Spec: docs/indicators/msr.md.

Output is dual-written to Turso msr_history + scan_snapshots('msr') and
data/msr.json (Python readers only).

Usage:
    python3 scripts/fetch_msr.py            # human summary (stderr)
    python3 scripts/fetch_msr.py --json     # JSON to stdout
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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

from utils.atomic_io import atomic_save
from utils.ib_preflight import ib_auth_state as _ib_auth_state
from utils.market_calendar import last_completed_session_date

# ── constants ─────────────────────────────────────────────────────
MSR_JSON = _PROJECT_DIR / "data" / "msr.json"
HEALTH_SERVICE = "msr"

START_DATE = "2007-11-08"           # Goldman MSR chart start
INDEX_DEPTH_TARGET = "2009-09-11"   # covers VXN (2009-09-14) / RVX (2009-09-16) starts
DECIMALS = 6
WEEK_SESSIONS = 5
TOP_N = 10

MSR_INDEXES = ("SPX", "NDX", "RUT")
IV_SYMBOLS = {"SPX": "VIX", "NDX": "VXN", "RUT": "RVX"}
CBOE_FILES = ("SPX", "VIX", "VXN", "RVX")
STORED_INDEXES = ("NDX", "RUT")      # closes kept in price_history_daily
CBOE_INDEX_CLOSES = {"RUT"}          # Cboe CDN serves RUT; NDX is 403

IB_INDEX_EXCHANGES = {"NDX": "NASDAQ", "RUT": "RUSSELL"}
IB_HISTORY_TIMEOUT_S = 90
IB_CONNECT_TIMEOUT_S = 10
IB_DEEP_DURATION = "20 Y"
IB_INCREMENTAL_DURATION = "1 Y"
YAHOO_DEEP_YEARS = 18
YAHOO_INCREMENTAL_YEARS = 2


# ── Cboe CSV parsing ──────────────────────────────────────────────

def parse_close_csv(text: str, column: str) -> dict[str, float]:
    """Cboe daily-prices CSV → {"YYYY-MM-DD": close}.

    ``column`` is "SPX"/"RUT" for index files, "CLOSE" for vol-index OHLC
    files. Header, malformed and non-positive rows are skipped.
    """
    closes: dict[str, float] = {}
    for raw in csv.DictReader(io.StringIO(text or "")):
        parsed = _parse_csv_row(raw, column)
        if parsed is not None:
            closes[parsed[0]] = parsed[1]
    return closes


def _parse_csv_row(raw: dict[str, Any], column: str) -> Optional[tuple[str, float]]:
    try:
        session = datetime.strptime((raw.get("DATE") or "").strip(), "%m/%d/%Y").date()
        value = float(raw[column])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return session.isoformat(), value


# ── MSR math ──────────────────────────────────────────────────────

def compute_msr_rows(
    closes: dict[str, float], vols: dict[str, float], start: str = START_DATE
) -> list[dict[str, Any]]:
    """Ascending [{date, close, iv_prior, move_pct, msr}] over common sessions.

    The prior session is the prior COMMON date of both series and may sit
    before ``start``; only sessions t >= start are emitted.
    """
    common = sorted(closes.keys() & vols.keys())
    return [
        _msr_row(session, closes[session], closes[prior], vols[prior])
        for prior, session in zip(common, common[1:])
        if session >= start
    ]


def _msr_row(session: str, close: float, prior_close: float, iv_prior: float) -> dict[str, Any]:
    move_pct = (close / prior_close - 1.0) * 100.0
    return {
        "date": session,
        "close": close,
        "iv_prior": iv_prior,
        "move_pct": move_pct,
        "msr": abs(move_pct) / iv_prior,
    }


def rank_of(value: float, values: list[float]) -> int:
    """1 = largest; ties share a rank."""
    return 1 + sum(1 for v in values if v > value)


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile at index (n-1)*q (numpy default)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize_index(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """{current, stats, week_max, top} for one index's emitted rows."""
    if not rows:
        return {"current": None, "stats": None, "week_max": None, "top": []}
    values = [row["msr"] for row in rows]
    return {
        "current": _current_card(rows[-1], values),
        "stats": _stats(rows, values),
        "week_max": _week_max(rows, values),
        "top": _top(rows),
    }


def _r(value: float) -> float:
    return round(value, DECIMALS)


def _current_card(row: dict[str, Any], values: list[float]) -> dict[str, Any]:
    return {
        "date": row["date"],
        "msr": _r(row["msr"]),
        "move_pct": _r(row["move_pct"]),
        "close": row["close"],
        "iv_prior": row["iv_prior"],
        "rank": rank_of(row["msr"], values),
    }


def _stats(rows: list[dict[str, Any]], values: list[float]) -> dict[str, Any]:
    peak = max(rows, key=lambda row: row["msr"])
    return {
        "count": len(rows),
        "max": _r(peak["msr"]),
        "max_date": peak["date"],
        "p50": _r(percentile(values, 0.50)),
        "p90": _r(percentile(values, 0.90)),
        "p99": _r(percentile(values, 0.99)),
    }


def _week_max(rows: list[dict[str, Any]], values: list[float]) -> dict[str, Any]:
    peak = max(rows[-WEEK_SESSIONS:], key=lambda row: row["msr"])
    return {
        "date": peak["date"],
        "msr": _r(peak["msr"]),
        "move_pct": _r(peak["move_pct"]),
        "iv_prior": peak["iv_prior"],
        "rank": rank_of(peak["msr"], values),
    }


def _top(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    largest = sorted(rows, key=lambda row: (row["msr"], row["date"]), reverse=True)[:TOP_N]
    return [
        {"date": row["date"], "msr": _r(row["msr"]), "move_pct": _r(row["move_pct"])}
        for row in largest
    ]


def merge_series(by_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """[{date, spx, ndx, rut}] over the union of dates; null where absent."""
    msr_by_index = {
        index: {row["date"]: _r(row["msr"]) for row in by_index.get(index) or []}
        for index in MSR_INDEXES
    }
    dates = sorted(set().union(*msr_by_index.values()))
    return [
        {"date": session, **{index.lower(): msr_by_index[index].get(session) for index in MSR_INDEXES}}
        for session in dates
    ]


def build_payload(
    *,
    scan_time: str,
    rows_by_index: dict[str, list[dict[str, Any]]],
    source_last_modified: dict[str, Optional[str]],
    close_sources: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "scan_time": scan_time,
        "start_date": START_DATE,
        "source_last_modified": source_last_modified,
        "close_sources": close_sources,
        "indexes": {
            index: {"iv_symbol": IV_SYMBOLS[index], **summarize_index(rows_by_index.get(index) or [])}
            for index in MSR_INDEXES
        },
        "series": merge_series(rows_by_index),
    }


# ── NDX / RUT close ladder (price_history_daily) ──────────────────

def ensure_index_closes(symbol: str) -> tuple[dict[str, float], list[str], int]:
    """Durable close history for an index: (closes, source labels, new rows).

    Stored rows win. A missing or shallow store deep-seeds IB 20Y → Cboe
    (RUT) → Yahoo; a current-depth store that lags the last completed
    session refreshes IB 1Y → Cboe (RUT) → Yahoo 2Y, first non-empty wins.
    """
    stored = _read_stored_closes(symbol)
    before = len(stored)
    if _needs_deep_seed(stored):
        stored = _deep_seed(symbol, stored)
    elif _needs_incremental(stored):
        stored = _incremental_refresh(symbol, stored)
    closes = {session: close for session, (close, _source) in stored.items()}
    sources = sorted({source for _close, source in stored.values()})
    return closes, sources, len(stored) - before


def _read_stored_closes(symbol: str) -> dict[str, tuple[float, str]]:
    import db.client as db_client

    cursor = db_client.get_db().execute(
        "SELECT date, close, source FROM price_history_daily WHERE symbol = ? ORDER BY date",
        (symbol,),
    )
    return {row[0]: (float(row[1]), row[2]) for row in cursor.fetchall()}


def _needs_deep_seed(stored: dict[str, Any]) -> bool:
    return not stored or min(stored) > INDEX_DEPTH_TARGET


def _needs_incremental(stored: dict[str, Any]) -> bool:
    return max(stored) < last_completed_session_date()


def _is_deep_and_current(stored: dict[str, Any]) -> bool:
    return not _needs_deep_seed(stored) and not _needs_incremental(stored)


def _deep_seed(symbol: str, stored: dict[str, tuple[float, str]]) -> dict[str, tuple[float, str]]:
    tiers = _close_tiers(symbol, IB_DEEP_DURATION, YAHOO_DEEP_YEARS)
    for label, fetch in tiers:
        if _is_deep_and_current(stored):
            break
        stored = _store_new_dates(symbol, fetch(), label, stored)
    return stored


def _incremental_refresh(symbol: str, stored: dict[str, tuple[float, str]]) -> dict[str, tuple[float, str]]:
    tiers = _close_tiers(symbol, IB_INCREMENTAL_DURATION, YAHOO_INCREMENTAL_YEARS)
    for label, fetch in tiers:
        bars = _completed_sessions_only(fetch())
        if bars:
            return _store_new_dates(symbol, bars, label, stored)
    print(f"  {symbol}: every close source empty; serving stored history", file=sys.stderr)
    return stored


def _close_tiers(symbol: str, ib_duration: str, yahoo_years: int) -> list[tuple[str, Callable[[], dict[str, float]]]]:
    """Priority chain IB → Cboe (RUT only) → Yahoo. Robinhood and UW do not
    serve index symbols. IB is skipped only when /health reports an auth
    state other than authenticated."""
    tiers: list[tuple[str, Callable[[], dict[str, float]]]] = []
    if _ib_reachable():
        tiers.append(("ib", lambda: _fetch_ib_daily(symbol, duration=ib_duration)))
    if symbol in CBOE_INDEX_CLOSES:
        tiers.append(("cboe", lambda: _fetch_cboe_daily(symbol)))
    tiers.append(("yahoo", lambda: _fetch_yahoo_daily(symbol, years=yahoo_years)))
    return tiers


def _ib_reachable() -> bool:
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        print(f"  IB skipped: gateway auth_state={auth_state}", file=sys.stderr)
        return False
    return True


def _completed_sessions_only(bars: dict[str, float]) -> dict[str, float]:
    latest_complete = last_completed_session_date()
    return {session: close for session, close in (bars or {}).items() if session <= latest_complete}


def _store_new_dates(
    symbol: str, fetched: dict[str, float], source: str, stored: dict[str, tuple[float, str]]
) -> dict[str, tuple[float, str]]:
    """Upsert genuinely new COMPLETED-session closes; stored rows win."""
    from db import writer

    new_rows = [
        {"date": session, "close": close, "source": source}
        for session, close in sorted(_completed_sessions_only(fetched).items())
        if session not in stored
    ]
    if new_rows:
        writer.upsert_price_history_rows(symbol, new_rows)
        print(f"  {symbol}: stored {len(new_rows)} new {source} closes", file=sys.stderr)
    merged = dict(stored)
    merged.update({row["date"]: (float(row["close"]), source) for row in new_rows})
    return merged


# ── fetchers (network; monkeypatched in tests) ────────────────────

def _bar_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()[:10]


def _fetch_ib_daily(symbol: str, duration: str) -> dict[str, float]:
    """Daily TRADES closes for an index over one auto-ranged connection;
    the history request is bounded by IBClient's wait_for."""
    try:
        from clients.ib_client import IBClient
        from ib_insync import Index
    except ImportError:
        return {}
    client = None
    try:
        client = IBClient()
        client.connect(client_id="auto", timeout=IB_CONNECT_TIMEOUT_S)
        bars = client.get_historical_data(
            Index(symbol, IB_INDEX_EXCHANGES[symbol], "USD"),
            duration=duration,
            bar_size="1 day",
            what_to_show="TRADES",
            use_rth=True,
            timeout=IB_HISTORY_TIMEOUT_S,
        )
        closes = {_bar_date(bar.date): float(bar.close) for bar in bars or [] if bar.close and bar.close > 0}
        print(f"  IB: {symbol} {duration} returned {len(closes)} bars", file=sys.stderr)
        return closes
    except Exception as exc:  # noqa: BLE001 — fall through to the next tier
        print(f"  IB: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass


def _fetch_cboe_daily(symbol: str) -> dict[str, float]:
    try:
        from clients.cboe_client import CboeClient

        with CboeClient() as client:
            text, _stamp = client.fetch_history(symbol)
    except Exception as exc:  # noqa: BLE001 — fall through to Yahoo
        print(f"  Cboe: {symbol} failed: {exc}", file=sys.stderr)
        return {}
    closes = parse_close_csv(text or "", symbol)
    print(f"  Cboe: {symbol} returned {len(closes)} closes", file=sys.stderr)
    return closes


def _fetch_yahoo_daily(symbol: str, years: int) -> dict[str, float]:
    """Yahoo v8 chart closes (absolute last resort); shares rv-ratio's fetcher."""
    from rv_ratio_scan import _fetch_yahoo_daily as fetch_yahoo_chart

    return fetch_yahoo_chart(symbol, years)


# ── persistence ───────────────────────────────────────────────────

def _write_db_cache(
    payload: dict[str, Any],
    rows_by_index: dict[str, list[dict[str, Any]]],
    scan_time: str,
) -> None:
    """Best-effort Turso mirror (straddle precedent): msr_history rows only
    when rebuilt, snapshot + heartbeat every cycle."""
    try:
        from db import writer
    except ImportError:
        return
    try:
        for index, rows in rows_by_index.items():
            if rows:
                writer.upsert_msr_rows(index, rows, scan_time)
        writer.upsert_scan_snapshot(HEALTH_SERVICE, scan_time, payload)
        writer.record_service_health(HEALTH_SERVICE, "ok", finished_at=scan_time)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        print(f"[msr] db cache non-fatal: {exc}", file=sys.stderr)


def _ensure_writer_guard() -> None:
    try:
        from db import writer
    except ImportError:
        return
    writer.ensure_no_replica_for_writers()


def _record_failure(exc: Exception) -> None:
    """Error heartbeat before the oneshot dies, so the failure is visible
    as an error row rather than a silently aging ok row."""
    try:
        from db import writer

        writer.record_service_health(
            HEALTH_SERVICE, "error", finished_at=_scan_time(datetime.now(timezone.utc)),
            error={"message": str(exc)[:500]},
        )
    except Exception as health_exc:  # noqa: BLE001 — the original error wins
        print(f"[msr] error heartbeat failed: {health_exc}", file=sys.stderr)


def _write_json_cache(payload: dict[str, Any]) -> None:
    atomic_save(str(MSR_JSON), payload)


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        cached = json.loads(MSR_JSON.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict):
        return None
    cached.pop("_checksum", None)
    return cached


# ── orchestration ─────────────────────────────────────────────────

def _scan_time(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _fetch_cboe_files(
    client: Any, cached_stamps: dict[str, Optional[str]]
) -> tuple[dict[str, Optional[str]], dict[str, Optional[str]]]:
    """Conditional GET on every Cboe file: (texts, stamps); 304 → text None."""
    texts: dict[str, Optional[str]] = {}
    stamps: dict[str, Optional[str]] = {}
    for symbol in CBOE_FILES:
        texts[symbol], stamps[symbol.lower()] = client.fetch_history(
            symbol, if_modified_since=cached_stamps.get(symbol.lower())
        )
    return texts, stamps


def _refetch_unchanged(client: Any, texts: dict[str, Optional[str]], stamps: dict[str, Optional[str]]) -> None:
    for symbol in CBOE_FILES:
        if texts[symbol] is None:
            texts[symbol], stamps[symbol.lower()] = client.fetch_history(symbol)


def _load_index_closes() -> dict[str, tuple[dict[str, float], list[str], int]]:
    """NDX/RUT ladders; an unavailable index degrades to empty, never fails the run."""
    loaded = {}
    for symbol in STORED_INDEXES:
        try:
            loaded[symbol] = ensure_index_closes(symbol)
        except Exception as exc:  # noqa: BLE001 — index emitted with current: null
            print(f"[msr] {symbol} closes unavailable: {exc}", file=sys.stderr)
            loaded[symbol] = ({}, [], 0)
    return loaded


def _compute_rows_by_index(
    texts: dict[str, Optional[str]], index_closes: dict[str, tuple[dict[str, float], list[str], int]]
) -> dict[str, list[dict[str, Any]]]:
    closes = {
        "SPX": parse_close_csv(texts["SPX"] or "", "SPX"),
        **{symbol: index_closes[symbol][0] for symbol in STORED_INDEXES},
    }
    return {
        index: compute_msr_rows(closes[index], parse_close_csv(texts[IV_SYMBOLS[index]] or "", "CLOSE"))
        for index in MSR_INDEXES
    }


def _persist(payload: dict[str, Any], rows_by_index: dict[str, list[dict[str, Any]]], scan_time: str) -> None:
    _write_db_cache(payload, rows_by_index, scan_time)
    _write_json_cache(payload)


def run(client: Optional[Any] = None, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Fetch, compute, persist and return the MSR payload.

    When all four Cboe files return 304 and the NDX/RUT stores gained
    nothing, the cached payload is reused with a fresh scan_time (snapshot +
    heartbeat only). Otherwise the full series is rebuilt.
    """
    scan_time = _scan_time(now or datetime.now(timezone.utc))
    if client is None:
        from clients.cboe_client import CboeClient
        client = CboeClient()

    _ensure_writer_guard()
    cached = _read_json_cache()
    texts, stamps = _fetch_cboe_files(client, (cached or {}).get("source_last_modified") or {})
    index_closes = _load_index_closes()

    no_new_closes = all(new_count == 0 for _c, _s, new_count in index_closes.values())
    if cached and no_new_closes and all(text is None for text in texts.values()):
        print("[msr] sources unchanged (304, no new closes); refreshing snapshot only", file=sys.stderr)
        payload = {**cached, "scan_time": scan_time}
        _persist(payload, {}, scan_time)
        return payload

    _refetch_unchanged(client, texts, stamps)
    rows_by_index = _compute_rows_by_index(texts, index_closes)
    if not rows_by_index["SPX"]:
        raise ValueError("SPX msr series computed to zero rows")

    payload = build_payload(
        scan_time=scan_time,
        rows_by_index=rows_by_index,
        source_last_modified=stamps,
        close_sources={"SPX": ["cboe"], **{s: index_closes[s][1] for s in STORED_INDEXES}},
    )
    _persist(payload, rows_by_index, scan_time)
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    print(f"\nMSR — scan {payload['scan_time']}", file=sys.stderr)
    for index, summary in payload["indexes"].items():
        current = summary.get("current")
        if not current:
            print(f"  {index}  n/a", file=sys.stderr)
            continue
        count = (summary.get("stats") or {}).get("count")
        week = summary.get("week_max") or {}
        print(
            f"  {index}  {current['date']}  msr {current['msr']:.3f}  rank #{current['rank']} of {count}"
            f"  5d max {week.get('msr')} ({week.get('date')}, #{week.get('rank')})",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move surprise ratio (SPX/NDX/RUT vs prior-close VIX/VXN/RVX)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    try:
        payload = run()
    except Exception as exc:
        _record_failure(exc)
        raise
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""DISPERSION Indicator — VIX vs single-stock vs cross-sector dispersion.

Descriptive regime read, not a forecast: when the 95-5 spread of daily
single-stock (and sector-ETF) returns runs above +1 sigma while the VIX sits
below its mean, volatility is rising below the surface. Spec:
docs/indicators/dispersion.md.

Source ladder: IB daily TRADES bars for the S&P 500 seed + sector SPDRs +
VIX (asyncio sweep, IB_CONCURRENCY slots), then Yahoo for whatever IB left
empty. UW is skipped on purpose (515 per-symbol calls would spend the shared
daily cap). Only raw per-session rows are stored; the 60-session means and
since-2017 z-scores are rebuilt from every stored row each run.

Usage:
    python3 scripts/fetch_dispersion.py              # human summary (stderr)
    python3 scripts/fetch_dispersion.py --json       # payload to stdout
    python3 scripts/fetch_dispersion.py --backfill   # 10 Y IB seed run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
for _path in (_PROJECT_DIR, _SCRIPT_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

try:
    from db import writer  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — tests inject a FakeWriter
    writer = None  # type: ignore[assignment]

from lib import dispersion_math as dm  # noqa: E402
from lib.dispersion_math import (  # noqa: E402
    SECTOR_ETFS,
    VIX_SYMBOL,
    Closes,
    build_payload,
    build_raw_rows,
    ensure_plausible_rows,
    master_sessions,
)
from utils.market_calendar import last_completed_session_date  # noqa: E402

# ── constants (docs/indicators/dispersion.md section E.1) ─────────
SERVICE = "dispersion"
DISPERSION_JSON = _PROJECT_DIR / "data" / "dispersion.json"
IB_CONCURRENCY = 8
IB_HISTORY_TIMEOUT_S = 45          # ib_insync's own timeout= (the cancel path)
IB_CANCEL_GRACE_S = 5              # outer asyncio bound fires only after that cancel
SWEEP_BUDGET_S = 600               # wall clock for the whole sweep, both rungs
IB_SWEEP_BUDGET_S = 420            # the IB rung's share; it never eats the fallback's
YAHOO_SWEEP_BUDGET_S = 180         # reserved for the Yahoo rung even when IB overran (R-446)
INCREMENTAL_DURATION = "1 M"
BACKFILL_DURATION = "10 Y"         # IB's daily window floor is 2016-08-31
YAHOO_INCREMENTAL_RANGE = "1mo"
YAHOO_BACKFILL_PERIOD1 = "2016-08-01"
YAHOO_SPARK_BATCH_SIZE = 20        # >20 symbols per spark request gets HTTP 400
YAHOO_COURTESY_SLEEP_S = 0.25
YAHOO_TIMEOUT_S = 30
YAHOO_USER_AGENT = "Mozilla/5.0"   # an honest UA gets 429 from Yahoo
HISTORY_READ_PAGE_ROWS = 500       # Hrana bounding on the dispersion_history read
# Mirrors radon-dispersion.timer (OnCalendar=*-*-* 22:20:00 UTC)
TIMER_HOUR_UTC, TIMER_MINUTE_UTC = 22, 20

STATUS_OK = "ok"
STATUS_STALE_SOURCE = "stale_source"
SOURCE_IB = "ib"
SOURCE_YAHOO = "yahoo"
SOURCE_MIXED = "mixed"
SOURCE_NONE = "none"
SOURCE_STORED = "stored"

FetchCloses = Callable[[list[str], bool], dict[str, Closes]]


def _log(message: str) -> None:
    print(f"[dispersion] {message}", file=sys.stderr, flush=True)


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ── sweep result ──────────────────────────────────────────────────

class SweepCloses(dict):
    """{symbol: {date: close}} plus the rung that served each symbol."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: dict[str, str] = {}

    def take(self, fetched: dict[str, Closes], source: str) -> None:
        for symbol, series in fetched.items():
            if series:
                self[symbol] = series
                self.sources[symbol] = source

    def missing(self, symbols: list[str]) -> list[str]:
        return [s for s in symbols if s not in self]


# ── IB rung ───────────────────────────────────────────────────────

def _ib_gateway_unavailable() -> bool:
    from utils.ib_preflight import ib_auth_state

    state = ib_auth_state()
    if state is not None and state != "authenticated":
        _log(f"IB skipped: gateway auth_state={state}")
        return True
    return False


def _bar_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()[:10]


def _closes_from_bars(bars: Any) -> Closes:
    return {_bar_date(b.date): float(b.close) for b in bars or [] if b.close is not None}


def _ib_contract(symbol: str) -> Any:
    from ib_insync import Index, Stock

    if symbol == VIX_SYMBOL:
        return Index("VIX", "CBOE", "USD")
    return Stock(symbol.replace("-", " "), "SMART", "USD")


async def _ib_sweep(ib: Any, symbols: list[str], duration: str, deadline: float) -> dict[str, Closes]:
    slots = asyncio.Semaphore(IB_CONCURRENCY)
    results: dict[str, Closes] = {}

    async def fetch_one(symbol: str) -> None:
        async with slots:
            if time.monotonic() >= deadline:
                return
            try:
                bars = await asyncio.wait_for(
                    ib.reqHistoricalDataAsync(
                        _ib_contract(symbol),
                        endDateTime="",
                        durationStr=duration,
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=True,
                        formatDate=1,
                        timeout=IB_HISTORY_TIMEOUT_S,
                    ),
                    timeout=IB_HISTORY_TIMEOUT_S + IB_CANCEL_GRACE_S,
                )
            except Exception as exc:  # noqa: BLE001 — Yahoo rung picks it up
                _log(f"IB: {symbol} failed: {exc}")
                return
            closes = _closes_from_bars(bars)
            if closes:
                results[symbol] = closes
            else:
                _log(f"IB: {symbol} no bars returned")

    await asyncio.gather(*(fetch_one(s) for s in symbols))
    return results


def _construct_ib_client() -> Any:
    """A client that cannot even be BUILT is misconfiguration, not a gateway
    outage: record it and die loudly rather than silently falling to Yahoo."""
    from clients.ib_client import IBClient

    try:
        return IBClient()
    except Exception as exc:
        _heartbeat(
            "error",
            _iso(datetime.now(timezone.utc)),
            {"message": f"IB client construction failed: {exc}", "class": "client_construction"},
        )
        raise


def _fetch_ib_closes(symbols: list[str], duration: str, deadline: float) -> dict[str, Closes]:
    if _ib_gateway_unavailable():
        return {}
    client = _construct_ib_client()
    try:
        client.connect(client_id="auto", timeout=10)
    except Exception as exc:  # noqa: BLE001 — Yahoo rung covers the whole universe
        _log(f"IB connect failed: {exc}")
        return {}
    try:
        return client.ib.run(_ib_sweep(client.ib, symbols, duration, deadline))
    finally:
        client.disconnect()


# ── Yahoo rung ────────────────────────────────────────────────────

def _yahoo_symbol(symbol: str) -> str:
    return "^VIX" if symbol == VIX_SYMBOL else symbol


def _utc_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _closes_from_timestamps(timestamps: list[int], closes: list[Any]) -> Closes:
    return {_utc_date(ts): float(c) for ts, c in zip(timestamps, closes) if c is not None}


def _yahoo_get(url: str) -> Any:
    from urllib.request import Request, urlopen

    time.sleep(YAHOO_COURTESY_SLEEP_S)
    request = Request(url, headers={"User-Agent": YAHOO_USER_AGENT})
    with urlopen(request, timeout=YAHOO_TIMEOUT_S) as response:
        return json.load(response)


def parse_yahoo_spark(payload: dict[str, Any]) -> dict[str, Closes]:
    """{yahoo_symbol: {date: close}} from a v8 spark response keyed by symbol."""
    parsed: dict[str, Closes] = {}
    for symbol, entry in payload.items():
        if not isinstance(entry, dict):
            continue
        series = _closes_from_timestamps(entry.get("timestamp") or [], entry.get("close") or [])
        if series:
            parsed[symbol] = series
    return parsed


def parse_yahoo_chart(result: dict[str, Any]) -> Closes:
    """quote[0].close (split-adjusted, dividend-unadjusted); never adjclose."""
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    return _closes_from_timestamps(result.get("timestamp") or [], quote.get("close") or [])


def _fetch_yahoo_spark_batch(symbols: list[str]) -> dict[str, Closes]:
    from urllib.parse import quote

    yahoo_symbols = ",".join(_yahoo_symbol(s) for s in symbols)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/spark"
        f"?symbols={quote(yahoo_symbols)}&range={YAHOO_INCREMENTAL_RANGE}&interval=1d"
    )
    parsed = parse_yahoo_spark(_yahoo_get(url))
    return {s: parsed[_yahoo_symbol(s)] for s in symbols if _yahoo_symbol(s) in parsed}


def _fetch_yahoo_chart(symbol: str, period1: str, now: datetime) -> Closes:
    start = int(datetime.fromisoformat(period1).replace(tzinfo=timezone.utc).timestamp())
    end = int((now + timedelta(days=1)).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{_yahoo_symbol(symbol)}"
        f"?period1={start}&period2={end}&interval=1d"
    )
    return parse_yahoo_chart(_yahoo_get(url)["chart"]["result"][0])


def _fetch_yahoo_incremental(symbols: list[str], deadline: float) -> dict[str, Closes]:
    fetched: dict[str, Closes] = {}
    for start in range(0, len(symbols), YAHOO_SPARK_BATCH_SIZE):
        if time.monotonic() >= deadline:
            _log(f"Yahoo: sweep budget spent at {start}/{len(symbols)}")
            break
        batch = symbols[start : start + YAHOO_SPARK_BATCH_SIZE]
        try:
            fetched.update(_fetch_yahoo_spark_batch(batch))
        except Exception as exc:  # noqa: BLE001 — the batch is reported as failed
            _log(f"Yahoo: spark batch at {start} failed: {exc}")
    return fetched


def _fetch_yahoo_backfill(symbols: list[str], deadline: float) -> dict[str, Closes]:
    fetched: dict[str, Closes] = {}
    now = datetime.now(timezone.utc)
    for index, symbol in enumerate(symbols):
        if time.monotonic() >= deadline:
            _log(f"Yahoo: sweep budget spent at {index}/{len(symbols)}")
            break
        try:
            fetched[symbol] = _fetch_yahoo_chart(symbol, YAHOO_BACKFILL_PERIOD1, now)
        except Exception as exc:  # noqa: BLE001 — the symbol is reported as failed
            _log(f"Yahoo: {symbol} failed: {exc}")
    return fetched


def _fetch_yahoo_closes(symbols: list[str], backfill: bool, deadline: float) -> dict[str, Closes]:
    if not symbols:
        return {}
    _log(f"Yahoo fallback for {len(symbols)} symbols IB left empty")
    if backfill:
        return _fetch_yahoo_backfill(symbols, deadline)
    return _fetch_yahoo_incremental(symbols, deadline)


# ── ladder ────────────────────────────────────────────────────────

def fetch_closes_ladder(symbols: list[str], backfill: bool) -> SweepCloses:
    """IB for every symbol inside IB_SWEEP_BUDGET_S, then Yahoo for whatever IB
    left empty with its own YAHOO_SWEEP_BUDGET_S. One shared deadline handed
    the fallback nothing once an HMDS-inactive gateway had burned it (R-446)."""
    started = time.monotonic()
    duration = BACKFILL_DURATION if backfill else INCREMENTAL_DURATION
    closes = SweepCloses()
    closes.take(_fetch_ib_closes(symbols, duration, started + IB_SWEEP_BUDGET_S), SOURCE_IB)
    _log(f"IB served {len(closes)}/{len(symbols)} symbols ({duration})")
    yahoo_deadline = max(started + SWEEP_BUDGET_S, time.monotonic() + YAHOO_SWEEP_BUDGET_S)
    closes.take(_fetch_yahoo_closes(closes.missing(symbols), backfill, yahoo_deadline), SOURCE_YAHOO)
    return closes


def _describe_fetch(symbols: list[str], closes: dict[str, Closes]) -> dict[str, Any]:
    sources = _symbol_sources(closes)
    failed = [s for s in symbols if s not in closes]
    return {
        "ib_ok": sum(1 for s in sources.values() if s == SOURCE_IB),
        "yahoo_ok": sum(1 for s in sources.values() if s == SOURCE_YAHOO),
        "failed": len(failed),
        "failed_symbols": failed,
    }


def _symbol_sources(closes: dict[str, Closes]) -> dict[str, str]:
    """An injected plain dict carries no rung; every present symbol counts as IB."""
    declared = getattr(closes, "sources", {})
    return {symbol: declared.get(symbol, SOURCE_IB) for symbol in closes}


def _describe_source(closes: dict[str, Closes]) -> dict[str, str]:
    sources = _symbol_sources(closes)
    prices = {s for symbol, s in sources.items() if symbol != VIX_SYMBOL}
    return {
        "prices": _collapse_sources(prices),
        "vix": sources.get(VIX_SYMBOL, SOURCE_NONE),
    }


def _collapse_sources(sources: set[str]) -> str:
    if not sources:
        return SOURCE_NONE
    if len(sources) == 1:
        return next(iter(sources))
    return SOURCE_MIXED


def _ib_rung_warning(fetch: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A sweep IB served nothing on (gateway skipped, connect failed, every
    request empty) is real data Yahoo built, not a healthy steady state:
    rule 7 forbids Yahoo as the scheduled primary. The heartbeat stays ok
    and carries the class so the operator can see the rung is dead (R-434)."""
    if fetch["ib_ok"] > 0:
        return None
    return {
        "message": (
            f"dispersion: IB rung served 0 symbols; Yahoo served {fetch['yahoo_ok']}, "
            f"{fetch['failed']} failed"
        ),
        "class": "ib_rung_dead",
    }


# ── universe ──────────────────────────────────────────────────────

def _resolve_universe() -> dict[str, list[str]]:
    from clients.index_constituents import resolve_constituents

    tickers, source = resolve_constituents("SPX")
    _log(f"universe: {len(tickers)} SPX constituents via {source}")
    return {"stocks": list(tickers), "sectors": list(SECTOR_ETFS)}


def _describe_universe(universe: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "index": "SPX",
        "n_constituents": len(universe["stocks"]),
        "sectors": list(universe["sectors"]),
    }


# ── persistence ───────────────────────────────────────────────────

def _read_stored_rows() -> list[dict[str, Any]]:
    """Turso dispersion_history, keyset-paged on date (Hrana I/O bounding)."""
    from db.client import get_db

    db = get_db()
    rows: list[dict[str, Any]] = []
    cursor = ""
    while True:
        page = db.execute(
            "SELECT date, vix_close, stock_spread, sector_spread, n_stocks, n_sectors "
            "FROM dispersion_history WHERE date > ? ORDER BY date LIMIT ?",
            (cursor, HISTORY_READ_PAGE_ROWS),
        ).fetchall()
        if not page:
            break
        rows.extend(_stored_row(record) for record in page)
        cursor = page[-1][0]
        if len(page) < HISTORY_READ_PAGE_ROWS:
            break
    return rows


def _stored_row(record: Any) -> dict[str, Any]:
    return {
        "date": record[0],
        "vix_close": float(record[1]),
        "stock_spread": float(record[2]),
        "sector_spread": float(record[3]),
        "n_stocks": int(record[4]),
        "n_sectors": int(record[5]),
    }


def _load_stored_rows() -> list[dict[str, Any]]:
    """Turso is the source of truth; a failed read stops the run rather than
    rebuilding from the lossy JSON fallback (its series lacks the 60-session
    warm-up and the cross-section sizes)."""
    try:
        rows = _read_stored_rows()
    except Exception as exc:  # noqa: BLE001 — re-raised with the service named
        raise RuntimeError(f"dispersion_history read failed: {exc}") from exc
    return sorted(rows, key=lambda r: r["date"])


def _read_json_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(DISPERSION_JSON.read_text())
    except (OSError, ValueError):
        return None


def _write_json_cache(payload: dict[str, Any]) -> None:
    DISPERSION_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = DISPERSION_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, DISPERSION_JSON)


def _write_db(
    payload: dict[str, Any],
    new_rows: list[dict[str, Any]],
    scan_time: str,
    *,
    rows_changed: bool,
    health_error: Optional[dict[str, Any]] = None,
    health_warning: Optional[dict[str, Any]] = None,
) -> None:
    """Rows (only when changed) -> snapshot -> heartbeat, each isolated (R-192).

    A failed row upsert must not take the snapshot and the heartbeat down
    with it, and surfaces as an error heartbeat rather than a silent exit 0.
    ``health_warning`` rides on an ok heartbeat as its error payload: the
    run succeeded but not the way the ladder is meant to (R-434).
    """
    if writer is None:
        return
    row_error = _upsert_rows(new_rows, scan_time) if rows_changed else None
    snapshot_error = _upsert_snapshot(payload, scan_time)
    error = health_error or row_error or snapshot_error
    if error is None:
        _heartbeat("ok", scan_time, health_warning)
    else:
        _heartbeat("error", scan_time, error)


def _upsert_rows(new_rows: list[dict[str, Any]], scan_time: str) -> Optional[dict[str, Any]]:
    try:
        writer.upsert_dispersion_rows(new_rows, recorded_at=scan_time)
        return None
    except Exception as exc:  # noqa: BLE001
        _log(f"row upsert failed: {exc}")
        return {"message": f"dispersion row upsert failed: {exc}", "class": "db_write_failed"}


def _upsert_snapshot(payload: dict[str, Any], scan_time: str) -> Optional[dict[str, Any]]:
    try:
        writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
        return None
    except Exception as exc:  # noqa: BLE001
        _log(f"snapshot write failed: {exc}")
        return {"message": f"dispersion snapshot write failed: {exc}", "class": "db_write_failed"}


def _heartbeat(state: str, scan_time: str, error: Optional[dict[str, Any]]) -> None:
    if writer is None:
        return
    try:
        writer.record_service_health(SERVICE, state, finished_at=scan_time, error=error)
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        _log(f"health heartbeat non-fatal: {exc}")


def _record_cycle_failure(scan_time: str, exc: Exception) -> None:
    _heartbeat("error", scan_time, {"message": f"dispersion cycle failed: {exc}", "class": "cycle_failed"})


# ── orchestration ─────────────────────────────────────────────────

def _no_fetch_report() -> dict[str, Any]:
    return {"ib_ok": 0, "yahoo_ok": 0, "failed": 0, "failed_symbols": []}


def _stored_source() -> dict[str, str]:
    return {"prices": SOURCE_STORED, "vix": SOURCE_STORED}


def _refresh_snapshot_only(
    stored: list[dict[str, Any]], scan_time: str, universe: Optional[dict[str, list[str]]]
) -> dict[str, Any]:
    _log(f"no new session since {stored[-1]['date']}; refreshing snapshot only")
    payload = build_payload(
        stored,
        scan_time=scan_time,
        status=STATUS_OK,
        source=_stored_source(),
        universe=_describe_universe(universe) if universe else _universe_from_rows(stored),
        fetch=_no_fetch_report(),
    )
    _write_db(payload, [], scan_time, rows_changed=False)
    _write_json_cache(payload)
    return payload


def _universe_from_rows(stored: list[dict[str, Any]]) -> dict[str, Any]:
    """No constituent fetch on the no-new-session path; describe what the rows saw."""
    return {"index": "SPX", "n_constituents": stored[-1]["n_stocks"], "sectors": list(SECTOR_ETFS)}


def _serve_stale(
    stored: list[dict[str, Any]],
    scan_time: str,
    reason: str,
    *,
    source: dict[str, str],
    universe: dict[str, Any],
    fetch: dict[str, Any],
) -> dict[str, Any]:
    """Re-serve the stored series as stale_source with an error heartbeat; never
    latch ok over unconfirmed data (the ivrank posture)."""
    _log(f"{reason}; re-serving the stored series as stale_source")
    payload = _stale_payload(stored, scan_time, source=source, universe=universe, fetch=fetch)
    health_error = {"message": f"dispersion: {reason}", "class": "stale_source"}
    _write_db(payload, [], scan_time, rows_changed=False, health_error=health_error)
    _write_json_cache(payload)
    return payload


def _stale_payload(
    stored: list[dict[str, Any]], scan_time: str, **provenance: Any
) -> dict[str, Any]:
    try:
        return build_payload(stored, scan_time=scan_time, status=STATUS_STALE_SOURCE, **provenance)
    except ValueError as exc:
        cached = _read_json_cache()
        if not cached:
            raise RuntimeError(f"dispersion: source unavailable and no cached payload ({exc})") from exc
        return {**cached, "scan_time": scan_time, "status": STATUS_STALE_SOURCE}


def _ensure_window_bridges_gap(stored: list[dict[str, Any]], sessions: list[str]) -> None:
    if not stored or not sessions:
        return
    stored_max = stored[-1]["date"]
    if sessions[0] > stored_max:
        raise RuntimeError(
            f"dispersion: gap since {stored_max} exceeds the incremental window; rerun with --backfill"
        )


def _merge_rows(stored: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date = {row["date"]: row for row in stored}
    by_date.update((row["date"], row) for row in new_rows)
    return [by_date[d] for d in sorted(by_date)]


def _select_new_rows(
    fetched_rows: list[dict[str, Any]], stored: list[dict[str, Any]], backfill: bool
) -> list[dict[str, Any]]:
    if backfill or not stored:
        return fetched_rows
    stored_max = stored[-1]["date"]
    return [row for row in fetched_rows if row["date"] > stored_max]


def _sweep_and_build(
    stored: list[dict[str, Any]],
    *,
    backfill: bool,
    fetch_closes: FetchCloses,
    universe: dict[str, list[str]],
    scan_time: str,
    last_complete: str,
) -> dict[str, Any]:
    symbols = list(universe["stocks"]) + list(universe["sectors"]) + [VIX_SYMBOL]
    closes = fetch_closes(symbols, backfill)
    provenance = {
        "source": _describe_source(closes),
        "universe": _describe_universe(universe),
        "fetch": _describe_fetch(symbols, closes),
    }
    sessions = master_sessions(closes)
    if not sessions:
        return _serve_stale(stored, scan_time, "VIX unavailable from IB and Yahoo", **provenance)
    fetched_rows = build_raw_rows(closes, universe["stocks"], universe["sectors"])
    if last_complete not in {row["date"] for row in fetched_rows}:
        return _serve_stale(
            stored, scan_time, f"cross-section on {last_complete} below the floor", **provenance
        )
    if not backfill:
        _ensure_window_bridges_gap(stored, sessions)
    new_rows = _select_new_rows(fetched_rows, stored, backfill)
    ensure_plausible_rows(new_rows, backfill=backfill)
    merged = _merge_rows(stored, new_rows)
    payload = build_payload(merged, scan_time=scan_time, status=STATUS_OK, **provenance)
    _log(
        f"{len(new_rows)} new rows, {payload['count']} sessions through {payload['data_date']} "
        f"({payload['current']['regime']}, gap {payload['current']['surface_gap']})"
    )
    _write_db(
        payload,
        new_rows,
        scan_time,
        rows_changed=bool(new_rows),
        health_warning=_ib_rung_warning(provenance["fetch"]),
    )
    _write_json_cache(payload)
    return payload


def run(
    *,
    backfill: bool = False,
    fetch_closes: Optional[FetchCloses] = None,
    now: Optional[datetime] = None,
    universe: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Rehydrate stored rows, sweep only when a new session exists, rebuild,
    dual-write, return the payload. Weekend and holiday runs make no
    IB or Yahoo requests."""
    now = now or datetime.now(timezone.utc)
    scan_time = _iso(now)
    if writer is not None:
        writer.ensure_no_replica_for_writers()
    try:
        stored = _load_stored_rows()
        last_complete = last_completed_session_date(now)
        if not backfill and stored and stored[-1]["date"] >= last_complete:
            return _refresh_snapshot_only(stored, scan_time, universe)
        if not backfill and not stored:
            raise RuntimeError("dispersion: no stored rows; seed the table with --backfill")
        return _sweep_and_build(
            stored,
            backfill=backfill,
            fetch_closes=fetch_closes or fetch_closes_ladder,
            universe=universe or _resolve_universe(),
            scan_time=scan_time,
            last_complete=last_complete,
        )
    except Exception as exc:
        _record_cycle_failure(scan_time, exc)
        raise


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload.get("current") or {}
    stats = payload.get("stats") or {}
    print(
        f"\nDISPERSION — {payload['count']} sessions through {payload.get('data_date')} [{payload['status']}]",
        file=sys.stderr,
    )
    print(
        f"  regime     {current.get('regime')}  surface gap {current.get('surface_gap')}",
        file=sys.stderr,
    )
    print(
        f"  z          vix {current.get('z_vix')} / stock {current.get('z_stock')} / sector {current.get('z_sector')}",
        file=sys.stderr,
    )
    print(
        f"  raw        vix {current.get('vix')} / stock {current.get('stock_spread')} / "
        f"sector {current.get('sector_spread')} ({current.get('n_stocks')} stocks, {current.get('n_sectors')} sectors)",
        file=sys.stderr,
    )
    if stats:
        print(
            f"  below      {stats['days_below_surface']} days, last {stats['last_below_surface_date']}",
            file=sys.stderr,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DISPERSION — VIX vs single-stock vs cross-sector dispersion (IB, Yahoo fallback)"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--backfill", action="store_true", help="10 Y IB seed run; replaces every stored row")
    args = parser.parse_args()

    payload = run(backfill=args.backfill)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)
    if payload.get("status") == STATUS_STALE_SOURCE:
        sys.exit(1)


if __name__ == "__main__":
    main()

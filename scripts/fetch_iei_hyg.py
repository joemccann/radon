#!/usr/bin/env python3
"""IEI/HYG Indicator — Treasuries vs high yield, 52-week extremes.

Daily aligned closes of IEI (3-7y Treasury ETF) and HYG (HY corporate ETF).
The ratio IEI/HYG falls when high yield outperforms Treasuries (spreads
tightening, risk-on) and rises when credit is sold (risk-off). A session is
``new_low`` / ``new_high`` when its ratio IS the trailing 252-session
extreme. The US Dollar Index is stored as a nullable overlay only.

Sources, in order (never skip ahead):
  1. Interactive Brokers — Stock IEI/HYG on SMART, Index DX (then DXY) on NYBOT
  2. Unusual Whales — regular-session OHLC for IEI/HYG. DXY is not on UW.
  3. Robinhood (official trading MCP, read-only) — equity historicals for
     IEI/HYG when configured. DXY is not on Robinhood.
  4. Yahoo Finance — ABSOLUTE LAST RESORT: IEI, HYG, DX-Y.NYB

Output is dual-written to Turso iei_hyg_history + data/iei_hyg.json.

Usage:
    python3 scripts/fetch_iei_hyg.py          # human summary (stderr)
    python3 scripts/fetch_iei_hyg.py --json   # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

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

from db import writer
from fetch_credit_spread import (
    _bar_date,
    _connect_ib_with_retry,
    combine_source,
    fetch_yahoo_chart,
    parse_yahoo_chart,
)
from utils.ib_preflight import (
    IB_HISTORICAL_TIMEOUT_S,
    IB_REQUEST_TIMEOUT_S,
    ib_auth_state as _ib_auth_state,
)

# ── constants ─────────────────────────────────────────────────────
SERVICE = "iei-hyg"
IEI_HYG_JSON = _PROJECT_DIR / "data" / "iei_hyg.json"
NO_SOURCE = "none"
STATUS_STALE_SOURCE = "stale_source"

WINDOW_SESSIONS = 252
# R-126: `extremes_window` is a trailing slice, so on a 30-session series the
# latest row IS the window extreme nearly every day and `classify_state`
# published NEW 52W LOW/HIGH off a month of data. No partial-window extreme,
# ever — the same rule `fetch_ivrank.MIN_OBSERVATIONS` already enforces.
MIN_OBSERVATIONS = WINDOW_SESSIONS
IEI_SYMBOL = "IEI"
HYG_SYMBOL = "HYG"
DXY_SYMBOL = "DXY"
TICKERS = [IEI_SYMBOL, HYG_SYMBOL, DXY_SYMBOL]
YAHOO_SYMBOLS = {IEI_SYMBOL: "IEI", HYG_SYMBOL: "HYG", DXY_SYMBOL: "DX-Y.NYB"}
UW_SKIP = frozenset({DXY_SYMBOL})
RH_SKIP = frozenset({DXY_SYMBOL})  # Robinhood equity historicals: no FX index
UW_REGULAR_SESSION = "r"
# Shared with credit-spread: the jobs run 21:45 and 21:55 UTC and never overlap.
IEI_HYG_IB_HISTORY_CLIENT_IDS = (56, 69)
DXY_IB_SYMBOLS = ("DX", "DXY")

Closes = dict[str, float]
FetchCloses = Callable[[list[str]], dict[str, Closes]]


def _log(message: str) -> None:
    print(f"[{SERVICE}] {message}", file=sys.stderr)


# ── Yahoo ─────────────────────────────────────────────────────────

def fetch_yahoo_closes(tickers: list[str], session: Optional[Any] = None) -> dict[str, Closes]:
    """Last-resort Yahoo chart closes. ``tickers`` are IEI/HYG/DXY, not Yahoo symbols."""
    import requests

    http = session or requests
    out: dict[str, Closes] = {}
    for ticker in tickers:
        yahoo_sym = YAHOO_SYMBOLS.get(ticker, ticker)
        try:
            parsed = parse_yahoo_chart(fetch_yahoo_chart(yahoo_sym, http))
        except Exception as exc:
            _log(f"Yahoo: {ticker} ({yahoo_sym}) failed: {exc}")
            continue
        if parsed:
            out[ticker] = parsed
            _log(f"Yahoo: {ticker} {len(parsed)} bars")
    return out


# ── Interactive Brokers ───────────────────────────────────────────

def _ib_contract_candidates(ticker: str) -> list[Any]:
    from ib_insync import Index, Stock

    if ticker == DXY_SYMBOL:
        return [Index(sym, "NYBOT", "USD") for sym in DXY_IB_SYMBOLS]
    return [Stock(ticker, "SMART", "USD")]


async def _qualify_first(ib: Any, candidates: list[Any]) -> Optional[Any]:
    import asyncio

    for contract in candidates:
        try:
            qualified = await asyncio.wait_for(
                ib.qualifyContractsAsync(contract), timeout=IB_REQUEST_TIMEOUT_S
            )
        except Exception as exc:
            _log(f"IB: qualify {contract.symbol} failed: {exc}")
            continue
        if qualified and getattr(contract, "conId", 0):
            return contract
    return None


async def _ib_daily_closes(ib: Any, contract: Any) -> Closes:
    import asyncio

    bars = await asyncio.wait_for(
        ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="1 Y",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        ),
        timeout=IB_HISTORICAL_TIMEOUT_S,
    )
    return {_bar_date(b.date): float(b.close) for b in bars or [] if b.close is not None}


def _ib_gateway_unavailable() -> bool:
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        _log(f"IB skipped: gateway auth_state={auth_state} (falling back to UW/RH/Yahoo)")
        return True
    return False


def fetch_ib_closes(tickers: list[str]) -> dict[str, Closes]:
    """1Y daily TRADES bars. A DXY qualify failure skips DXY, not the run."""
    if _ib_gateway_unavailable():
        return {}
    try:
        from ib_insync import IB
    except ImportError:
        return {}

    ib = IB()
    if not _connect_ib_with_retry(ib, client_ids=IEI_HYG_IB_HISTORY_CLIENT_IDS):
        return {}

    import asyncio

    results: dict[str, Closes] = {}

    async def _fetch_one(ticker: str) -> None:
        contract = await _qualify_first(ib, _ib_contract_candidates(ticker))
        if contract is None:
            _log(f"IB: {ticker} not qualified, falling back")
            return
        try:
            parsed = await _ib_daily_closes(ib, contract)
        except asyncio.TimeoutError:
            _log(f"IB: {ticker} timed out, falling back")
            return
        except Exception as exc:
            _log(f"IB: {ticker} failed: {exc}")
            return
        if parsed:
            results[ticker] = parsed
            _log(f"IB: {ticker} {len(parsed)} bars")
        else:
            _log(f"IB: {ticker} no bars returned")

    async def _fetch_all() -> None:
        await asyncio.gather(*[_fetch_one(t) for t in tickers])

    try:
        ib.run(_fetch_all())
    finally:
        ib.disconnect()
    return results


# ── Unusual Whales ────────────────────────────────────────────────

def uw_regular_closes(rows: list[dict[str, Any]]) -> Closes:
    """date -> close for regular-session rows only (UW 1d returns pr/r/po per date)."""
    return {
        _bar_date(row["date"]): float(row["close"])
        for row in rows
        if row.get("market_time") == UW_REGULAR_SESSION
        and row.get("date")
        and row.get("close") is not None
    }


def fetch_uw_closes(tickers: list[str]) -> dict[str, Closes]:
    fetchable = [t for t in tickers if t not in UW_SKIP]
    if not fetchable:
        return {}
    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}

    results: dict[str, Closes] = {}
    try:
        with UWClient() as uw:
            for ticker in fetchable:
                try:
                    data = uw.get_stock_ohlc(ticker, candle_size="1d")
                    parsed = uw_regular_closes(data.get("data") or [])
                except Exception as exc:
                    _log(f"UW: {ticker} failed: {exc}")
                    continue
                if parsed:
                    results[ticker] = parsed
                    _log(f"UW: {ticker} {len(parsed)} bars")
    except Exception as exc:
        _log(f"UW connection failed: {exc}")
    return results


# ── cascade ───────────────────────────────────────────────────────

def _take(
    fetch: FetchCloses,
    wanted: list[str],
    label: str,
    closes: dict[str, Closes],
    sources: dict[str, str],
) -> None:
    missing = [t for t in wanted if t not in closes]
    if not missing:
        return
    for ticker, series in fetch(missing).items():
        if series:
            closes[ticker] = series
            sources[ticker] = label


def _robinhood_degradation(sources: dict[str, str]) -> Optional[dict[str, Any]]:
    """Heartbeat detail when the Robinhood rung failed and Yahoo served (REL-174)."""
    try:
        from clients.robinhood_client import robinhood_degradation
    except ImportError:
        return None
    return robinhood_degradation(SERVICE, sources, skip=RH_SKIP)


def fetch_rh_closes(tickers: list[str]) -> dict[str, Closes]:
    """Robinhood daily closes (read-only MCP). DXY is skipped.

    Ranked after IB and UW, before Yahoo. Unconfigured hosts return {}
    without any network I/O so the ladder falls through to Yahoo.
    """
    fetchable = [t for t in tickers if t not in RH_SKIP]
    if not fetchable:
        return {}
    try:
        from clients.robinhood_client import fetch_robinhood_closes
    except ImportError:
        return {}
    return fetch_robinhood_closes(fetchable)


def fetch_closes(
    tickers: Optional[list[str]] = None,
    *,
    fetch_ib: Optional[FetchCloses] = None,
    fetch_uw: Optional[FetchCloses] = None,
    fetch_rh: Optional[FetchCloses] = None,
    fetch_yahoo: Optional[FetchCloses] = None,
) -> tuple[dict[str, Closes], str, dict[str, str]]:
    """IB first, then UW for gaps, then Robinhood, then Yahoo.

    Returns ``(closes, combined source, per-ticker sources)``. R-190: the
    combined string alone cannot say WHICH leg fell back, so a mixed IB+Yahoo
    ratio was stored indistinguishably from the opposite pairing.
    """
    wanted = list(tickers or TICKERS)
    closes: dict[str, Closes] = {}
    sources: dict[str, str] = {}
    _take(fetch_ib or fetch_ib_closes, wanted, "ib", closes, sources)
    _take(fetch_uw or fetch_uw_closes, wanted, "uw", closes, sources)
    _take(fetch_rh or fetch_rh_closes, wanted, "rh", closes, sources)
    _take(fetch_yahoo or fetch_yahoo_closes, wanted, "yahoo", closes, sources)
    return closes, combine_source(sources) or NO_SOURCE, dict(sources)


# ── series assembly ───────────────────────────────────────────────

def _row(date: str, iei_close: float, hyg_close: float, dxy_close: Optional[float]) -> dict[str, Any]:
    return {
        "date": date,
        "iei_close": iei_close,
        "hyg_close": hyg_close,
        "dxy_close": dxy_close,
        "ratio": iei_close / hyg_close,
    }


def align_series(iei: Closes, hyg: Closes, dxy: Closes) -> list[dict[str, Any]]:
    """Inner join on IEI and HYG dates, DXY left-joined (None when absent)."""
    return [
        _row(date, iei[date], hyg[date], dxy.get(date))
        for date in sorted(set(iei) & set(hyg))
        if hyg[date]
    ]


def extremes_window(series: list[dict[str, Any]], n: int = WINDOW_SESSIONS) -> list[dict[str, Any]]:
    return series[-n:]


def classify_state(ratio: float, low: float, high: float) -> str:
    if low == high:
        return "neutral"
    if ratio == low:
        return "new_low"
    if ratio == high:
        return "new_high"
    return "neutral"


def pct_rank(ratio: float, low: float, high: float) -> Optional[float]:
    """R-162: a single-distinct-ratio window has NO percentile.

    Returning 0.0 fabricated the strongest risk-on reading this indicator
    emits — "IEI/HYG at the bottom of its 52-week range", i.e. maximum
    high-yield outperformance — from no data, while `classify_state` on the
    same input correctly said `"neutral"`. The two published fields
    contradicted each other and the rank is the headline number.
    """
    if high == low:
        return None
    return (ratio - low) / (high - low)


def _current(series: list[dict[str, Any]]) -> dict[str, Any]:
    latest = series[-1]
    window = extremes_window(series)
    lowest = min(window, key=lambda r: r["ratio"])
    highest = max(window, key=lambda r: r["ratio"])
    low, high = lowest["ratio"], highest["ratio"]
    complete = len(window) >= MIN_OBSERVATIONS
    return {
        "date": latest["date"],
        "iei_close": latest["iei_close"],
        "hyg_close": latest["hyg_close"],
        "dxy_close": latest["dxy_close"],
        "ratio": latest["ratio"],
        "ratio_52w_low": low,
        "low_date": lowest["date"],
        "ratio_52w_high": high,
        "high_date": highest["date"],
        "ratio_pct_rank": pct_rank(latest["ratio"], low, high) if complete else None,
        "window_sessions": len(window),
        "window_complete": complete,
        "state": classify_state(latest["ratio"], low, high) if complete else "unknown",
    }


def build_output(
    series: list[dict[str, Any]],
    scan_time: Optional[str] = None,
    source: str = "ib",
    source_by_ticker: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    stamp = scan_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "scan_time": stamp,
        "source": source,
        # R-190: the collapsed `source` string cannot say WHICH leg fell back,
        # so a mixed IB+Yahoo ratio was stored indistinguishably from the
        # other mixed pairing. The per-ticker map rides alongside it.
        "source_by_ticker": dict(source_by_ticker or {}),
        "count": len(series),
        "current": _current(series) if series else None,
        "series": series,
    }


def merge_series(cached: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fresh wins per date."""
    by_date = {row["date"]: row for row in cached}
    by_date.update({row["date"]: row for row in fresh})
    return [by_date[d] for d in sorted(by_date)]


def _db_fields(row: dict[str, Any]) -> tuple:
    return (row["date"], row["iei_close"], row["hyg_close"], row.get("dxy_close"))


def diff_new_rows(cached: list[dict[str, Any]], series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows absent from or different in (date, iei_close, hyg_close, dxy_close)."""
    cached_by_date = {row["date"]: row for row in cached}
    return [
        row
        for row in series
        if row["date"] not in cached_by_date
        or _db_fields(cached_by_date[row["date"]]) != _db_fields(row)
    ]


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    IEI_HYG_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = IEI_HYG_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, IEI_HYG_JSON)


def _record_error_health(message: str, error_class: str) -> None:
    """Error heartbeat for the paths that never reach a full persist_result.

    T-162: a row that NEVER appears is worse than a stale one
    (docs/operations.md). This service carries a 26h watchdog window, so a
    cycle that dies — or returns early on an empty series — without any
    `record_service_health` call reads as ordinary staleness a day later.
    Best-effort: a broken writer must not mask the original failure.
    """
    try:
        writer.ensure_no_replica_for_writers()
        writer.record_service_health(
            SERVICE,
            "error",
            finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            error={"message": message, "class": error_class},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        _log(f"error heartbeat non-fatal: {exc}")


def persist_result(
    payload: dict[str, Any],
    changed_rows: list[dict[str, Any]],
    health_error: Optional[dict[str, Any]] = None,
    *,
    degraded: Optional[dict[str, Any]] = None,
) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    Refuses an empty series entirely, but still heartbeats an error so the
    refusal is visible. Snapshot + heartbeat run EVERY cycle so the staleness
    banner notices a silent writer; the heartbeat is an error when no source
    confirmed the series (stale_source).
    """
    if not payload["series"]:
        _log("refusing to persist empty series")
        _record_error_health(
            f"{SERVICE}: refusing to persist an empty series", "empty_series"
        )
        return
    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if changed_rows:
        writer.upsert_iei_hyg_rows(changed_rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    # REL-174 (R-470): `degraded` rides on an `ok` row — the writer succeeded
    # (writer-state semantics), but a closed Robinhood rung that demoted a
    # ticker to Yahoo is named in last_error instead of hidden under
    # `ok`/`yahoo`. It clears on the next clean cycle.
    writer.record_service_health(
        SERVICE,
        "ok" if health_error is None else "error",
        finished_at=scan_time,
        error=health_error if health_error is not None else degraded,
    )
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────

def _turso_series() -> list[dict[str, Any]]:
    """The durable series from `iei_hyg_history` (Turso-first read, R-123)."""
    from db.client import get_db

    rows = get_db().execute(
        "SELECT date, iei_close, hyg_close, dxy_close "
        "FROM iei_hyg_history ORDER BY date"
    ).fetchall()
    # `ratio` is derived, not stored — go through _row so the stored and the
    # freshly-computed series are byte-identical in shape.
    return [
        _row(
            row[0],
            float(row[1]),
            float(row[2]),
            None if row[3] is None else float(row[3]),
        )
        for row in rows
        if row[2]
    ]


def _json_series() -> list[dict[str, Any]]:
    try:
        return json.loads(IEI_HYG_JSON.read_text())["series"]
    except (OSError, ValueError, KeyError):
        return []


def load_cached_series() -> list[dict[str, Any]]:
    try:
        stored = _turso_series()
        if stored:
            return stored
    except Exception as exc:  # noqa: BLE001 — the JSON fallback still works
        _log(f"turso rehydrate non-fatal: {exc}")
    return _json_series()


def _serve_cached(cached: list[dict[str, Any]]) -> dict[str, Any]:
    """IB, UW, Robinhood and Yahoo all down: re-serve the cache as
    stale_source, page.

    R-098: see fetch_credit_spread; a dead source lived only in
    payload["source"], which nothing reads.
    """
    if not cached:
        raise RuntimeError(
            f"{SERVICE}: IB, UW, Robinhood and Yahoo all failed with no cached series"
        )
    payload = {**build_output(cached, source=NO_SOURCE), "status": STATUS_STALE_SOURCE}
    through = payload["current"]["date"]
    _log(f"all sources down; re-serving cached series through {through}")
    health_error = {
        "message": (
            f"{SERVICE}: every source failed (IB, UW, Robinhood, Yahoo); serving the "
            f"cached series through {through}"
        ),
        "class": "source_down",
    }
    persist_result(payload, [], health_error)
    return payload


def run() -> dict[str, Any]:
    """One cycle. Raises loudly on a hard failure — AND leaves an error row.

    T-162: `_serve_cached` raises when every source is down with no cache, and
    that raise used to kill the oneshot before anything touched
    `service_health`. The whole body is inside the health-reporting block
    (docs/operations.md) so an outage is recorded, not merely silent.
    """
    _log("fetching IEI, HYG and DXY (IB -> UW -> RH -> Yahoo)")
    try:
        closes, source, source_by_ticker = fetch_closes()
        cached = load_cached_series()
        if source == NO_SOURCE:
            return _serve_cached(cached)
        fresh = align_series(
            closes.get(IEI_SYMBOL, {}), closes.get(HYG_SYMBOL, {}), closes.get(DXY_SYMBOL, {})
        )
        series = merge_series(cached, fresh)
        new_rows = diff_new_rows(cached, series)
        if not new_rows:
            _log("source unchanged; refreshing snapshot only")
        payload = build_output(series, source=source, source_by_ticker=source_by_ticker)
        persist_result(payload, new_rows, degraded=_robinhood_degradation(source_by_ticker))
    except Exception as exc:
        _record_error_health(f"{SERVICE}: {exc}", "cycle_failed")
        raise
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nIEI/HYG: {payload['count']} sessions", file=sys.stderr)
    if current:
        print(f"  latest {current['date']} ratio {current['ratio']:.4f}", file=sys.stderr)
        print(f"  state {current['state']}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="IEI/HYG ratio 52-week extremes series")
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

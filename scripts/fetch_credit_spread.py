#!/usr/bin/env python3
"""CREDIT Indicator — HYG vs S&P 500 credit-equity divergence.

Daily aligned closes of HYG (high-yield credit proxy) and SPX. The two
usually rise together (risk-on). Divergence is equities up over 168 sessions
while HYG is down.

Sources, in order (never skip ahead):
  1. Interactive Brokers — Stock('HYG','SMART','USD'), Index('SPX','CBOE')
  2. Unusual Whales — OHLC for HYG. Does not serve the SPX index.
  3. Robinhood (official trading MCP, read-only) — equity historicals for
     HYG when configured. Does not serve the SPX index.
  4. Yahoo Finance — ABSOLUTE LAST RESORT for remaining gaps.

ICE CCC OAS is not stored.

Output is dual-written to Turso credit_spread_history + data/credit_spread.json.

Usage:
    python3 scripts/fetch_credit_spread.py          # human summary (stderr)
    python3 scripts/fetch_credit_spread.py --json   # JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

# ── path setup ────────────────────────────────────────────────────
from pathlib import Path

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
from utils.ib_preflight import (
    IB_HISTORICAL_TIMEOUT_S,
    IB_REQUEST_TIMEOUT_S,
    ib_auth_state as _ib_auth_state,
)

# ── constants ─────────────────────────────────────────────────────
CREDIT_SPREAD_JSON = _PROJECT_DIR / "data" / "credit_spread.json"
SERVICE = "credit-spread"
NO_SOURCE = "none"
STATUS_STALE_SOURCE = "stale_source"

LOOKBACK_SESSIONS = 168
NEAR_HIGH_RATIO = 0.97
HYG_SYMBOL = "HYG"
SPX_SYMBOL = "SPX"
YAHOO_SYMBOLS = {HYG_SYMBOL: "HYG", SPX_SYMBOL: "^GSPC"}
UW_SKIP = frozenset({SPX_SYMBOL})
RH_SKIP = frozenset({SPX_SYMBOL})  # Robinhood equity historicals: no indices
CREDIT_IB_HISTORY_CLIENT_IDS = (56, 69)

_PERIOD1 = int(datetime(2007, 4, 11, tzinfo=timezone.utc).timestamp())
FetchCloses = Callable[[list[str]], dict[str, dict[str, float]]]


# ── Yahoo chart ───────────────────────────────────────────────────

def fetch_yahoo_chart(symbol: str, session: Optional[Any] = None) -> str:
    """Yahoo v8 chart JSON for ``symbol``. Raises on non-200 or empty body.

    Browser UA: the honest radon/2.0 UA gets 429 (same as the yield-curve
    SPX overlay).
    """
    import requests

    http = session or requests
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        f"?period1={_PERIOD1}"
        f"&period2={int(datetime.now(timezone.utc).timestamp())}&interval=1d"
    )
    resp = http.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if resp.status_code != 200:
        raise ValueError(f"Yahoo chart {symbol} returned HTTP {resp.status_code}")
    if not resp.text.strip():
        raise ValueError(f"Yahoo chart {symbol} is empty")
    return resp.text


def parse_yahoo_chart(text: str) -> dict[str, float]:
    """date (YYYY-MM-DD UTC) -> close. Null closes are skipped."""
    result = json.loads(text)["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    by_date: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc)
        by_date[stamp.strftime("%Y-%m-%d")] = float(close)
    return by_date


def fetch_yahoo_closes(tickers: list[str], session: Optional[Any] = None) -> dict[str, dict[str, float]]:
    """Last-resort Yahoo chart closes. ``tickers`` are HYG/SPX, not Yahoo symbols."""
    import requests

    http = session or requests
    out: dict[str, dict[str, float]] = {}
    for ticker in tickers:
        yahoo_sym = YAHOO_SYMBOLS.get(ticker, ticker)
        try:
            parsed = parse_yahoo_chart(fetch_yahoo_chart(yahoo_sym, http))
        except Exception as exc:
            print(f"  Yahoo: {ticker} ({yahoo_sym}) failed — {exc}", file=sys.stderr)
            continue
        if parsed:
            out[ticker] = parsed
            print(f"  Yahoo: {ticker} — {len(parsed)} bars", file=sys.stderr)
    return out


def _bar_date(value: Any) -> str:
    return str(value)[:10]


def _ib_host() -> str:
    try:
        from clients.ib_client import DEFAULT_HOST

        return DEFAULT_HOST
    except Exception:
        return os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")


def _connect_ib_with_retry(
    ib: Any,
    client_ids: tuple[int, ...] = CREDIT_IB_HISTORY_CLIENT_IDS,
    ports: tuple[int, ...] = (4001, 7497),
    timeout: int = 8,
) -> bool:
    host = _ib_host()
    for client_id in client_ids:
        for port in ports:
            try:
                ib.connect(host, port, clientId=client_id, timeout=timeout)
                return True
            except Exception as exc:
                print(
                    f"  IB connect failed on port {port} clientId {client_id}: {exc}",
                    file=sys.stderr,
                )
    return False


def fetch_ib_closes(tickers: list[str]) -> dict[str, dict[str, float]]:
    """1Y daily TRADES bars. Skip the socket when the gateway is awaiting 2FA."""
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        print(
            f"  IB skipped — gateway auth_state={auth_state} (falling back to UW/RH/Yahoo)",
            file=sys.stderr,
        )
        return {}

    try:
        from ib_insync import IB, Index, Stock
    except ImportError:
        return {}

    ib = IB()
    if not _connect_ib_with_retry(ib):
        return {}

    import asyncio

    results: dict[str, dict[str, float]] = {}

    async def _qualify_and_fetch(ticker: str) -> None:
        if ticker == SPX_SYMBOL:
            contract = Index(ticker, "CBOE")
        else:
            contract = Stock(ticker, "SMART", "USD")
        try:
            await asyncio.wait_for(
                ib.qualifyContractsAsync(contract),
                timeout=IB_REQUEST_TIMEOUT_S,
            )
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
            parsed = {
                _bar_date(b.date): float(b.close)
                for b in bars or []
                if b.close is not None
            }
            if parsed:
                results[ticker] = parsed
                print(f"  IB: {ticker} — {len(parsed)} bars", file=sys.stderr)
            else:
                print(f"  IB: {ticker} — no bars returned", file=sys.stderr)
        except asyncio.TimeoutError:
            print(
                f"  IB: {ticker} timed out — falling back",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  IB: {ticker} failed — {exc}", file=sys.stderr)

    async def _fetch_all() -> None:
        await asyncio.gather(*[_qualify_and_fetch(t) for t in tickers])

    try:
        ib.run(_fetch_all())
    finally:
        ib.disconnect()

    return results


def fetch_uw_closes(tickers: list[str]) -> dict[str, dict[str, float]]:
    """UW daily OHLC. Indices (SPX) are skipped — UW has no index history."""
    fetchable = [t for t in tickers if t not in UW_SKIP]
    if not fetchable:
        return {}

    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}

    results: dict[str, dict[str, float]] = {}
    try:
        with UWClient() as uw:
            for ticker in fetchable:
                try:
                    data = uw.get_stock_ohlc(ticker, candle_size="1d")
                    parsed = {
                        _bar_date(b["date"]): float(b["close"])
                        for b in data.get("data") or []
                        if b.get("date") and b.get("close") is not None
                    }
                    if parsed:
                        results[ticker] = parsed
                        print(f"  UW: {ticker} — {len(parsed)} bars", file=sys.stderr)
                except Exception as exc:
                    print(f"  UW: {ticker} failed — {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"  UW connection failed — {exc}", file=sys.stderr)

    return results


def fetch_rh_closes(tickers: list[str]) -> dict[str, dict[str, float]]:
    """Robinhood daily closes (read-only MCP). Indices (SPX) are skipped.

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
) -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """IB first, then UW for gaps, then Robinhood, then Yahoo.

    Yahoo is never called on a hit from any higher rung.
    """
    wanted = list(tickers or [HYG_SYMBOL, SPX_SYMBOL])
    ib_fn = fetch_ib or fetch_ib_closes
    uw_fn = fetch_uw or fetch_uw_closes
    rh_fn = fetch_rh or fetch_rh_closes
    yahoo_fn = fetch_yahoo or fetch_yahoo_closes

    closes: dict[str, dict[str, float]] = {}
    sources: dict[str, str] = {}

    for ticker, series in ib_fn(wanted).items():
        if series:
            closes[ticker] = series
            sources[ticker] = "ib"

    missing = [t for t in wanted if t not in closes]
    if missing:
        for ticker, series in uw_fn(missing).items():
            if series:
                closes[ticker] = series
                sources[ticker] = "uw"

    missing = [t for t in wanted if t not in closes]
    if missing:
        for ticker, series in rh_fn(missing).items():
            if series:
                closes[ticker] = series
                sources[ticker] = "rh"

    missing = [t for t in wanted if t not in closes]
    if missing:
        for ticker, series in yahoo_fn(missing).items():
            if series:
                closes[ticker] = series
                sources[ticker] = "yahoo"

    return closes, sources


def combine_source(sources: dict[str, str]) -> str:
    """Collapse the per-ticker map to the legacy display string.

    R-190: this is LOSSY on purpose (it is what the `source` column has always
    held), but it was the only thing that reached the payload — so an IB HYG
    with a Yahoo SPX and a Yahoo HYG with an IB SPX were both "ib+yahoo", and
    which leg was the fallback was unrecoverable. `build_output` now carries
    the map alongside it.
    """
    return "+".join(sorted(set(sources.values())))


# ── series assembly ───────────────────────────────────────────────

def align_series(hyg: dict[str, float], spx: dict[str, float]) -> list[dict[str, Any]]:
    """Inner-join on date, ascending {date, hyg_close, spx_close}."""
    return [
        {"date": date, "hyg_close": hyg[date], "spx_close": spx[date]}
        for date in sorted(set(hyg) & set(spx))
    ]


def lookback_window(series: list[dict[str, Any]], n: int = LOOKBACK_SESSIONS) -> list[dict[str, Any]]:
    return series[-n:]


def lookback_return(window: list[dict[str, Any]], key: str) -> Optional[float]:
    """last / first - 1 when both finite and first != 0; else None."""
    if len(window) < 2:
        return None
    first = window[0].get(key)
    last = window[-1].get(key)
    try:
        first_f = float(first)
        last_f = float(last)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(first_f) and math.isfinite(last_f)) or first_f == 0:
        return None
    return last_f / first_f - 1


def classify_regime(spx_ret: Optional[float], hyg_ret: Optional[float]) -> Optional[str]:
    """Strict inequalities. Zero return is coupled; MISSING is no regime.

    R-161: a missing or non-finite return used to map onto `"coupled"`, which
    is also the benign risk-on label. `lookback_return` returns None for a
    window shorter than 2 rows, a non-numeric close or a zero denominator, so
    a one-session series — a first run after a cache wipe, or a provider
    cascade returning a single overlapping date — reported "credit and
    equities in agreement". This indicator exists to surface `"divergent"`;
    its failure mode must not be the reassuring label.
    """
    if spx_ret is None or hyg_ret is None:
        return None
    if not (math.isfinite(spx_ret) and math.isfinite(hyg_ret)):
        return None
    if spx_ret > 0 and hyg_ret < 0:
        return "divergent"
    if spx_ret > 0 and hyg_ret > 0:
        return "coupled"
    if spx_ret < 0 and hyg_ret < 0:
        return "risk-off"
    if spx_ret < 0 and hyg_ret > 0:
        return "credit-lead"
    return "coupled"


def is_near_high(last: float, high: float, ratio: float = NEAR_HIGH_RATIO) -> bool:
    try:
        last_f = float(last)
        high_f = float(high)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(last_f) and math.isfinite(high_f)):
        return False
    return last_f >= ratio * high_f


def build_series(aligned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"date": row["date"], "hyg_close": row["hyg_close"], "spx_close": row["spx_close"]}
        for row in aligned
    ]


def merge_series(
    cached: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fresh wins per date."""
    by_date = {row["date"]: row for row in cached}
    by_date.update({row["date"]: row for row in fresh})
    return [by_date[d] for d in sorted(by_date)]


def _db_fields(row: dict[str, Any]) -> tuple:
    return (row["date"], row["hyg_close"], row["spx_close"])


def diff_new_rows(
    cached: list[dict[str, Any]], series: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Rows absent from or different in (date, hyg_close, spx_close)."""
    cached_by_date = {row["date"]: row for row in cached}
    return [
        row
        for row in series
        if row["date"] not in cached_by_date
        or _db_fields(cached_by_date[row["date"]]) != _db_fields(row)
    ]


def build_output(
    series: list[dict[str, Any]],
    scan_time: Optional[str] = None,
    source: str = "ib",
    source_by_ticker: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    stamp = scan_time or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    current: Optional[dict[str, Any]] = None
    if series:
        latest = series[-1]
        window = lookback_window(series)
        hyg_ret = lookback_return(window, "hyg_close")
        spx_ret = lookback_return(window, "spx_close")
        spx_high = max(row["spx_close"] for row in window)
        current = {
            "date": latest["date"],
            "hyg_close": latest["hyg_close"],
            "spx_close": latest["spx_close"],
            "hyg_ret": hyg_ret,
            "spx_ret": spx_ret,
            "regime": classify_regime(spx_ret, hyg_ret),
            "near_high": is_near_high(latest["spx_close"], spx_high),
        }
    return {
        "scan_time": stamp,
        "source": source,
        # R-190: the collapsed `source` string cannot say WHICH leg fell back,
        # so a mixed IB+Yahoo ratio was stored indistinguishably from the
        # other mixed pairing. The per-ticker map rides alongside it.
        "source_by_ticker": dict(source_by_ticker or {}),
        "count": len(series),
        "current": current,
        "series": series,
    }


# ── persistence ───────────────────────────────────────────────────

def _write_json_cache(payload: dict[str, Any]) -> None:
    CREDIT_SPREAD_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = CREDIT_SPREAD_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, CREDIT_SPREAD_JSON)


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
        print(f"[{SERVICE}] error heartbeat non-fatal: {exc}", file=sys.stderr)


def persist_result(
    payload: dict[str, Any],
    rows_changed_rows: list[dict[str, Any]],
    health_error: Optional[dict[str, Any]] = None,
) -> None:
    """Dual-write: Turso rows + snapshot + heartbeat, then the JSON fallback.

    Refuses an empty series entirely, but still heartbeats an error so the
    refusal is visible. Snapshot + heartbeat run EVERY cycle so the staleness
    banner notices a silent writer; the heartbeat is an error when no source
    confirmed the series (stale_source).
    """
    if not payload["series"]:
        print(f"[{SERVICE}] refusing to persist empty series", file=sys.stderr)
        _record_error_health(
            f"{SERVICE}: refusing to persist an empty series", "empty_series"
        )
        return

    scan_time = payload["scan_time"]
    writer.ensure_no_replica_for_writers()
    if rows_changed_rows:
        writer.upsert_credit_spread_rows(rows_changed_rows, recorded_at=scan_time)
    writer.upsert_scan_snapshot(SERVICE, scan_time, payload)
    writer.record_service_health(
        SERVICE,
        "ok" if health_error is None else "error",
        finished_at=scan_time,
        error=health_error,
    )
    _write_json_cache(payload)


# ── orchestration ─────────────────────────────────────────────────

def _turso_series() -> list[dict[str, Any]]:
    """The durable series from `credit_spread_history` (Turso-first read).

    R-123: this table was WRITE-ONLY — no SELECT anywhere in the repo — and
    the fetcher rehydrated from host-local JSON alone. Losing
    `data/credit_spread.json` (a host rebuild, or a failed `persist_result`
    before `_write_json_cache`) made the next run treat IB's 1-year window as
    the WHOLE series and republish a 1-year snapshot where an 18-year one
    stood, while the 2007+ rows sat unreadable in Turso.
    """
    from db.client import get_db

    rows = get_db().execute(
        "SELECT date, hyg_close, spx_close FROM credit_spread_history ORDER BY date"
    ).fetchall()
    return [
        {"date": row[0], "hyg_close": float(row[1]), "spx_close": float(row[2])}
        for row in rows
    ]


def _json_series() -> list[dict[str, Any]]:
    try:
        return json.loads(CREDIT_SPREAD_JSON.read_text())["series"]
    except (OSError, ValueError, KeyError):
        return []


def _read_cached_series() -> list[dict[str, Any]]:
    try:
        stored = _turso_series()
        if stored:
            return stored
    except Exception as exc:  # noqa: BLE001 — the JSON fallback still works
        print(f"[{SERVICE}] turso rehydrate non-fatal: {exc}", file=sys.stderr)
    return _json_series()


def _serve_cached(cached: list[dict[str, Any]]) -> dict[str, Any]:
    """IB, UW, Robinhood and Yahoo all down: re-serve the cache as stale_source, page.

    R-098: `source="none"` used to live only in payload["source"], which
    nothing consumed; the row heartbeated ok off the cached series and the
    panel rendered it as current.
    """
    if not cached:
        raise RuntimeError(
            f"{SERVICE}: IB, UW, Robinhood and Yahoo all failed with no cached series"
        )
    payload = {**build_output(cached, source=NO_SOURCE), "status": STATUS_STALE_SOURCE}
    through = payload["current"]["date"]
    print(
        f"[{SERVICE}] all sources down; re-serving cached series through {through}",
        file=sys.stderr,
    )
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
    print(f"[{SERVICE}] fetching HYG and SPX (IB → UW → RH → Yahoo)", file=sys.stderr)
    try:
        closes, sources = fetch_closes()
        cached = _read_cached_series()
        source = combine_source(sources) or NO_SOURCE
        if source == NO_SOURCE:
            return _serve_cached(cached)
        hyg = closes.get(HYG_SYMBOL, {})
        spx = closes.get(SPX_SYMBOL, {})

        fresh = build_series(align_series(hyg, spx))
        series = merge_series(cached, fresh)
        new_rows = diff_new_rows(cached, series)

        if not new_rows:
            print(f"[{SERVICE}] source unchanged; refreshing snapshot only", file=sys.stderr)

        payload = build_output(series, source=source, source_by_ticker=sources)
        persist_result(payload, new_rows)
    except Exception as exc:
        _record_error_health(f"{SERVICE}: {exc}", "cycle_failed")
        raise
    return payload


# ── CLI ───────────────────────────────────────────────────────────

def _print_summary(payload: dict[str, Any]) -> None:
    current = payload["current"]
    print(f"\nCREDIT — {payload['count']} sessions", file=sys.stderr)
    if current:
        print(f"  latest {current['date']}", file=sys.stderr)
        print(f"  regime {current['regime']}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="HYG vs S&P 500 credit-equity series")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    args = parser.parse_args()

    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_summary(payload)


if __name__ == "__main__":
    main()

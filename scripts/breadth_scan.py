#!/usr/bin/env python3
"""NYSE Market-Breadth (Advance/Decline) Scanner.

Tracks the NYSE advance/decline line: daily net advancers-minus-decliners
(AD-NYSE), the cumulative A/D line, the intraday 5-min net A/D tape, and the
TICK-NYSE snapshot. Divergence between SPY and the cumulative A/D line over
the trailing 20 sessions flags narrowing (bearish) or broadening (bullish)
participation.

Data sources:
  1. Daily net A/D + SPY history: StockCharts quotebrain ($NYAD, SPY) — public
     JSON, COMPLETE daily series. Primary because IB's AD-NYSE daily is gappy and
     lags a session or two. IB daily bars are the fallback.
  2. Live current net A/D + TICK: Interactive Brokers snapshot — AD-NYSE
     publishes advancers in the bid, decliners in the ask (net = bid - ask);
     TICK-NYSE via reqMktData. StockCharts $NYAD quote is the live fallback.
  3. data/breadth.json cache — served (source: "cache") when everything above is
     unavailable or returns nothing.

IB serves AD-NYSE DAILY bars (gappy, and lagging the tape by a session or two)
but NO intraday history — reqHistoricalData returns Error 162 "HMDS query
returned no data" for every intraday bar size. The current session's net A/D is
therefore sampled from a LIVE snapshot: IB publishes advancing issues in the
bid and declining issues in the ask of AD-NYSE (bid + ask ~= total NYSE issues),
so live net A/D = bid - ask. Those live readings accumulate into the session
tape across scans (see accumulate_session_tape). A total fetch failure serves
the existing cache and exits 0.

Usage:
    python3 scripts/breadth_scan.py --json           # JSON to stdout
    python3 scripts/breadth_scan.py --json --force   # bypass off-hours cache gate
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

from clients.ib_client import DEFAULT_HOST
from utils.atomic_io import atomic_save
from utils.ib_preflight import (
    IB_REQUEST_TIMEOUT_S,
    ib_auth_state as _ib_auth_state,
)
from utils.market_calendar import is_market_open_et, most_recent_session_date

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover - DB layer optional in stripped envs
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

# ── constants ─────────────────────────────────────────────────────
_CACHE_PATH = _PROJECT_DIR / "data" / "breadth.json"

DIVERGENCE_WINDOW_SESSIONS = 20
DIVERGENCE_SPY_THRESHOLD_PCT = 1.0

# Scanner-range pools disjoint from cri_scan's 50-61.
BREADTH_IB_HISTORY_CLIENT_IDS = (62, 63, 64)
BREADTH_IB_QUOTE_CLIENT_IDS = (65, 66)

# IB serves NO intraday HMDS history for AD-NYSE (Error 162), so the current
# session's net-A/D tape is accumulated from live snapshots — one point per
# bucket, updated in place while a bucket is current.
INTRADAY_BUCKET_MINUTES = 1
_ET = zoneinfo.ZoneInfo("America/New_York")


# ══════════════════════════════════════════════════════════════════
# Data Fetching (IB only)
# ══════════════════════════════════════════════════════════════════

def _finite_value(value: Any) -> Optional[float]:
    """Return a finite float, else None. Breadth values are SIGNED —
    negative net A/D and TICK readings are valid, so no positivity check."""
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def _connect_ib_with_retry(
    ib: Any,
    client_ids: Tuple[int, ...],
    ports: Tuple[int, ...] = (4001, 7497),
    timeout: int = 8,
) -> bool:
    """Connect to IB by cycling a small client-id pool before giving up."""
    for client_id in client_ids:
        for port in ports:
            try:
                ib.connect(DEFAULT_HOST, port, clientId=client_id, timeout=timeout)
                return True
            except Exception as exc:
                print(
                    f"  IB connect failed on port {port} clientId {client_id}: {exc}",
                    file=sys.stderr,
                )
    return False


def _fetch_ib_bars() -> Tuple[List[Any], List[Any], List[Any]]:
    """Fetch (AD-NYSE 1Y daily, SPY 1Y daily, AD-NYSE latest-session 5-min)
    bars from IB concurrently. Each request degrades independently to []."""
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        print(
            f"  IB skipped — gateway auth_state={auth_state} (serving cache)",
            file=sys.stderr,
        )
        return [], [], []

    try:
        from ib_insync import IB, Index, Stock
    except ImportError:
        return [], [], []

    ib = IB()
    if not _connect_ib_with_retry(ib, BREADTH_IB_HISTORY_CLIENT_IDS):
        return [], [], []

    import asyncio

    results: Dict[str, List[Any]] = {"ad_daily": [], "spy_daily": [], "ad_intraday": []}

    async def _qualify_and_fetch(key: str, contract: Any, duration: str, bar_size: str) -> None:
        try:
            await asyncio.wait_for(
                ib.qualifyContractsAsync(contract),
                timeout=IB_REQUEST_TIMEOUT_S,
            )
            bars = await asyncio.wait_for(
                ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                ),
                timeout=IB_REQUEST_TIMEOUT_S,
            )
            if bars:
                results[key] = list(bars)
                print(f"  IB: {key} — {len(bars)} bars", file=sys.stderr)
            else:
                print(f"  IB: {key} — no bars returned", file=sys.stderr)
        except asyncio.TimeoutError:
            print(
                f"  IB: {key} timed out after {IB_REQUEST_TIMEOUT_S}s — degrading",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"  IB: {key} failed — {exc}", file=sys.stderr)

    async def _fetch_all_concurrent() -> None:
        await asyncio.gather(
            _qualify_and_fetch("ad_daily", Index("AD-NYSE", "NYSE"), "1 Y", "1 day"),
            _qualify_and_fetch("spy_daily", Stock("SPY", "SMART", "USD"), "1 Y", "1 day"),
            _qualify_and_fetch("ad_intraday", Index("AD-NYSE", "NYSE"), "1 D", "5 mins"),
        )

    try:
        ib.run(_fetch_all_concurrent())
    finally:
        ib.disconnect()

    return results["ad_daily"], results["spy_daily"], results["ad_intraday"]


def _extract_ib_quote_value(ticker: Any) -> Optional[float]:
    """Return an actionable current quote (signed values valid for TICK)."""
    last = _finite_value(getattr(ticker, "last", None))
    if last is not None:
        return last

    bid = _finite_value(getattr(ticker, "bid", None))
    ask = _finite_value(getattr(ticker, "ask", None))
    if bid is not None and ask is not None:
        return (bid + ask) / 2.0

    for candidate in (bid, ask):
        if candidate is not None:
            return candidate
    return None


def _extract_ad_net(ticker: Any) -> Optional[float]:
    """Live NYSE net advance/decline from an AD-NYSE snapshot.

    IB publishes advancing issues in the bid and declining issues in the ask
    for this generated index (bid + ask ~= total NYSE issues); the live net is
    ``bid - ask``. ``last`` is NaN for these calculated indices, and there is no
    intraday HMDS history, so this snapshot is the ONLY source of the current
    session's net A/D."""
    bid = _finite_value(getattr(ticker, "bid", None))
    ask = _finite_value(getattr(ticker, "ask", None))
    if bid is None or ask is None:
        return None
    return bid - ask


def _snapshot_value(ib: Any, contract: Any, extract: Any) -> Optional[float]:
    """Live snapshot of an IB index, trying live -> frozen -> delayed feeds.
    snapshot=True MUST pass "" genericTickList (feedback_ib_snapshot_no_generic_ticks)."""
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        return None
    contract = qualified[0]
    for data_type in (1, 3, 4):
        try:
            ib.reqMarketDataType(data_type)
            snapshot = ib.reqMktData(contract, "", True, False)
            ib.sleep(2)
            value = extract(snapshot)
            ib.cancelMktData(contract)
            if value is not None:
                return value
        except Exception:
            continue
    return None


def _fetch_live_snapshots() -> Tuple[Optional[float], Optional[float]]:
    """Fetch the current AD-NYSE net (bid - ask) and TICK-NYSE readings from IB
    over a single connection. AD-NYSE has no intraday history (Error 162), so
    this live snapshot is how the current session's net A/D is sampled."""
    auth_state = _ib_auth_state()
    if auth_state and auth_state != "authenticated":
        return None, None

    try:
        from ib_insync import IB, Index
    except ImportError:
        return None, None

    ib = IB()
    if not _connect_ib_with_retry(ib, BREADTH_IB_QUOTE_CLIENT_IDS):
        return None, None

    try:
        ad_net = _snapshot_value(ib, Index("AD-NYSE", "NYSE"), _extract_ad_net)
        tick = _snapshot_value(ib, Index("TICK-NYSE", "NYSE"), _extract_ib_quote_value)
        return ad_net, tick
    finally:
        ib.disconnect()


# ══════════════════════════════════════════════════════════════════
# StockCharts daily A/D (IB's AD-NYSE daily is gappy + lags a session or two)
# ══════════════════════════════════════════════════════════════════

# StockCharts' ACP quotebrain endpoints are public JSON (no auth). $NYAD carries
# the COMPLETE daily NYSE net advance/decline series (advancers minus decliners)
# that IB's gappy AD-NYSE daily bars miss. SPY is fetched the same way so the two
# series align by date.
_STOCKCHARTS_QUOTEBRAIN = "https://stockcharts.com/quotebrain"
_STOCKCHARTS_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
_STOCKCHARTS_LOOKBACK_DAYS = 400  # > 1Y of sessions for the history chart's All range


def _stockcharts_get(path: str, params: Dict[str, str], timeout: int = 15) -> Any:
    """GET a public StockCharts quotebrain JSON endpoint."""
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    url = f"{_STOCKCHARTS_QUOTEBRAIN}/{path}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": _STOCKCHARTS_UA, "Accept-Encoding": "gzip"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip

            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def parse_stockcharts_daily(payload: Any) -> List[Tuple[str, float]]:
    """Completed daily (date, close) bars from a StockCharts historyandquote
    payload. Each bar's close is that session's value — net A/D for $NYAD, price
    for SPY. Only ``SQL_DAILY`` (completed) intervals are used; the trailing
    in-progress ``OTHER`` interval is dropped so today's partial value never
    enters the daily/cumulative series (the live tape carries that instead)."""
    intervals = ((payload or {}).get("history") or {}).get("intervals") or []
    series: List[Tuple[str, float]] = []
    for interval in intervals:
        if not isinstance(interval, dict) or interval.get("source") != "SQL_DAILY":
            continue
        close = _finite_value(interval.get("close"))
        end_time = str((interval.get("end") or {}).get("time") or "")
        date = end_time.split(" ")[0]
        if close is None or len(date) != 10:
            continue
        series.append((date, close))
    return series


def _fetch_stockcharts_daily(symbol: str) -> List[Tuple[str, float]]:
    """~1Y of completed daily bars for a StockCharts symbol ($NYAD net A/D, SPY
    price). Returns [] on any failure so the caller can fall back to IB."""
    now_et = datetime.now(_ET)
    start = (now_et - timedelta(days=_STOCKCHARTS_LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = (now_et + timedelta(days=1)).strftime("%Y%m%d")
    try:
        payload = _stockcharts_get(
            "historyandquote/d", {"symbol": symbol, "start": start, "end": end}
        )
        series = parse_stockcharts_daily(payload)
        print(f"  StockCharts: {symbol} daily — {len(series)} sessions", file=sys.stderr)
        return series
    except Exception as exc:
        print(f"  StockCharts: {symbol} daily fetch failed — {exc}", file=sys.stderr)
        return []


def _fetch_stockcharts_quote(symbol: str) -> Optional[float]:
    """Current value for a StockCharts symbol — the live-net-A/D fallback when
    the IB snapshot is unavailable."""
    try:
        payload = _stockcharts_get("quote", {"symbol": symbol, "format": "json"})
        if isinstance(payload, list) and payload:
            return _finite_value(payload[0].get("close"))
    except Exception as exc:
        print(f"  StockCharts: {symbol} quote fetch failed — {exc}", file=sys.stderr)
    return None


# ══════════════════════════════════════════════════════════════════
# Pure Computation
# ══════════════════════════════════════════════════════════════════

def build_daily_net_ad(bars: List[Any]) -> List[Tuple[str, float]]:
    """Daily net advance/decline series from AD-NYSE daily bars — each bar's
    close IS that session's advancers minus decliners."""
    series: List[Tuple[str, float]] = []
    for bar in bars:
        close = _finite_value(getattr(bar, "close", None))
        if close is None:
            continue
        series.append((str(bar.date), close))
    return series


def build_cumulative_ad(daily: List[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Cumulative A/D line — running sum of the daily net A/D series."""
    cumulative: List[Tuple[str, float]] = []
    total = 0.0
    for date, net_ad in daily:
        total += net_ad
        cumulative.append((date, total))
    return cumulative


def count_sessions_positive(
    daily: List[Tuple[str, float]], n: int = DIVERGENCE_WINDOW_SESSIONS
) -> Optional[int]:
    """Of the last ``n`` sessions, how many closed with net A/D > 0.
    None when fewer than ``n`` sessions are available."""
    if len(daily) < n:
        return None
    return sum(1 for _, net_ad in daily[-n:] if net_ad > 0)


def compute_divergence(
    spy_change_20d_pct: Optional[float],
    cum_ad_change_20d: Optional[float],
) -> Optional[str]:
    """Breadth divergence over the last 20 sessions (user-facing semantics):
    spy_change_20d_pct > 1 AND cum_ad_change_20d < 0 -> "bearish";
    spy_change_20d_pct < -1 AND cum_ad_change_20d > 0 -> "bullish";
    else "none"; null when inputs missing."""
    if spy_change_20d_pct is None or cum_ad_change_20d is None:
        return None
    if spy_change_20d_pct > DIVERGENCE_SPY_THRESHOLD_PCT and cum_ad_change_20d < 0:
        return "bearish"
    if spy_change_20d_pct < -DIVERGENCE_SPY_THRESHOLD_PCT and cum_ad_change_20d > 0:
        return "bullish"
    return "none"


def _session_date_from_intraday(intraday: List[Tuple[str, float]]) -> str:
    """ET session date of the latest intraday bar, falling back to the most
    recent expected session when the timestamp does not parse."""
    try:
        raw = intraday[-1][0].replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        import zoneinfo
        return dt.astimezone(zoneinfo.ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return most_recent_session_date()


def _resolve_session_date(
    ad_daily: List[Tuple[str, float]], intraday: List[Tuple[str, float]]
) -> str:
    if intraday:
        return _session_date_from_intraday(intraday)
    if ad_daily:
        return ad_daily[-1][0]
    return most_recent_session_date()


def _prior_session_net_ad(
    ad_daily: List[Tuple[str, float]], session_date: str
) -> Optional[float]:
    if not ad_daily:
        return None
    if ad_daily[-1][0] != session_date:
        return ad_daily[-1][1]
    if len(ad_daily) >= 2:
        return ad_daily[-2][1]
    return None


def _change_over_window(values: List[float], n: int) -> Optional[float]:
    if len(values) < n + 1:
        return None
    return values[-1] - values[-(n + 1)]


def _pct_change_over_window(values: List[float], n: int) -> Optional[float]:
    if len(values) < n + 1 or values[-(n + 1)] <= 0:
        return None
    return (values[-1] / values[-(n + 1)] - 1) * 100


def build_output(
    ad_daily: List[Tuple[str, float]],
    spy_daily: List[Tuple[str, float]],
    intraday: List[Tuple[str, float]],
    tick: Optional[float],
    market_open: bool,
    source: str,
    scan_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the breadth contract payload (see web /api/breadth consumer)."""
    cumulative = build_cumulative_ad(ad_daily)
    spy_lookup = {date: close for date, close in spy_daily}

    session_date = _resolve_session_date(ad_daily, intraday)
    if intraday:
        latest_net_ad: Optional[float] = intraday[-1][1]
    elif ad_daily:
        latest_net_ad = ad_daily[-1][1]
    else:
        latest_net_ad = None

    cum_ad = cumulative[-1][1] if cumulative else None
    cum_ad_change_20d = _change_over_window(
        [value for _, value in cumulative], DIVERGENCE_WINDOW_SESSIONS
    )
    spy_change_20d_pct = _pct_change_over_window(
        [close for _, close in spy_daily], DIVERGENCE_WINDOW_SESSIONS
    )

    history = [
        {
            "date": date,
            "net_ad": round(net_ad, 2),
            "cum_ad": round(cum, 2),
            "spy_close": round(spy_lookup[date], 2) if date in spy_lookup else None,
        }
        for (date, net_ad), (_, cum) in zip(ad_daily, cumulative)
    ]

    return {
        "scan_time": scan_time or datetime.now(timezone.utc).isoformat(),
        "market_open": market_open,
        "source": source,
        "latest": {
            "session_date": session_date,
            "net_ad": round(latest_net_ad, 2) if latest_net_ad is not None else None,
            "net_ad_prev": (
                round(_prior_session_net_ad(ad_daily, session_date), 2)
                if _prior_session_net_ad(ad_daily, session_date) is not None
                else None
            ),
            "tick": round(tick, 2) if tick is not None else None,
            "cum_ad": round(cum_ad, 2) if cum_ad is not None else None,
            "cum_ad_change_20d": (
                round(cum_ad_change_20d, 2) if cum_ad_change_20d is not None else None
            ),
            "spy_change_20d_pct": (
                round(spy_change_20d_pct, 2) if spy_change_20d_pct is not None else None
            ),
            "sessions_positive_20": count_sessions_positive(ad_daily),
            "divergence": compute_divergence(spy_change_20d_pct, cum_ad_change_20d),
        },
        "history": history,
        "intraday": [
            {"time": time, "net_ad": round(net_ad, 2)} for time, net_ad in intraday
        ],
    }


# ══════════════════════════════════════════════════════════════════
# Persistence
# ══════════════════════════════════════════════════════════════════

def persist_result(result: Dict[str, Any], cache_path: Optional[Path] = None) -> bool:
    """Cache to disk + mirror to Turso ONLY when the payload carries data —
    an empty overwrite would clobber the last good scan that the cache
    fallback path serves."""
    cache_path = cache_path or _CACHE_PATH
    if not (result.get("history") or result.get("intraday")):
        print(
            "  Empty breadth payload — skipping cache write + Turso mirror",
            file=sys.stderr,
        )
        return False
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save(str(cache_path), result)
    mirror_scan_snapshot("breadth-scan", result)
    return True


def carry_forward_history(
    result: Dict[str, Any], cached: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """A degraded scan (fresh intraday, no daily bars) must not clobber the
    cached daily history the cumulative A/D stats derive from — rebuild the
    payload with the cached history carried forward."""
    if result.get("history") or not isinstance(cached, dict):
        return result
    cached_history = cached.get("history") or []
    if not cached_history:
        return result
    ad_daily = [
        (row["date"], float(row["net_ad"]))
        for row in cached_history
        if isinstance(row, dict) and row.get("net_ad") is not None
    ]
    spy_daily = [
        (row["date"], float(row["spy_close"]))
        for row in cached_history
        if isinstance(row, dict) and row.get("spy_close") is not None
    ]
    if not ad_daily:
        return result
    intraday = [
        (point["time"], float(point["net_ad"]))
        for point in result.get("intraday") or []
    ]
    tick = (result.get("latest") or {}).get("tick")
    print(
        f"  Daily history unavailable this scan — carrying {len(ad_daily)} "
        "cached sessions forward",
        file=sys.stderr,
    )
    return build_output(
        ad_daily=ad_daily,
        spy_daily=spy_daily,
        intraday=intraday,
        tick=tick,
        market_open=bool(result.get("market_open")),
        source=result.get("source") or "ib",
        scan_time=result.get("scan_time"),
    )


def _et_date(dt: datetime) -> str:
    """ET session date (YYYY-MM-DD) of an aware/naive UTC datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_ET).strftime("%Y-%m-%d")


def _bucket_iso(dt: datetime, minutes: int = INTRADAY_BUCKET_MINUTES) -> str:
    """Floor a UTC datetime to the bucket boundary and return ISO-8601 UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    floored = dt.replace(minute=(dt.minute // minutes) * minutes, second=0, microsecond=0)
    return floored.isoformat()


def accumulate_session_tape(
    cached: Optional[Dict[str, Any]],
    live_net_ad: Optional[float],
    now: datetime,
    minutes: int = INTRADAY_BUCKET_MINUTES,
) -> List[Tuple[str, float]]:
    """Build the current session's intraday net-A/D tape from live snapshots.

    IB serves no intraday history for AD-NYSE (HMDS Error 162), so the SESSION
    NET A/D tape is accumulated across scans: carry forward the cached points
    from today's ET session, then append the latest live reading (or update it
    in place while its bucket is still current). Points from prior sessions are
    dropped so a new session starts clean."""
    session_date = _et_date(now)
    tape: List[Tuple[str, float]] = []
    for point in (cached or {}).get("intraday") or []:
        if not isinstance(point, dict) or point.get("net_ad") is None:
            continue
        raw = str(point.get("time", ""))
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if _et_date(dt) == session_date:
            tape.append((raw, float(point["net_ad"])))

    if live_net_ad is None:
        return tape

    bucket = _bucket_iso(now, minutes)
    if tape and tape[-1][0] == bucket:
        tape[-1] = (bucket, live_net_ad)
    else:
        tape.append((bucket, live_net_ad))
    return tape


def _read_cached_payload() -> Optional[Dict[str, Any]]:
    try:
        cached = json.loads(_CACHE_PATH.read_text())
    except (OSError, ValueError):
        return None
    return cached if isinstance(cached, dict) else None


def _load_cache_fallback(market_open: bool) -> Dict[str, Any]:
    """Serve the last good disk cache (source: "cache"); when none exists,
    emit the contract's missing shape so consumers never see 4xx/garbage."""
    try:
        cached = json.loads(_CACHE_PATH.read_text())
    except (OSError, ValueError):
        cached = None
    if isinstance(cached, dict) and (cached.get("history") or cached.get("intraday")):
        cached["source"] = "cache"
        cached["market_open"] = market_open
        return cached
    return {
        "missing": True,
        "scan_time": None,
        "market_open": market_open,
        "source": "cache",
        "latest": None,
        "history": [],
        "intraday": [],
    }


# ══════════════════════════════════════════════════════════════════
# Console Summary
# ══════════════════════════════════════════════════════════════════

def print_summary(result: Dict[str, Any]) -> None:
    latest = result.get("latest") or {}
    print(f"\n{'='*60}", file=sys.stderr)
    print(
        f"NYSE BREADTH SCAN — {latest.get('session_date', 'n/a')} "
        f"(source: {result.get('source')})",
        file=sys.stderr,
    )
    print(f"{'='*60}", file=sys.stderr)
    print(f"  Net A/D        : {latest.get('net_ad')}", file=sys.stderr)
    print(f"  Prev net A/D   : {latest.get('net_ad_prev')}", file=sys.stderr)
    print(f"  TICK           : {latest.get('tick')}", file=sys.stderr)
    print(f"  Cum A/D        : {latest.get('cum_ad')}", file=sys.stderr)
    print(f"  Cum A/D 20d    : {latest.get('cum_ad_change_20d')}", file=sys.stderr)
    print(f"  SPY 20d %      : {latest.get('spy_change_20d_pct')}", file=sys.stderr)
    print(f"  Positive of 20 : {latest.get('sessions_positive_20')}", file=sys.stderr)
    print(f"  Divergence     : {latest.get('divergence')}", file=sys.stderr)
    print(
        f"  History: {len(result.get('history') or [])} sessions | "
        f"Intraday: {len(result.get('intraday') or [])} bars",
        file=sys.stderr,
    )
    print(f"{'='*60}\n", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def _iso_utc(raw: Any) -> str:
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return str(raw)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NYSE Market-Breadth (Advance/Decline) Scanner",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fetch fresh even when the market is closed (bypass off-hours cache gate)",
    )
    args = parser.parse_args()

    market_open = is_market_open_et()

    print(f"\n{'='*60}", file=sys.stderr)
    print("BREADTH SCANNER — NYSE Advance/Decline", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    if not market_open:
        print("  Market closed — using last available data.", file=sys.stderr)

    # Off-hours cache gate: when closed and the cached scan already covers the
    # most-recent session, serve it and skip the IB fetch entirely.
    if args.json and not args.force:
        try:
            from utils.scan_cache_gate import cached_scan_if_fresh

            cached = cached_scan_if_fresh(_CACHE_PATH, force=False)
        except Exception:
            cached = None
        if cached is not None:
            print(
                "  Serving cached breadth (market closed); skipping IB fetch.",
                file=sys.stderr,
            )
            print(json.dumps(cached, indent=2))
            return

    ad_net_live, tick = _fetch_live_snapshots()

    # Daily history: StockCharts is primary. IB's AD-NYSE daily is gappy and lags
    # by a session or two, so $NYAD (complete daily net A/D) drives the cumulative
    # line + 20-day stats; SPY comes from the same source so dates align. IB daily
    # is the fallback if StockCharts is unreachable.
    ad_daily = _fetch_stockcharts_daily("$NYAD")
    spy_daily = _fetch_stockcharts_daily("SPY")
    daily_source = "stockcharts"
    ad_intraday_bars: List[Any] = []
    if not ad_daily:
        print("  StockCharts AD daily unavailable — falling back to IB.", file=sys.stderr)
        ad_daily_bars, spy_daily_bars, ad_intraday_bars = _fetch_ib_bars()
        ad_daily = build_daily_net_ad(ad_daily_bars)
        if not spy_daily:
            spy_daily = build_daily_net_ad(spy_daily_bars)
        daily_source = "ib"

    # Live current net A/D: IB bid-ask snapshot (real-time); StockCharts quote
    # fallback when IB is unavailable.
    if ad_net_live is None:
        ad_net_live = _fetch_stockcharts_quote("$NYAD")
    if ad_net_live is not None:
        print(f"  Live AD-NYSE net {ad_net_live:+.0f}", file=sys.stderr)
    if tick is not None:
        print(f"  IB: TICK-NYSE current {tick:.0f}", file=sys.stderr)

    cached_payload = _read_cached_payload()
    historical_intraday = [
        (_iso_utc(bar.date), close)
        for bar in ad_intraday_bars
        for close in [_finite_value(getattr(bar, "close", None))]
        if close is not None
    ]
    # IB has no intraday history for AD-NYSE, so the session tape is built from
    # live snapshots (only while the market is open). If IB ever starts serving
    # intraday bars, those take precedence.
    if historical_intraday:
        intraday = historical_intraday
    else:
        intraday = accumulate_session_tape(
            cached_payload,
            ad_net_live if market_open else None,
            datetime.now(timezone.utc),
        )

    if not ad_daily and not intraday:
        print("  No breadth data — falling back to cache.", file=sys.stderr)
        result = _load_cache_fallback(market_open)
        print_summary(result)
        print(json.dumps(result, indent=2))
        return

    result = build_output(
        ad_daily=ad_daily,
        spy_daily=spy_daily,
        intraday=intraday,
        tick=tick,
        market_open=market_open,
        source=daily_source,
    )
    result = carry_forward_history(result, cached_payload)
    persist_result(result)
    print_summary(result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

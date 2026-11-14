"""Provisional current-session values for Cboe indexes.

Daily-close indicators (VIX model, VIX TS, Panic) read official Cboe CSVs
that post after the session. During the session they publish a provisional
row built from one of these sources, never a mix: IB pool daily bars first
(today's partial bar), then Cboe 15-minute delayed quotes. Every symbol must
resolve on the same session date or the whole read is None. Callers treat
None as "no live row" and never fail their official run on it.
"""
from __future__ import annotations

import math
import os
import sys
import threading
from datetime import datetime
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

LIVE_QUOTE_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_{symbol}.json"
POOL_BARS_URL = "http://127.0.0.1:8321/historical/bars"
USER_AGENT = "radon/2.0"
_ET = ZoneInfo("America/New_York")
_THREAD_HTTP = threading.local()


def _http_session():
    """One requests.Session per thread so sequential Cboe GETs and IB POSTs
    reuse a keep-alive socket (PERF-055). Session is not thread-safe."""
    import requests

    session = getattr(_THREAD_HTTP, "session", None)
    if session is None:
        session = requests.Session()
        _THREAD_HTTP.session = session
    return session


def close_http_session() -> None:
    session = getattr(_THREAD_HTTP, "session", None)
    if session is None:
        return
    try:
        session.close()
    finally:
        _THREAD_HTTP.session = None


def et_today(now: datetime) -> str:
    return now.astimezone(_ET).date().isoformat()


def live_service(service: str) -> str:
    """Scan-snapshot key for an indicator's provisional row."""
    return f"{service}:live"


def _positive(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _iso_date(value: Any) -> Optional[str]:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def pool_daily_bars(symbol: str, duration: str) -> Any:
    """Daily index bars through the FastAPI IB pool; never opens a new IB socket."""
    headers = {"User-Agent": USER_AGENT}
    if os.environ.get("MDW_API_KEY"):
        headers["X-API-Key"] = os.environ["MDW_API_KEY"]
    response = _http_session().post(
        POOL_BARS_URL,
        headers=headers,
        json={
            "contract": {"sec_type": "IND", "symbol": symbol, "exchange": "CBOE", "currency": "USD"},
            "duration": duration,
            "bar_size": "1 day",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("bars")


def live_from_ib(symbols: Iterable[str], now: datetime) -> Optional[dict[str, Any]]:
    """Today's partial daily bar for every symbol, or None."""
    today = et_today(now)
    values: dict[str, float] = {}
    for symbol in symbols:
        bars = pool_daily_bars(symbol, "1 D")
        last = bars[-1] if isinstance(bars, list) and bars and isinstance(bars[-1], dict) else {}
        value = _positive(last.get("close"))
        if _iso_date(last.get("date")) != today or value is None:
            return None
        values[symbol] = value
    return {"date": today, "values": values, "source": "ib", "quote_time": None}


def live_from_cboe(symbols: Iterable[str]) -> Optional[dict[str, Any]]:
    """Cboe 15-minute delayed quotes for every symbol, all on one session, or None."""
    values: dict[str, float] = {}
    dates: set[str] = set()
    times: list[str] = []
    session = _http_session()
    for symbol in symbols:
        response = session.get(
            LIVE_QUOTE_URL.format(symbol=symbol), headers={"User-Agent": USER_AGENT}, timeout=15
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        stamp = data.get("last_trade_time")
        value = _positive(data.get("current_price"))
        day = _iso_date(stamp)
        if value is None or day is None:
            return None
        values[symbol] = value
        dates.add(day)
        times.append(str(stamp))
    if len(dates) != 1:
        return None
    return {"date": dates.pop(), "values": values, "source": "cboe_delayed", "quote_time": max(times)}


def fetch_live(
    symbols: Iterable[str],
    now: datetime,
    *,
    try_ib: bool,
    label: str,
    ib: Callable[[Iterable[str], datetime], Optional[dict[str, Any]]] = None,  # type: ignore[assignment]
    cboe: Callable[[Iterable[str]], Optional[dict[str, Any]]] = None,  # type: ignore[assignment]
) -> Optional[dict[str, Any]]:
    """IB first when the caller's gate allows it, then Cboe. Advisory: errors return None."""
    symbols = tuple(symbols)
    ib = ib or live_from_ib
    cboe = cboe or live_from_cboe
    if try_ib:
        try:
            live = ib(symbols, now)
            if live:
                return live
        except Exception as exc:  # noqa: BLE001 - fall through to Cboe
            print(f"[{label}] IB live bars unavailable: {type(exc).__name__}", file=sys.stderr)
    try:
        return cboe(symbols)
    except Exception as exc:  # noqa: BLE001 - live row is advisory
        print(f"[{label}] Cboe delayed quotes unavailable: {type(exc).__name__}", file=sys.stderr)
        return None


def ib_allowed() -> bool:
    """Skip the IB pool only when /health names a non-authenticated state."""
    try:
        from utils.ib_preflight import ib_auth_state

        state = ib_auth_state()
    except Exception:  # noqa: BLE001 - unknown state still tries IB
        return True
    return not state or state == "authenticated"


def is_newer(live: Optional[dict[str, Any]], official_date: Optional[str], now: datetime) -> bool:
    """A live read is usable only for a session after the official close and not in the future."""
    return bool(live and official_date and official_date < live["date"] <= et_today(now))


def snapshot_is_old(scan_time: Any, now: datetime, max_age_hours: float = 12.0) -> bool:
    try:
        stamp = datetime.fromisoformat(str(scan_time).replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        return True
    return (now - stamp).total_seconds() > max_age_hours * 3600

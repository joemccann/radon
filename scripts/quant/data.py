"""Daily close matrix for the quant engine, Turso-first.

``price_history_daily`` (migration 0029) is the source of truth. A symbol the
store cannot serve (empty, thin, or stale) is fetched through the mandated
ladder, IB first with a duration sized to the requested start, then
Robinhood, UW and, last, Yahoo, and the rows are written back so the next run
is a pure read. Only IB serves multi-year history; the fallback rungs serve
about a year and are good enough to keep a sleeve current, not to backtest.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

#: Fewer rows than this, or a latest close older than ``STALE_DAYS``, triggers
#: the backfill ladder for that symbol.
MIN_SESSIONS = 252
STALE_DAYS = 10
IB_HISTORY_TIMEOUT_S = 60.0
IB_MAX_YEARS = 20


@dataclass
class PriceMatrix:
    dates: list[str]
    symbols: list[str]
    closes: np.ndarray
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def n_sessions(self) -> int:
        return len(self.dates)


def align_closes(
    series: Mapping[str, Mapping[str, float]],
    symbols: Sequence[str],
    *,
    since: Optional[str] = None,
) -> PriceMatrix:
    """Union-of-dates matrix, forward-filled within each symbol after its first
    observation, NaN before it. A symbol with no data is an all-NaN column so
    the strategy's universe keeps its shape.
    """
    names = [str(s) for s in symbols]
    dates = sorted({d for s in names for d in (series.get(s) or {}) if since is None or d >= since})
    index = {d: i for i, d in enumerate(dates)}
    closes = np.full((len(dates), len(names)), np.nan)
    for j, sym in enumerate(names):
        for d, value in (series.get(sym) or {}).items():
            i = index.get(d)
            if i is None:
                continue
            try:
                v = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v) and v > 0:
                closes[i, j] = v
        last = np.nan
        for i in range(len(dates)):
            if np.isnan(closes[i, j]):
                closes[i, j] = last
            else:
                last = closes[i, j]
    return PriceMatrix(dates=dates, symbols=names, closes=closes)


def _needs_backfill(closes: Optional[Mapping[str, float]], today: date) -> bool:
    if not closes or len(closes) < MIN_SESSIONS:
        return True
    latest = date.fromisoformat(max(closes))
    return (today - latest).days > STALE_DAYS


def _ib_duration(since: str, today: date) -> str:
    years = math.ceil((today - date.fromisoformat(since)).days / 365.25) + 1
    return f"{max(1, min(years, IB_MAX_YEARS))} Y"


def _fetch_ib_closes_batch(symbols: Sequence[str], duration: str) -> Dict[str, Dict[str, float]]:
    """One IB connection for every symbol; bounded awaits inside the client."""
    try:
        from portfolio_risk import _ib_reachable
    except ImportError:  # pragma: no cover - sibling module always present in-tree
        return {}
    if not _ib_reachable():
        return {}
    try:
        from clients.ib_client import IBClient
        from ib_insync import Stock
    except ImportError:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    client = IBClient()
    try:
        client.connect(client_id="auto", timeout=10)
        for symbol in symbols:
            try:
                bars = client.get_historical_data(
                    Stock(symbol, "SMART", "USD"),
                    duration=duration,
                    bar_size="1 day",
                    what_to_show="TRADES",
                    use_rth=True,
                    timeout=IB_HISTORY_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001 - the ladder falls through
                print(f"  IB daily closes failed for {symbol}: {exc}", file=sys.stderr)
                continue
            closes = {str(bar.date)[:10]: float(bar.close) for bar in bars or [] if float(bar.close) > 0}
            if closes:
                out[symbol] = closes
    except Exception as exc:  # noqa: BLE001
        print(f"  IB connect failed: {exc}", file=sys.stderr)
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return out


def backfill_closes(symbols: Sequence[str], *, since: str, today: Optional[date] = None) -> Dict[str, Dict[str, float]]:
    """IB (deep) -> Robinhood -> UW -> Yahoo per symbol, persisted to Turso."""
    today = today or datetime.now(timezone.utc).date()
    fetched: Dict[str, Dict[str, float]] = {}
    sources: Dict[str, str] = {}
    for symbol, closes in _fetch_ib_closes_batch(symbols, _ib_duration(since, today)).items():
        fetched[symbol] = closes
        sources[symbol] = "ib"
    try:
        from portfolio_risk import _fetch_rh_closes, _fetch_uw_closes, _fetch_yahoo_closes
    except ImportError:  # pragma: no cover
        _fetch_rh_closes = _fetch_uw_closes = _fetch_yahoo_closes = lambda _s: {}  # type: ignore[assignment]
    for symbol in symbols:
        if symbol in fetched:
            continue
        for tag, fetch in (("rh", _fetch_rh_closes), ("uw", _fetch_uw_closes), ("yahoo", _fetch_yahoo_closes)):
            closes = fetch(symbol)
            if closes:
                fetched[symbol] = closes
                sources[symbol] = tag
                break
    if fetched:
        try:
            from db.writer import ensure_no_replica_for_writers, upsert_price_history_rows

            ensure_no_replica_for_writers()
            for symbol, closes in fetched.items():
                upsert_price_history_rows(
                    symbol,
                    [{"date": d, "close": c, "source": sources[symbol]} for d, c in sorted(closes.items())],
                )
        except Exception as exc:  # noqa: BLE001 - a failed persist still returns the data
            print(f"  price history persist failed: {exc}", file=sys.stderr)
    for symbol in symbols:
        print(f"  {symbol}: {len(fetched.get(symbol, {}))} closes via {sources.get(symbol, 'none')}", file=sys.stderr)
    return fetched


def load_price_matrix(
    symbols: Sequence[str],
    *,
    since: str,
    db: Any = None,
    allow_backfill: bool = True,
    today: Optional[date] = None,
) -> PriceMatrix:
    """Turso closes for ``symbols`` from ``since``; thin or stale symbols are
    backfilled through the ladder when ``allow_backfill`` is set."""
    from db.readers import read_price_history_closes

    today = today or datetime.now(timezone.utc).date()
    series: Dict[str, Dict[str, float]] = dict(read_price_history_closes(symbols, since=since, db=db))
    sources = {s: "turso" for s in symbols if series.get(s)}
    missing = [s for s in symbols if _needs_backfill(series.get(s), today)]
    if missing and allow_backfill:
        print(f"backfilling {', '.join(missing)} from {since}", file=sys.stderr)
        for symbol, closes in backfill_closes(missing, since=since, today=today).items():
            merged = dict(series.get(symbol) or {})
            merged.update(closes)
            series[symbol] = merged
            sources[symbol] = "ladder"
    matrix = align_closes(series, symbols, since=since)
    matrix.sources = sources
    return matrix

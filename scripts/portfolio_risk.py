#!/usr/bin/env python3
"""Portfolio correlation risk-budget guard (Gate 3).

Builds a return-correlation matrix from durable daily close history for the
tickers currently held, groups highly-correlated names into clusters, and flags
a cluster when its aggregate book exposure exceeds a configurable budget derived
from the 2.5% per-position cap.

This is a GUARDRAIL, not an optimizer. It answers one question: "are several
positions secretly the same bet?" and surfaces the concentration so the operator
can decide. It never resizes or proposes trades.

Pure-function core (``build_correlation_matrix`` / ``build_risk_budget_report``)
takes in-memory price-series dicts so it is fully testable offline.

``load_price_series_for_portfolio`` sources those series Turso-first from
``price_history_daily`` (migration 0029, shared with the RV-ratio and BPI
scans). A held underlying the store cannot cover deeply or recently enough is
backfilled through the mandated IB -> UW -> Yahoo ladder and persisted back to
Turso, so the next run is a pure read. The legacy
``data/price_history_cache/`` request cache remains a last-ditch fallback only:
its sole writer (``portfolio_performance.py``) is no longer invoked by any
timer, which is why Gate 3 read "measurement unavailable" for every ticker.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from db.readers import read_latest_portfolio_snapshot, read_price_history_closes

# ── Constants ────────────────────────────────────────────────────────────────

# Per-position hard cap (Gate 3). The cluster budget defaults to this: a cluster
# of correlated names should not, in aggregate, carry more risk than a single
# position is allowed to.
PER_POSITION_CAP = 0.025

DEFAULT_CORR_THRESHOLD = 0.70
DEFAULT_MIN_OVERLAP = 5          # minimum overlapping daily returns to trust a corr
DEFAULT_BOOK_BUDGET = PER_POSITION_CAP

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CACHE_DIR = _DATA_DIR / "price_history_cache"
_STOCKS_DIR = _CACHE_DIR / "stocks"

# Depth target per underlying. The report needs DEFAULT_MIN_OVERLAP overlapping
# returns (6 dated closes); 30 clears that with room for holidays and for two
# names whose session calendars do not line up.
MIN_CLOSES_TARGET = 30

# Trailing window read from Turso. ~124 sessions: deep enough for the target,
# short enough that one read stays small over Hrana.
HISTORY_WINDOW_DAYS = 180

# A series whose newest close is older than this is not a current correlation
# read, however deep it is.
STALE_TOLERANCE_DAYS = 10

# ib_sync calls this loader every minute during RTH (radon-portfolio-sync.timer).
# Both bounds exist so a symbol no source can serve cannot fire the fetch ladder
# 390x/day: at most this many symbols per invocation, and never the same symbol
# twice inside the retry window. The marker is a rate-limiter, not data — losing
# it on a deploy costs one extra attempt.
BACKFILL_MAX_SYMBOLS_PER_RUN = 4
BACKFILL_RETRY_S = 6 * 3600
_BACKFILL_MARKER_PATH = _DATA_DIR / "price_risk_backfill.json"

IB_HISTORY_DURATION = "1 Y"
IB_HISTORY_TIMEOUT_S = 20


# ── Position → underlying mapping ────────────────────────────────────────────


def position_underlying(position: dict) -> Optional[str]:
    """Return the underlying ticker for a position (options map to the underlying).

    Reads defensively: JSON portfolio rows use ``ticker``; IB contract dicts use
    ``symbol``.
    """
    return position.get("ticker") or position.get("symbol")


# ── Correlation matrix ───────────────────────────────────────────────────────


def _aligned_returns(
    series_a: Dict[str, float],
    series_b: Dict[str, float],
) -> tuple:
    """Return (returns_a, returns_b) computed over the shared, date-sorted dates."""
    shared = sorted(set(series_a) & set(series_b))
    if len(shared) < 2:
        return [], []

    returns_a: List[float] = []
    returns_b: List[float] = []
    for prev, cur in zip(shared, shared[1:]):
        pa, ca = series_a[prev], series_a[cur]
        pb, cb = series_b[prev], series_b[cur]
        if pa <= 0 or ca <= 0 or pb <= 0 or cb <= 0:
            continue
        returns_a.append(math.log(ca / pa))
        returns_b.append(math.log(cb / pb))
    return returns_a, returns_b


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    corr = cov / math.sqrt(var_x * var_y)
    return max(-1.0, min(1.0, corr))


def correlation(
    series_a: Dict[str, float],
    series_b: Dict[str, float],
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> Optional[float]:
    """Pearson correlation of daily log-returns, or None on insufficient overlap."""
    returns_a, returns_b = _aligned_returns(series_a, series_b)
    if len(returns_a) < min_overlap:
        return None
    return _pearson(returns_a, returns_b)


def build_correlation_matrix(
    series_by_ticker: Dict[str, Dict[str, float]],
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> Dict[str, Dict[str, Optional[float]]]:
    """Symmetric correlation matrix. Diagonal is 1.0; thin pairs are None."""
    tickers = sorted(series_by_ticker)
    matrix: Dict[str, Dict[str, Optional[float]]] = {
        t: {u: None for u in tickers} for t in tickers
    }
    for t in tickers:
        matrix[t][t] = 1.0
    for i, t in enumerate(tickers):
        for u in tickers[i + 1:]:
            corr = correlation(
                series_by_ticker[t], series_by_ticker[u], min_overlap=min_overlap
            )
            matrix[t][u] = corr
            matrix[u][t] = corr
    return matrix


# ── Position weights ─────────────────────────────────────────────────────────


def _position_weight_dollars(position: dict) -> float:
    """Dollar exposure used as the position's risk weight.

    Defined-risk option positions weight by ``max_risk`` (capital truly at risk);
    everything else weights by absolute market value, falling back to entry cost.
    """
    risk_profile = str(position.get("risk_profile", "")).lower()
    if risk_profile == "defined":
        max_risk = position.get("max_risk")
        if max_risk:
            return abs(float(max_risk))
    for field in ("market_value", "entry_cost", "max_risk"):
        value = position.get(field)
        if value:
            return abs(float(value))
    return 0.0


def _weights_by_underlying(portfolio: dict) -> Dict[str, float]:
    """Aggregate dollar weight per underlying ticker across all positions."""
    weights: Dict[str, float] = {}
    for position in portfolio.get("positions") or []:
        ticker = position_underlying(position)
        if not ticker:
            continue
        weights[ticker] = weights.get(ticker, 0.0) + _position_weight_dollars(position)
    return weights


# ── Clustering ───────────────────────────────────────────────────────────────


def _correlated_clusters(
    tickers: Sequence[str],
    matrix: Dict[str, Dict[str, Optional[float]]],
    corr_threshold: float,
) -> List[List[str]]:
    """Group tickers connected by corr >= threshold (transitive union-find)."""
    parent = {t: t for t in tickers}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for i, t in enumerate(tickers):
        for u in tickers[i + 1:]:
            corr = matrix.get(t, {}).get(u)
            if corr is not None and corr >= corr_threshold:
                union(t, u)

    groups: Dict[str, List[str]] = {}
    for t in tickers:
        groups.setdefault(find(t), []).append(t)

    return [sorted(members) for members in groups.values() if len(members) > 1]


# ── Report ───────────────────────────────────────────────────────────────────


def build_risk_budget_report(
    portfolio: dict,
    series_by_ticker: Dict[str, Dict[str, float]],
    corr_threshold: float = DEFAULT_CORR_THRESHOLD,
    book_budget: float = DEFAULT_BOOK_BUDGET,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> dict:
    """Build the structured correlation risk-budget report.

    Returns ``{clusters, breaches, aggregate_exposure, insufficient_data,
    corr_threshold, book_budget}`` where every exposure is a fraction of bankroll.
    """
    bankroll = float(portfolio.get("bankroll") or 0.0)
    weights = _weights_by_underlying(portfolio)

    aggregate_exposure = (
        sum(weights.values()) / bankroll if bankroll > 0 else 0.0
    )

    measurable, insufficient = _split_by_data_sufficiency(
        weights, series_by_ticker, min_overlap
    )

    if not measurable or bankroll <= 0:
        return _empty_report(aggregate_exposure, insufficient, corr_threshold, book_budget)

    matrix = build_correlation_matrix(
        {t: series_by_ticker[t] for t in measurable}, min_overlap=min_overlap
    )
    cluster_members = _correlated_clusters(measurable, matrix, corr_threshold)

    clusters = [
        _cluster_report(members, weights, bankroll, matrix, book_budget)
        for members in cluster_members
    ]
    breaches = [c for c in clusters if c["breached"]]

    return {
        "clusters": clusters,
        "breaches": breaches,
        "aggregate_exposure": aggregate_exposure,
        "insufficient_data": insufficient,
        "corr_threshold": corr_threshold,
        "book_budget": book_budget,
    }


def _split_by_data_sufficiency(
    weights: Dict[str, float],
    series_by_ticker: Dict[str, Dict[str, float]],
    min_overlap: int,
) -> tuple:
    """Partition held tickers into (measurable, insufficient_data)."""
    measurable: List[str] = []
    insufficient: List[str] = []
    for ticker in sorted(weights):
        series = series_by_ticker.get(ticker)
        if series and _usable_return_count(series) >= min_overlap:
            measurable.append(ticker)
        else:
            insufficient.append(ticker)
    return measurable, insufficient


def _usable_return_count(series: Dict[str, float]) -> int:
    dates = sorted(series)
    count = 0
    for prev, cur in zip(dates, dates[1:]):
        if series[prev] > 0 and series[cur] > 0:
            count += 1
    return count


def _cluster_report(
    members: List[str],
    weights: Dict[str, float],
    bankroll: float,
    matrix: Dict[str, Dict[str, Optional[float]]],
    book_budget: float,
) -> dict:
    aggregate = sum(weights.get(t, 0.0) for t in members) / bankroll
    return {
        "tickers": members,
        "aggregate_exposure": aggregate,
        "budget": book_budget,
        "breached": aggregate > book_budget,
        "max_pair_corr": _max_pair_corr(members, matrix),
        "per_ticker_exposure": {
            t: weights.get(t, 0.0) / bankroll for t in members
        },
    }


def _max_pair_corr(
    members: Sequence[str],
    matrix: Dict[str, Dict[str, Optional[float]]],
) -> Optional[float]:
    corrs = [
        matrix[t][u]
        for i, t in enumerate(members)
        for u in members[i + 1:]
        if matrix.get(t, {}).get(u) is not None
    ]
    return max(corrs) if corrs else None


def _empty_report(
    aggregate_exposure: float,
    insufficient: List[str],
    corr_threshold: float,
    book_budget: float,
) -> dict:
    return {
        "clusters": [],
        "breaches": [],
        "aggregate_exposure": aggregate_exposure,
        "insufficient_data": insufficient,
        "corr_threshold": corr_threshold,
        "book_budget": book_budget,
    }


# ── Price source: Turso first, IB -> UW -> Yahoo backfill, disk last ─────────


def held_underlyings(portfolio: dict) -> List[str]:
    """Sorted, de-duplicated underlyings across the book."""
    tickers = {
        position_underlying(p)
        for p in portfolio.get("positions") or []
        if position_underlying(p)
    }
    return sorted(t for t in tickers if t)


def load_price_series_for_portfolio(
    portfolio: dict,
    stocks_dir: Path = _STOCKS_DIR,
    db: Optional[Any] = None,
    allow_backfill: bool = True,
) -> Dict[str, Dict[str, float]]:
    """Daily close series per held underlying, deep enough to measure Gate 3.

    Turso ``price_history_daily`` is the source of truth. Underlyings it cannot
    cover (too thin, or stale beyond ``STALE_TOLERANCE_DAYS``) go through the
    bounded IB -> UW -> Yahoo ladder and are written back to Turso. Anything
    still missing falls back to the legacy on-disk request cache.
    """
    wanted = held_underlyings(portfolio)
    if not wanted:
        return {}

    since = (
        datetime.now(timezone.utc) - timedelta(days=HISTORY_WINDOW_DAYS)
    ).date().isoformat()
    series = read_price_history_closes(wanted, since=since, db=db)

    unusable = [t for t in wanted if not _has_target_depth(series.get(t))]
    if unusable and allow_backfill:
        for ticker, closes in backfill_price_history(unusable).items():
            series[ticker] = closes
        unusable = [t for t in wanted if not _has_target_depth(series.get(t))]

    if unusable:
        for ticker, closes in _load_disk_cache_series(unusable, stocks_dir).items():
            if not _has_target_depth(series.get(ticker)):
                series[ticker] = closes

    return {t: s for t, s in series.items() if s}


def _has_target_depth(series: Optional[Dict[str, float]]) -> bool:
    """Deep enough AND recent enough to be a current correlation input."""
    if not series or _usable_return_count(series) + 1 < MIN_CLOSES_TARGET:
        return False
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=STALE_TOLERANCE_DAYS)
    ).date().isoformat()
    return max(series) >= cutoff


# ── Backfill ladder (IB -> UW -> Yahoo, persisted to Turso) ──────────────────


def backfill_price_history(symbols: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """Fetch and persist daily closes for symbols Turso cannot serve.

    Bounded by ``BACKFILL_MAX_SYMBOLS_PER_RUN`` and the per-symbol retry
    marker; a run that fetches nothing returns ``{}`` and the caller reports
    those tickers as insufficient rather than guessing.
    """
    due = _due_for_backfill(symbols)[:BACKFILL_MAX_SYMBOLS_PER_RUN]
    if not due:
        return {}
    _record_backfill_attempt(due)

    fetched: Dict[str, Dict[str, float]] = {}
    for symbol in due:
        closes, source = _fetch_closes_via_ladder(symbol)
        if not closes:
            print(f"  no daily closes for {symbol} from IB/UW/Yahoo", file=sys.stderr)
            continue
        _persist_closes(symbol, closes, source)
        fetched[symbol] = closes
    return fetched


def _fetch_closes_via_ladder(symbol: str) -> tuple:
    """Data Source Priority: IB every cycle, then UW, then Yahoo."""
    if _ib_reachable():
        closes = _fetch_ib_closes(symbol)
        if closes:
            return closes, "ib"
    closes = _fetch_uw_closes(symbol)
    if closes:
        return closes, "uw"
    return _fetch_yahoo_closes(symbol), "yahoo"


def _ib_reachable() -> bool:
    """Skip the IB socket only when /health reports a non-authenticated
    gateway; an unreachable /health proceeds optimistically."""
    try:
        from utils.ib_preflight import ib_auth_state
    except ImportError:  # pragma: no cover - utils always present in-tree
        return True
    state = ib_auth_state()
    return state is None or state == "authenticated"


def _fetch_ib_closes(symbol: str) -> Dict[str, float]:
    """Trailing-year daily TRADES closes from IB Gateway."""
    try:
        from clients.ib_client import IBClient
        from ib_insync import Stock
    except ImportError:
        return {}

    client = IBClient()
    try:
        client.connect(client_id="auto", timeout=10)
        bars = client.get_historical_data(
            Stock(symbol, "SMART", "USD"),
            duration=IB_HISTORY_DURATION,
            bar_size="1 day",
            what_to_show="TRADES",
            use_rth=True,
            timeout=IB_HISTORY_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - the ladder falls through to UW
        print(f"  IB daily closes failed for {symbol}: {exc}", file=sys.stderr)
        return {}
    finally:
        try:
            client.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return {
        str(bar.date)[:10]: float(bar.close)
        for bar in bars or []
        if bar.close and bar.close > 0
    }


def _fetch_uw_closes(symbol: str) -> Dict[str, float]:
    try:
        from clients.uw_client import UWClient
    except ImportError:
        return {}
    try:
        with UWClient() as uw:
            data = uw.get_stock_ohlc(symbol, candle_size="1d")
    except Exception as exc:  # noqa: BLE001 - the ladder falls through to Yahoo
        print(f"  UW daily closes failed for {symbol}: {exc}", file=sys.stderr)
        return {}
    return {
        str(bar["date"])[:10]: float(bar["close"])
        for bar in (data or {}).get("data") or []
        if bar.get("date") and bar.get("close")
    }


def _fetch_yahoo_closes(symbol: str) -> Dict[str, float]:
    """ABSOLUTE LAST RESORT — reached only after IB and UW return nothing.

    Reads ``indicators.quote[0].close`` (split-adjusted, dividend-UNadjusted),
    matching the ``price_history_daily`` contract.
    """
    from urllib.request import Request, urlopen

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={int(start.timestamp())}&period2={int(end.timestamp())}&interval=1d"
    )
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as resp:
            result = json.load(resp)["chart"]["result"][0]
    except Exception as exc:  # noqa: BLE001 - measurement stays "insufficient"
        print(f"  Yahoo daily closes failed for {symbol}: {exc}", file=sys.stderr)
        return {}
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    return {
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"): float(close)
        for ts, close in zip(result.get("timestamp") or [], quote.get("close") or [])
        if close
    }


def _persist_closes(symbol: str, closes: Dict[str, float], source: str) -> None:
    """Write the series to Turso, completed sessions only.

    A bar dated after the last completed ET session is an in-progress price,
    never a daily close; storing one would poison the shared store.
    """
    from db.writer import upsert_price_history_rows
    from utils.market_calendar import last_completed_session_date

    latest = last_completed_session_date()
    rows = [
        {"date": d, "close": close, "source": source}
        for d, close in sorted(closes.items())
        if close > 0 and d <= latest
    ]
    if rows:
        upsert_price_history_rows(symbol, rows)


# ── Backfill throttle ────────────────────────────────────────────────────────


def _due_for_backfill(symbols: Sequence[str]) -> List[str]:
    attempts = _read_backfill_marker()
    now = datetime.now(timezone.utc)
    due = []
    for symbol in symbols:
        last = _parse_iso(attempts.get(symbol))
        if last is None or (now - last).total_seconds() >= BACKFILL_RETRY_S:
            due.append(symbol)
    return due


def _record_backfill_attempt(symbols: Sequence[str]) -> None:
    attempts = _read_backfill_marker()
    stamp = datetime.now(timezone.utc).isoformat()
    attempts.update({symbol: stamp for symbol in symbols})
    try:
        _BACKFILL_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BACKFILL_MARKER_PATH.write_text(json.dumps(attempts))
    except OSError as exc:
        print(f"  backfill marker not written: {exc}", file=sys.stderr)


def _read_backfill_marker() -> Dict[str, str]:
    try:
        loaded = json.loads(_BACKFILL_MARKER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── Legacy disk cache (fallback only) ────────────────────────────────────────


def _load_disk_cache_series(
    wanted: Sequence[str],
    stocks_dir: Path = _STOCKS_DIR,
) -> Dict[str, Dict[str, float]]:
    """Last-ditch read of the generated ``data/price_history_cache/`` records.

    Cache filenames are SHA-256 hashes, so we read each file and key it off the
    embedded ``key`` ("TICKER|start|end|v1"). The freshest file per ticker wins.
    """
    wanted = set(wanted)
    if not wanted or not stocks_dir.exists():
        return {}

    freshest: Dict[str, tuple] = {}
    for path in stocks_dir.glob("*.json"):
        record = _read_cache_record(path)
        if not record:
            continue
        ticker = str(record.get("key", "")).split("|", 1)[0]
        if ticker not in wanted:
            continue
        fetched_at = str(record.get("fetched_at", ""))
        if ticker not in freshest or fetched_at > freshest[ticker][0]:
            freshest[ticker] = (fetched_at, record.get("data") or {})

    return {ticker: data for ticker, (_, data) in freshest.items() if data}


def _read_cache_record(path: Path) -> Optional[dict]:
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def run() -> dict:
    """Build the report from the latest Turso portfolio snapshot + close store."""
    portfolio = read_latest_portfolio_snapshot() or {"positions": []}
    series = load_price_series_for_portfolio(portfolio)
    return build_risk_budget_report(portfolio, series)


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, indent=2))

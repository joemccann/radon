#!/usr/bin/env python3
"""Portfolio correlation risk-budget guard (Gate 3).

Builds a return-correlation matrix from cached daily price history for the
tickers currently held, groups highly-correlated names into clusters, and flags
a cluster when its aggregate book exposure exceeds a configurable budget derived
from the 2.5% per-position cap.

This is a GUARDRAIL, not an optimizer. It answers one question: "are several
positions secretly the same bet?" and surfaces the concentration so the operator
can decide. It never resizes or proposes trades.

Pure-function core (``build_correlation_matrix`` / ``build_risk_budget_report``)
takes in-memory price-series dicts so it is fully testable offline. The
``load_price_series_for_portfolio`` / ``run`` helpers wire positions from Turso
to the generated ``data/price_history_cache/`` cache; they are the only
disk-touching paths and are never exercised by the unit tests.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from db.readers import read_latest_portfolio_snapshot

# ── Constants ────────────────────────────────────────────────────────────────

# Per-position hard cap (Gate 3). The cluster budget defaults to this: a cluster
# of correlated names should not, in aggregate, carry more risk than a single
# position is allowed to.
PER_POSITION_CAP = 0.025

DEFAULT_CORR_THRESHOLD = 0.70
DEFAULT_MIN_OVERLAP = 5          # minimum overlapping daily returns to trust a corr
DEFAULT_BOOK_BUDGET = PER_POSITION_CAP

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "price_history_cache"
_STOCKS_DIR = _CACHE_DIR / "stocks"


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


# ── Disk wiring (never touched by unit tests) ────────────────────────────────


def load_price_series_for_portfolio(
    portfolio: dict,
    stocks_dir: Path = _STOCKS_DIR,
) -> Dict[str, Dict[str, float]]:
    """Load the most recent cached stock price series for each held underlying.

    Cache filenames are SHA-256 hashes, so we read each file and key it off the
    embedded ``key`` ("TICKER|start|end|v1"). The freshest file per ticker wins.
    """
    wanted = {
        position_underlying(p)
        for p in portfolio.get("positions") or []
        if position_underlying(p)
    }
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
    """Build the report from the latest Turso portfolio snapshot + price cache."""
    portfolio = read_latest_portfolio_snapshot() or {"positions": []}
    series = load_price_series_for_portfolio(portfolio)
    return build_risk_budget_report(portfolio, series)


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, indent=2))

"""DISPERSION indicator — pure math, no network, no numpy.

VIX close, the 95th-minus-5th percentile spread of daily single-stock
returns across the S&P 500 seed, and the same spread across the 11 Select
Sector SPDRs. Each is rolled to a trailing 60-session mean and z-scored over
the full sample since 2017. Spec: docs/indicators/dispersion.md section C.

WINDOW / ZSCORE_BASE_START / MIN_STOCKS / MIN_SECTORS / MIN_SERIES_ROWS are
read from module globals at call time so tests can monkeypatch them.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

WINDOW = 60
ZSCORE_BASE_START = "2017-01-01"
MIN_STOCKS = 300
MIN_SECTORS = 9
PCT_HIGH = 95.0
PCT_LOW = 5.0
SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC")
VIX_SYMBOL = "VIX"

STOCK_SPREAD_MIN, STOCK_SPREAD_MAX = 0.005, 0.60
SECTOR_SPREAD_MIN, SECTOR_SPREAD_MAX = 0.001, 0.30
VIX_MIN, VIX_MAX = 5.0, 100.0
MIN_SERIES_ROWS = 400

STRESS_Z = 1.0
COMPRESSED_Z = -1.0

REGIME_BROAD_STRESS = "BROAD STRESS"
REGIME_BELOW_SURFACE = "BELOW THE SURFACE"
REGIME_COMPRESSED = "COMPRESSED"
REGIME_NORMAL = "NORMAL"

Closes = dict[str, float]

_Z_DP = 4
_SPREAD_DP = 6
_VIX_DP = 6


# ── calendar + returns (C.2) ──────────────────────────────────────

def master_sessions(closes: dict[str, Closes]) -> list[str]:
    """The VIX trades every US equity session, so its dates are the calendar."""
    return sorted(closes.get(VIX_SYMBOL) or {})


def daily_returns(closes: dict[str, Closes], sessions: list[str]) -> dict[str, dict[str, float]]:
    """{session: {symbol: close(t) / close(master-previous) - 1}}; no forward fill."""
    return {
        session: _returns_between(closes, previous, session)
        for previous, session in zip(sessions, sessions[1:])
    }


def _returns_between(closes: dict[str, Closes], previous: str, session: str) -> dict[str, float]:
    return {
        symbol: series[session] / series[previous] - 1.0
        for symbol, series in closes.items()
        if session in series and previous in series
    }


# ── cross-sectional spread (C.3) ──────────────────────────────────

def cross_sectional_spread(values: list[float]) -> float:
    """95th minus 5th percentile, linear interpolation (numpy default)."""
    if not values:
        raise ValueError("cross_sectional_spread: empty cross-section")
    ordered = sorted(values)
    return _percentile(ordered, PCT_HIGH) - _percentile(ordered, PCT_LOW)


def _percentile(ordered: list[float], pct: float) -> float:
    position = (len(ordered) - 1) * pct / 100.0
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def build_raw_rows(
    closes: dict[str, Closes], stock_symbols: list[str], sector_symbols: list[str]
) -> list[dict[str, Any]]:
    """One raw row per master session whose VIX, stock and sector legs all exist."""
    sessions = master_sessions(closes)
    returns = daily_returns(closes, sessions)
    vix = closes.get(VIX_SYMBOL) or {}
    rows = (
        _raw_row(session, vix[session], returns[session], stock_symbols, sector_symbols)
        for session in sessions[1:]
    )
    return [row for row in rows if row is not None]


def _raw_row(
    session: str,
    vix_close: float,
    returns: dict[str, float],
    stock_symbols: list[str],
    sector_symbols: list[str],
) -> Optional[dict[str, Any]]:
    stocks = [returns[s] for s in stock_symbols if s in returns]
    sectors = [returns[s] for s in sector_symbols if s in returns]
    if len(stocks) < MIN_STOCKS or len(sectors) < MIN_SECTORS:
        return None
    return {
        "date": session,
        "vix_close": vix_close,
        "stock_spread": cross_sectional_spread(stocks),
        "sector_spread": cross_sectional_spread(sectors),
        "n_stocks": len(stocks),
        "n_sectors": len(sectors),
    }


# ── rolling mean (C.4) + z-score (C.5) ────────────────────────────

def rolling_mean(values: list[float], window: int) -> list[Optional[float]]:
    """Trailing mean ending at each index; None until a full window exists."""
    return [
        statistics.fmean(values[i - window + 1 : i + 1]) if i + 1 >= window else None
        for i in range(len(values))
    ]


_METRIC_FIELDS = (("m60_vix", "vix_close"), ("m60_stock", "stock_spread"), ("m60_sector", "sector_spread"))
_Z_FIELDS = (("z_vix", "m60_vix"), ("z_stock", "m60_stock"), ("z_sector", "m60_sector"))


def zscore_series(rows: list[dict[str, Any]], base_start: str) -> list[dict[str, Any]]:
    """Ascending, unrounded points from base_start on: z, raw values, 60-session means."""
    windowed = _windowed_rows(sorted(rows, key=lambda r: r["date"]))
    base = [row for row in windowed if row["date"] >= base_start]
    scales = {z_field: _zscore_scale([row[m_field] for row in base]) for z_field, m_field in _Z_FIELDS}
    return [_z_point(row, scales) for row in base]


def _windowed_rows(ordered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    means = {
        m_field: rolling_mean([float(row[raw_field]) for row in ordered], WINDOW)
        for m_field, raw_field in _METRIC_FIELDS
    }
    return [
        {**row, **{m_field: means[m_field][i] for m_field, _ in _METRIC_FIELDS}}
        for i, row in enumerate(ordered)
        if means["m60_vix"][i] is not None
    ]


def _zscore_scale(base: list[float]) -> tuple[float, float]:
    if len(base) < 2:
        raise ValueError(f"z-score base needs at least 2 points, got {len(base)}")
    mu = statistics.fmean(base)
    sigma = statistics.stdev(base)
    if sigma == 0:
        raise ValueError("z-score base has zero variance")
    return mu, sigma


def _z_point(row: dict[str, Any], scales: dict[str, tuple[float, float]]) -> dict[str, Any]:
    point = {
        "date": row["date"],
        "vix": row["vix_close"],
        "stock_spread": row["stock_spread"],
        "sector_spread": row["sector_spread"],
        "n_stocks": row["n_stocks"],
        "n_sectors": row["n_sectors"],
    }
    for m_field, _ in _METRIC_FIELDS:
        point[m_field] = row[m_field]
    for z_field, m_field in _Z_FIELDS:
        mu, sigma = scales[z_field]
        point[z_field] = (row[m_field] - mu) / sigma
    return point


# ── regime (C.6) ──────────────────────────────────────────────────

def classify_regime(z_vix: float, z_stock: float, z_sector: float) -> str:
    below = max(z_stock, z_sector)
    if z_vix >= STRESS_Z:
        return REGIME_BROAD_STRESS
    if below >= STRESS_Z:
        return REGIME_BELOW_SURFACE
    if z_vix <= COMPRESSED_Z and below <= COMPRESSED_Z:
        return REGIME_COMPRESSED
    return REGIME_NORMAL


def surface_gap(z_vix: float, z_stock: float, z_sector: float) -> float:
    return max(z_stock, z_sector) - z_vix


def _regime_of(point: dict[str, Any]) -> str:
    return classify_regime(point["z_vix"], point["z_stock"], point["z_sector"])


# ── stats (C.8) ───────────────────────────────────────────────────

def compute_stats(series: list[dict[str, Any]]) -> dict[str, Any]:
    below_dates = [p["date"] for p in series if _regime_of(p) == REGIME_BELOW_SURFACE]
    return {
        "base": {"start": series[0]["date"], "end": series[-1]["date"], "n": len(series)},
        "vix": _metric_stats(series, "m60_vix", "z_vix"),
        "stock": _metric_stats(series, "m60_stock", "z_stock"),
        "sector": _metric_stats(series, "m60_sector", "z_sector"),
        "days_below_surface": len(below_dates),
        "last_below_surface_date": below_dates[-1] if below_dates else None,
    }


def _metric_stats(series: list[dict[str, Any]], m_field: str, z_field: str) -> dict[str, Any]:
    means = [p[m_field] for p in series]
    zs = [p[z_field] for p in series]
    return {
        "mean_60d": statistics.fmean(means),
        "stdev_60d": statistics.stdev(means) if len(means) >= 2 else None,
        "z_min": min(zs),
        "z_max": max(zs),
    }


# ── plausibility guard ────────────────────────────────────────────

def ensure_plausible_rows(rows: list[dict[str, Any]], *, backfill: bool) -> None:
    """Raise ValueError on any row outside the C.1 bands; every row is checked."""
    if not rows:
        raise ValueError("dispersion: no rows to validate")
    if backfill and len(rows) < MIN_SERIES_ROWS:
        raise ValueError(f"dispersion: backfill produced {len(rows)} rows, below the {MIN_SERIES_ROWS} floor")
    for row in rows:
        _ensure_plausible_row(row)


def _ensure_plausible_row(row: dict[str, Any]) -> None:
    date = row["date"]
    _ensure_in_band("vix_close", row["vix_close"], VIX_MIN, VIX_MAX, date)
    _ensure_in_band("stock_spread", row["stock_spread"], STOCK_SPREAD_MIN, STOCK_SPREAD_MAX, date)
    _ensure_in_band("sector_spread", row["sector_spread"], SECTOR_SPREAD_MIN, SECTOR_SPREAD_MAX, date)
    if row["n_stocks"] < MIN_STOCKS:
        raise ValueError(f"dispersion: n_stocks {row['n_stocks']} below {MIN_STOCKS} on {date}")
    if row["n_sectors"] < MIN_SECTORS:
        raise ValueError(f"dispersion: n_sectors {row['n_sectors']} below {MIN_SECTORS} on {date}")


def _ensure_in_band(field: str, value: float, low: float, high: float, date: str) -> None:
    if not (low <= value <= high):
        raise ValueError(f"dispersion: {field} {value} outside [{low}, {high}] on {date}")


# ── payload (D) ───────────────────────────────────────────────────

def build_payload(
    rows: list[dict[str, Any]],
    *,
    scan_time: str,
    status: str,
    source: dict[str, str],
    universe: dict[str, Any],
    fetch: dict[str, Any],
) -> dict[str, Any]:
    """Full API contract, rebuilt from raw rows every run (nothing rolling is stored)."""
    series = zscore_series(rows, ZSCORE_BASE_START)
    return {
        "scan_time": scan_time,
        "status": status,
        "source": source,
        "data_date": series[-1]["date"],
        "universe": universe,
        "fetch": fetch,
        "count": len(series),
        "current": _current(series[-1]),
        "stats": _rounded_stats(compute_stats(series)),
        "series": [_series_point(point) for point in series],
    }


def _series_point(point: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": point["date"],
        "z_vix": round(point["z_vix"], _Z_DP),
        "z_stock": round(point["z_stock"], _Z_DP),
        "z_sector": round(point["z_sector"], _Z_DP),
        "vix": point["vix"],
        "stock_spread": round(point["stock_spread"], _SPREAD_DP),
        "sector_spread": round(point["sector_spread"], _SPREAD_DP),
    }


def _current(point: dict[str, Any]) -> dict[str, Any]:
    z_vix, z_stock, z_sector = (round(point[f], _Z_DP) for f in ("z_vix", "z_stock", "z_sector"))
    return {
        **_series_point(point),
        "m60_vix": round(point["m60_vix"], _VIX_DP),
        "m60_stock": round(point["m60_stock"], _SPREAD_DP),
        "m60_sector": round(point["m60_sector"], _SPREAD_DP),
        "n_stocks": point["n_stocks"],
        "n_sectors": point["n_sectors"],
        "regime": classify_regime(z_vix, z_stock, z_sector),
        "surface_gap": round(surface_gap(z_vix, z_stock, z_sector), _Z_DP),
    }


def _rounded_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        **stats,
        "vix": _rounded_metric(stats["vix"], _VIX_DP),
        "stock": _rounded_metric(stats["stock"], _SPREAD_DP),
        "sector": _rounded_metric(stats["sector"], _SPREAD_DP),
    }


def _rounded_metric(block: dict[str, Any], mean_dp: int) -> dict[str, Any]:
    stdev = block["stdev_60d"]
    return {
        "mean_60d": round(block["mean_60d"], mean_dp),
        "stdev_60d": None if stdev is None else round(stdev, mean_dp),
        "z_min": round(block["z_min"], _Z_DP),
        "z_max": round(block["z_max"], _Z_DP),
    }

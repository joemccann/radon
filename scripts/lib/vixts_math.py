"""VIX TS math — the VIX / VIX3M term-structure ratio, its regime bands and
its plausibility guard.

Pure functions only: no network, no database, no clock. Everything the
`vixts` indicator computes lives here so pytest covers it offline.

The ratio is the slope of the volatility term structure. Below 1.00 the curve
is in contango (near-term vol priced under 3-month vol); above 1.00 it is in
backwardation (stress in the front of the curve). This is a descriptive
regime read: no forward-return claim is made.

Spec: docs/indicators/vixts.md.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

# ── regime bands ──────────────────────────────────────────────────
# Boundaries are inclusive lower edges (see classify_regime). Frequencies
# over the full 2009-09-18.. history: backwardation ~7.6% of sessions, the
# 0.95 flat zone ~12%, and the sub-0.80 extreme under 2%.
BACKWARDATION_THRESHOLD = 1.00   # ratio >= this: front-month bid over 3-month
FLAT_THRESHOLD = 0.95            # [0.95, 1.00): curve flattening toward the flip
STEEP_CONTANGO_THRESHOLD = 0.80  # ratio < this: rare complacency extreme

# ── plausibility bounds ───────────────────────────────────────────
# There is no fallback data rung for this indicator, so these are the only
# protection against a silently truncated or corrupt Cboe file.
MIN_SERIES_ROWS = 2000   # plausibility floor; the real join is ~4,250 rows
RATIO_SANITY_MIN = 0.40  # any latest ratio outside [min, max] means corruption
RATIO_SANITY_MAX = 2.50

_RATIO_PRECISION = 4


def classify_regime(ratio: float) -> str:
    """Name the term-structure band a ratio falls in (inclusive lower edges)."""
    if ratio >= BACKWARDATION_THRESHOLD:
        return "BACKWARDATION"
    if ratio >= FLAT_THRESHOLD:
        return "FLAT"
    if ratio >= STEEP_CONTANGO_THRESHOLD:
        return "CONTANGO"
    return "STEEP CONTANGO"


def _by_date(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["date"]: row["value"] for row in rows}


def join_series(
    vix_rows: list[dict[str, Any]],
    vix3m_rows: list[dict[str, Any]],
    spx_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ascending ratio series over the dates both VIX legs share.

    VIX x VIX3M is an INNER join: the effective history floor is VIX3M's
    2009-09-18 start, and a date carried by only one file is never emitted.
    SPX is a LEFT join used only for the chart overlay, so a date the SPX
    file has not published yet still emits a row with ``spx: None``.

    A non-positive VIX3M close RAISES. It used to `continue`, which made the
    `bad_leg` guard in `ensure_plausible_series` unreachable and turned Cboe
    publishing zero closes for three days into a silent three-day hole that
    passed every guard and heartbeat `ok`. R-363.
    """
    vix_by_date = _by_date(vix_rows)
    vix3m_by_date = _by_date(vix3m_rows)
    spx_by_date = _by_date(spx_rows)

    series: list[dict[str, Any]] = []
    for date in sorted(set(vix_by_date) & set(vix3m_by_date)):
        vix3m = vix3m_by_date[date]
        if vix3m <= 0:
            raise ValueError(
                f"vixts row {date} carries a non-positive vix3m ({vix3m}); "
                "dropping it would leave a silent hole in the series"
            )
        vix = vix_by_date[date]
        series.append(
            {
                "date": date,
                "vix": vix,
                "vix3m": vix3m,
                "ratio": round(vix / vix3m, _RATIO_PRECISION),
                "spx": spx_by_date.get(date),
            }
        )
    return series


def build_current(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Latest-session card: the last row plus the regime its ratio names."""
    if not series:
        return None
    latest = series[-1]
    return {**latest, "regime": classify_regime(latest["ratio"])}


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Distribution of the ratio plus the backwardation tally, over the full series."""
    if not series:
        return None
    ratios = [row["ratio"] for row in series]
    crossings = [row["date"] for row in series if _is_backwardation(row["ratio"])]
    return {
        "min": min(ratios),
        "max": max(ratios),
        "mean": statistics.fmean(ratios),
        "median": statistics.median(ratios),
        "days_backwardation": len(crossings),
        "pct_backwardation": len(crossings) / len(series) * 100.0,
        "last_backwardation_date": crossings[-1] if crossings else None,
    }


def _is_backwardation(ratio: float) -> bool:
    return ratio >= BACKWARDATION_THRESHOLD


def ensure_plausible_series(series: list[dict[str, Any]]) -> None:
    """Raise ValueError when the built series cannot be a healthy Cboe pull.

    Raising keeps the run retryable and writes an error heartbeat. Latching
    service_health ``ok`` over an unverified series would hide a dead or
    truncated source, and this indicator has no second rung to fall back to.
    """
    if len(series) < MIN_SERIES_ROWS:
        raise ValueError(
            f"vixts series has {len(series)} rows; expected at least {MIN_SERIES_ROWS}"
        )
    bad_leg = next((row for row in series if row["vix3m"] <= 0), None)
    if bad_leg is not None:
        raise ValueError(
            f"vixts row {bad_leg['date']} carries a non-positive vix3m ({bad_leg['vix3m']})"
        )
    # EVERY row, not just `series[-1]`. One garbled VIX_History.csv row
    # parsing as CLOSE=999.0 anywhere in the ~4,250-row history inflated
    # `max`, `days_backwardation` and `pct_backwardation`, making the chart
    # axis and the panel's "percent of sessions" copy wrong with
    # service_health green. R-364.
    bad_ratio = next(
        (row for row in series
         if not RATIO_SANITY_MIN <= row["ratio"] <= RATIO_SANITY_MAX),
        None,
    )
    if bad_ratio is not None:
        raise ValueError(
            f"vixts ratio {bad_ratio['ratio']} on {bad_ratio['date']} is outside "
            f"the sane band [{RATIO_SANITY_MIN}, {RATIO_SANITY_MAX}]"
        )

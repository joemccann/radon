"""Panic Proxy math — equal-weight mean of 252-session z-scores.

Four Cboe legs only: VIX, VVIX, VIX/VIX3M, Cboe SKEW. Higher is more panic.
The headline series is the one-day change of that composite. UW 25d skew is
an overlay and is never folded into the level.

Pure functions only: no network, no database, no clock, no numpy. Spec:
docs/indicators/panic-index.md.
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

# ── named constants ───────────────────────────────────────────────
Z_WINDOW = 252            # sessions in the rolling z-score window, inclusive of t
RANK_WINDOW = 2520        # trailing sessions for the 10y rank and the Δ1d z
RANK_MIN_ROWS = 504       # Δ1d z / rank are None until this many Δ1d rows exist
Z_STD_FLOOR = 1e-9        # a window with no dispersion emits None, never inf

MIN_SERIES_ROWS = 3500    # plausibility floor; the real join is ~4,268
MAX_DROPPED_SHARE = 0.01  # inner join may drop at most 1% of VIX∩VIX3M dates
VIX_SANITY = (5.0, 150.0)
VVIX_SANITY = (40.0, 250.0)
TS_RATIO_SANITY = (0.40, 2.50)
SKEW_SANITY = (90.0, 220.0)

ALERT_SIGMA = 3.0         # Δ1d <= -ALERT_SIGMA * trailing-10y stdev fires the push
ALERT_TOP_N = 10          # rank <= this inside the trailing 10y escalates the copy

_Z_DP = 4
_DELTA_Z_DP = 2
_TS_DP = 4


def _round_or_none(value: Optional[float], digits: int) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _by_date(rows: list[dict[str, Any]]) -> dict[str, float]:
    return {row["date"]: row["value"] for row in rows}


def join_series(
    vix_rows: list[dict[str, Any]],
    vix3m_rows: list[dict[str, Any]],
    vvix_rows: list[dict[str, Any]],
    skew_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Four-way inner join. Returns (series, dropped_dates, base_count).

    ``base_count`` is ``len(dates(VIX) ∩ dates(VIX3M))``. A base date missing
    from VVIX or SKEW, or carrying a non-positive VIX3M, is dropped rather
    than emitted. Ascending by date.
    """
    vix_by_date = _by_date(vix_rows)
    vix3m_by_date = _by_date(vix3m_rows)
    vvix_by_date = _by_date(vvix_rows)
    skew_by_date = _by_date(skew_rows)

    base_dates = set(vix_by_date) & set(vix3m_by_date)
    dropped: list[str] = []
    series: list[dict[str, Any]] = []
    for date in sorted(base_dates):
        vix3m = vix3m_by_date[date]
        if vix3m <= 0 or date not in vvix_by_date or date not in skew_by_date:
            dropped.append(date)
            continue
        vix = vix_by_date[date]
        ts_raw = vix / vix3m
        series.append(
            {
                "date": date,
                "vix": vix,
                "vix3m": vix3m,
                "vvix": vvix_by_date[date],
                "ts": round(ts_raw, _TS_DP),
                # z_ts is scored on the unrounded ratio so the composite
                # reproduces the §C.8 anchors; `ts` stays the 4dp display value.
                "_ts_raw": ts_raw,
                "skew": skew_by_date[date],
                "z_vix": None,
                "z_vvix": None,
                "z_ts": None,
                "z_skew": None,
                "level": None,
                "delta_1d": None,
            }
        )
    return series, dropped, len(base_dates)


def rolling_z(values: list[Optional[float]]) -> list[Optional[float]]:
    """252-session sample z-score, inclusive of t. None until warm-up or a
    degenerate / incomplete window. Never inf.
    """
    out: list[Optional[float]] = [None] * len(values)
    for t in range(len(values)):
        if t < Z_WINDOW - 1:
            continue
        window = values[t - Z_WINDOW + 1 : t + 1]
        if any(v is None for v in window):
            continue
        filled = [float(v) for v in window]  # type: ignore[arg-type]
        std = statistics.stdev(filled)
        if std <= Z_STD_FLOOR:
            continue
        out[t] = (filled[-1] - statistics.fmean(filled)) / std
    return out


def attach_z_scores(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stamp per-leg 252-session z-scores. Rounding happens after the level."""
    ts_values = [row.get("_ts_raw", row["ts"]) for row in series]
    for field, z_field, values in (
        ("vix", "z_vix", [row["vix"] for row in series]),
        ("vvix", "z_vvix", [row["vvix"] for row in series]),
        ("ts", "z_ts", ts_values),
        ("skew", "z_skew", [row["skew"] for row in series]),
    ):
        zs = rolling_z(values)
        for row, z in zip(series, zs):
            row[z_field] = z
    return series


def strip_compute_fields(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop join-time scratch keys before the payload / upsert."""
    for row in series:
        row.pop("_ts_raw", None)
    return series


def attach_level(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equal-weight mean of the four unrounded z-scores, then 4dp.

    Spec C.4 then D: compute the mean first, round level and each z at
    build time. Rounding z before the mean misses the §C.8 anchors.
    """
    for row in series:
        zs = [row["z_vix"], row["z_vvix"], row["z_ts"], row["z_skew"]]
        if any(z is None for z in zs):
            row["level"] = None
        else:
            row["level"] = _round_or_none(statistics.fmean(zs), _Z_DP)
        for z_field in ("z_vix", "z_vvix", "z_ts", "z_skew"):
            row[z_field] = _round_or_none(row[z_field], _Z_DP)
    return series


def attach_delta(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One-day change vs the previous joined row. None if either level is None."""
    for i, row in enumerate(series):
        if i == 0 or row["level"] is None or series[i - 1]["level"] is None:
            row["delta_1d"] = None
        else:
            row["delta_1d"] = _round_or_none(row["level"] - series[i - 1]["level"], _Z_DP)
    return series


def attach_skew25d(
    series: list[dict[str, Any]], overlay_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """UW 25d put/call overlay. Same 252 window; never enters the composite."""
    by_date = _by_date(overlay_rows)
    values: list[Optional[float]] = [by_date.get(row["date"]) for row in series]
    zs = rolling_z(values)
    for row, value, z in zip(series, values, zs):
        row["skew25d"] = value
        row["z_skew25d"] = _round_or_none(z, _Z_DP)
    return series


def trailing_delta_stats(series: list[dict[str, Any]]) -> dict[str, Any]:
    """Trailing-10y Δ1d mean / sample std / z / ranks for the latest row."""
    empty = {
        "delta_mean_10y": None,
        "delta_std_10y": None,
        "delta_z": None,
        "rank_decline_10y": None,
        "rank_surge_10y": None,
        "rank_n": None,
    }
    deltas = [row["delta_1d"] for row in series if row.get("delta_1d") is not None]
    if len(deltas) < RANK_MIN_ROWS:
        return empty
    window = deltas[-RANK_WINDOW:]
    latest = window[-1]
    mean = statistics.fmean(window)
    std = statistics.stdev(window)
    delta_z = None if std <= Z_STD_FLOOR else (latest - mean) / std
    return {
        "delta_mean_10y": mean,
        "delta_std_10y": std,
        "delta_z": _round_or_none(delta_z, _DELTA_Z_DP),
        "rank_decline_10y": 1 + sum(1 for v in window if v < latest),
        "rank_surge_10y": 1 + sum(1 for v in window if v > latest),
        "rank_n": len(window),
    }


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Population stats over the entire Δ1d series, matching skew.md."""
    points = [(row["date"], row["delta_1d"]) for row in series if row.get("delta_1d") is not None]
    if not points:
        return None
    values = [value for _, value in points]
    low = min(values)
    high = max(values)
    return {
        "high": _round_or_none(high, _Z_DP),
        "high_date": next(date for date, value in points if value == high),
        "low": _round_or_none(low, _Z_DP),
        "low_date": next(date for date, value in points if value == low),
        "avg": _round_or_none(statistics.fmean(values), _Z_DP),
        "stddev": _round_or_none(statistics.pstdev(values), _Z_DP),
    }


def build_current(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Latest-session card. None only when series is empty."""
    if not series:
        return None
    latest = series[-1]
    trailing = trailing_delta_stats(series)
    legs: dict[str, Any] = {
        "vix": {"value": latest["vix"], "z": latest["z_vix"]},
        "vvix": {"value": latest["vvix"], "z": latest["z_vvix"]},
        "ts": {"value": latest["ts"], "z": latest["z_ts"], "vix3m": latest["vix3m"]},
        "skew": {"value": latest["skew"], "z": latest["z_skew"]},
    }
    if latest.get("skew25d") is not None or latest.get("z_skew25d") is not None:
        legs["skew25d"] = {"value": latest.get("skew25d"), "z": latest.get("z_skew25d")}
    else:
        legs["skew25d"] = None
    return {
        "date": latest["date"],
        "level": latest["level"],
        "delta_1d": latest["delta_1d"],
        "delta_z": trailing["delta_z"],
        "delta_std_10y": _round_or_none(trailing["delta_std_10y"], _Z_DP),
        "rank_decline_10y": trailing["rank_decline_10y"],
        "rank_surge_10y": trailing["rank_surge_10y"],
        "rank_n": trailing["rank_n"],
        "legs": legs,
    }


def pearson_z_skew_overlay(series: list[dict[str, Any]]) -> Optional[float]:
    """Pearson(z_SKEW, z_25d) over the overlap. Overlay diagnostic only."""
    pairs = [
        (row["z_skew"], row["z_skew25d"])
        for row in series
        if row.get("z_skew") is not None and row.get("z_skew25d") is not None
    ]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    return statistics.correlation(xs, ys)


def ensure_plausible_series(
    series: list[dict[str, Any]],
    dropped_dates: list[str],
    base_count: int,
) -> None:
    """Raise ValueError when the built series cannot be a healthy Cboe pull."""
    if len(series) < MIN_SERIES_ROWS:
        raise ValueError(
            f"panic-index series has {len(series)} rows; expected at least {MIN_SERIES_ROWS}"
        )
    if base_count <= 0:
        raise ValueError("panic-index VIX∩VIX3M base_count is 0")
    dropped_share = len(dropped_dates) / base_count
    if dropped_share > MAX_DROPPED_SHARE:
        raise ValueError(
            f"panic-index dropped {len(dropped_dates)} of {base_count} VIX∩VIX3M "
            f"dates ({dropped_share:.4f}); max share is {MAX_DROPPED_SHARE}"
        )
    bad_leg = next((row for row in series if row["vix3m"] <= 0), None)
    if bad_leg is not None:
        raise ValueError(
            f"panic-index row {bad_leg['date']} carries a non-positive vix3m ({bad_leg['vix3m']})"
        )
    if not series:
        raise ValueError("panic-index series is empty")
    latest = series[-1]
    if latest.get("level") is None:
        raise ValueError(
            f"panic-index latest row {latest['date']} has no composite level"
        )
    checks = (
        ("vix", latest["vix"], VIX_SANITY),
        ("vvix", latest["vvix"], VVIX_SANITY),
        ("ts_ratio", latest["ts"], TS_RATIO_SANITY),
        ("skew", latest["skew"], SKEW_SANITY),
    )
    for name, value, (lo, hi) in checks:
        if not lo <= value <= hi:
            raise ValueError(
                f"panic-index latest {name} {value} on {latest['date']} is outside "
                f"the sane band [{lo}, {hi}]"
            )


def should_fire_decline(current: Optional[dict[str, Any]], *, status: str, expected_session: str) -> bool:
    """True when the latest Δ1d qualifies for the record-decline push."""
    if status != "ok" or not current:
        return False
    if current.get("date") != expected_session:
        return False
    rank_n = current.get("rank_n")
    if rank_n is None or rank_n < RANK_MIN_ROWS:
        return False
    delta = current.get("delta_1d")
    std = current.get("delta_std_10y")
    if delta is None or std is None:
        return False
    return delta <= -ALERT_SIGMA * std


def format_alert_message(current: dict[str, Any]) -> str:
    """One-line priority-0 copy. No em dashes."""
    legs = current["legs"]
    message = (
        f"Panic Proxy 1d change {current['delta_1d']:+.2f} "
        f"({current['delta_z']:+.1f}σ vs 10y). "
        f"Rank #{current['rank_decline_10y']} most negative of {current['rank_n']} sessions. "
        f"Level {current['level']:+.2f}. "
        f"Legs z: VIX {legs['vix']['z']:+.1f}, VVIX {legs['vvix']['z']:+.1f}, "
        f"TS {legs['ts']['z']:+.1f}, SKEW {legs['skew']['z']:+.1f}. "
        "Radon proxy, not GS."
    )
    rank = current.get("rank_decline_10y")
    if rank is not None and rank <= ALERT_TOP_N:
        message = f"TOP-{rank} IN 10Y. {message}"
    return message

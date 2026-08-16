"""VIXCOR pure math — rolling VIX x COR3M correlation, episodes, statistics.

Every formula and every threshold for the VIXCOR indicator lives here so the
ingest job (``scripts/fetch_vixcor.py``) stays orchestration only. Nothing in
this module touches the network, Turso, the clock or the filesystem.

Convention, settled in docs/indicators/vixcor.md section B.1: the correlation
is Pearson over price LEVELS, not returns, which is what StockCharts'
``CORR(a,b,n)`` plots and the only convention that reproduces the operator's
chart. A level correlation collapses when either leg goes range-bound, so a
low reading marks a quiet VIX tape rather than a stress warning.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Optional

# ── named constants (scoped to FULL history, never a visible slice) ──

WINDOW = 20                     # joined sessions, trailing, INCLUSIVE of today
MIN_OBSERVATIONS = 20           # == WINDOW. No partial-window value, ever.
BREAKDOWN_TRIGGER = 0.25        # corr20 < this opens / confirms an episode
BREAKDOWN_EXIT = 0.30           # hysteresis: the episode runs while corr20 < this
EPISODE_MERGE_SESSIONS = 10     # sub-EXIT runs this many sessions apart merge
EPISODE_DEBOUNCE_SESSIONS = 42  # a trigger this close to the prior one is absorbed
FORWARD_HORIZONS = (5, 10, 21, 42, 63)   # joined sessions
DRAWUP_MATERIAL_THRESHOLD = 0.20         # the "+20% drawup" column
LOOSENING_FLOOR = 0.50          # regime band edge
COUPLED_COV_FLOOR = 0.75        # windows at/above this form the coupled CoV pool

REGIME_DECOUPLED = "DECOUPLED"
REGIME_LOOSENING = "LOOSENING"
REGIME_COUPLED = "COUPLED"


# ── correlation ───────────────────────────────────────────────────

def corr_window(xs: Iterable[float], ys: Iterable[float]) -> Optional[float]:
    """Sample Pearson correlation of two equal-length level series.

    The n-1 cancels between numerator and denominator, so this sums form is
    identical to the population form and cannot drift with a stats library's
    denominator choice. A degenerate window (one leg constant, den == 0) is
    unmeasurable and returns None: 0.0 is a legitimate breakdown reading and
    conflating the two would fabricate an episode.
    """
    xs = list(xs)
    ys = list(ys)
    if len(xs) != len(ys) or not xs:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    numerator = sum(a * b for a, b in zip(dx, dy))
    denominator = math.sqrt(sum(a * a for a in dx) * sum(b * b for b in dy))
    if denominator == 0:
        return None
    return numerator / denominator


# ── the join ──────────────────────────────────────────────────────

def join_series(
    vix_rows: list[dict[str, Any]], cor3m_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Inner join two ``[{date, value}]`` series on session date, ascending.

    cor3m dates are a strict subset of VIX dates, so the result is the cor3m
    session calendar. Forward filling is banned (section B.5): pairing a
    repeated parent point with a genuinely-moved VIX drags the window by up
    to 0.20, enough to fabricate or erase a trough.
    """
    vix_by_date = {row["date"]: row["value"] for row in vix_rows}
    cor3m_by_date = {row["date"]: row["value"] for row in cor3m_rows}
    return [
        {
            "date": session,
            "vix_close": vix_by_date[session],
            "cor3m_close": cor3m_by_date[session],
        }
        for session in sorted(set(vix_by_date) & set(cor3m_by_date))
    ]


def compute_corr_series(joined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach the trailing WINDOW-session correlation to every joined row.

    Rows before the window fills carry ``corr20: None`` so the date axis
    stays continuous. Window ``i`` spans joined rows ``[i-19 .. i]``.
    """
    series: list[dict[str, Any]] = []
    for i, row in enumerate(joined):
        corr20 = None
        if i + 1 >= MIN_OBSERVATIONS:
            window = joined[i + 1 - WINDOW:i + 1]
            corr20 = corr_window(
                [entry["vix_close"] for entry in window],
                [entry["cor3m_close"] for entry in window],
            )
        series.append({**row, "corr20": corr20})
    return series


# ── breakdown episodes ────────────────────────────────────────────

def _sub_exit_runs(values: list[float]) -> list[list[int]]:
    """Maximal runs of consecutive positions with ``corr20 < BREAKDOWN_EXIT``."""
    runs: list[list[int]] = []
    for position, value in enumerate(values):
        if value >= BREAKDOWN_EXIT:
            continue
        if runs and runs[-1][1] == position - 1:
            runs[-1][1] = position
        else:
            runs.append([position, position])
    return runs


def _merge_nearby_runs(runs: list[list[int]]) -> list[list[int]]:
    """Fold runs separated by at most EPISODE_MERGE_SESSIONS sessions together."""
    merged: list[list[int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] - 1 <= EPISODE_MERGE_SESSIONS:
            merged[-1][1] = end
            continue
        merged.append([start, end])
    return merged


def detect_episodes(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Breakdown episodes over the emittable part of the series.

    A run below BREAKDOWN_EXIT is an episode only once it contains a session
    below BREAKDOWN_TRIGGER; a trigger within EPISODE_DEBOUNCE_SESSIONS of the
    previously emitted trigger extends that episode instead of opening a new
    one. All comparisons are strict, so 0.25 is not a trigger and 0.30 closes
    a run.
    """
    points = [(index, row) for index, row in enumerate(series) if row.get("corr20") is not None]
    if not points:
        return []
    values = [row["corr20"] for _index, row in points]
    last_series_index = len(series) - 1

    def episode_from(start: int, end: int, trigger: int) -> dict[str, Any]:
        trough_position = min(range(start, end + 1), key=lambda k: values[k])
        return {
            "trigger": points[trigger][1]["date"],
            "start": points[start][1]["date"],
            "end": points[end][1]["date"],
            "sessions": end - start + 1,
            "trough": values[trough_position],
            "trough_date": points[trough_position][1]["date"],
            "corr_at_trigger": values[trigger],
            "vix_at_trigger": points[trigger][1]["vix_close"],
            "open": points[end][0] == last_series_index,
            "_start": start,
            "_end": end,
            "_trigger": trigger,
        }

    episodes: list[dict[str, Any]] = []
    for start, end in _merge_nearby_runs(_sub_exit_runs(values)):
        trigger = next(
            (k for k in range(start, end + 1) if values[k] < BREAKDOWN_TRIGGER), None
        )
        if trigger is None:
            continue
        prior = episodes[-1] if episodes else None
        if prior is not None and trigger - prior["_trigger"] <= EPISODE_DEBOUNCE_SESSIONS:
            episodes[-1] = episode_from(prior["_start"], end, prior["_trigger"])
            continue
        episodes.append(episode_from(start, end, trigger))
    return [
        {key: value for key, value in episode.items() if not key.startswith("_")}
        for episode in episodes
    ]


# ── forward statistics (descriptive only) ─────────────────────────

def forward_drawup(
    series: list[dict[str, Any]], index: int, horizon: int
) -> Optional[float]:
    """Peak VIX over the next ``horizon`` joined sessions, as a fraction."""
    if index + horizon >= len(series):
        return None
    base = series[index]["vix_close"]
    if not base:
        return None
    peak = max(series[j]["vix_close"] for j in range(index + 1, index + horizon + 1))
    return peak / base - 1


def _forward_bucket(
    series: list[dict[str, Any]], indexes: list[int], horizon: int
) -> dict[str, Any]:
    drawups: list[float] = []
    higher: list[bool] = []
    for index in indexes:
        drawup = forward_drawup(series, index, horizon)
        if drawup is None:
            continue
        drawups.append(drawup)
        higher.append(series[index + horizon]["vix_close"] > series[index]["vix_close"])
    if not drawups:
        return {
            "n": 0,
            "mean_drawup": None,
            "median_drawup": None,
            "p_higher": None,
            "p_drawup_20": None,
        }
    return {
        "n": len(drawups),
        "mean_drawup": statistics.fmean(drawups),
        "median_drawup": statistics.median(drawups),
        "p_higher": sum(1 for flag in higher if flag) / len(higher),
        "p_drawup_20": sum(1 for value in drawups if value >= DRAWUP_MATERIAL_THRESHOLD)
        / len(drawups),
    }


def _indexes_by_date(series: list[dict[str, Any]]) -> dict[str, int]:
    return {row["date"]: index for index, row in enumerate(series)}


def attach_forward_drawups(
    series: list[dict[str, Any]], episodes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-episode forward VIX drawup off the trigger, keyed by string horizon."""
    positions = _indexes_by_date(series)
    return [
        {
            **episode,
            "forward": {
                str(horizon): forward_drawup(series, positions[episode["trigger"]], horizon)
                for horizon in FORWARD_HORIZONS
            },
        }
        for episode in episodes
    ]


def episode_membership(
    series: list[dict[str, Any]], episodes: list[dict[str, Any]]
) -> set[str]:
    """Every session date inside a detected episode, so the chart can shade
    without re-deriving the episode boundaries."""
    positions = _indexes_by_date(series)
    dates: set[str] = set()
    for episode in episodes:
        span = series[positions[episode["start"]]:positions[episode["end"]] + 1]
        dates.update(row["date"] for row in span)
    return dates


def compute_forward_stats(
    series: list[dict[str, Any]], episodes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Post-breakdown forward VIX drawup beside the all-session base rate.

    The base aggregate is the null the UI must display next to the event
    aggregate. The study behind this indicator found the post-breakdown drawup
    below the all-session mean at every horizon, while on the median the two
    are indistinguishable at 42 sessions (event 31.79% against base 29.31%,
    P(+20% drawup) 63.3% against 63.8%, Mann-Whitney p=0.68). The mean-only gap
    is manufactured by the right skew of the base distribution (mean/median
    ~1.50), so every bucket carries median_drawup and p_drawup_20 alongside the
    mean and the UI shows all three.
    """
    positions = _indexes_by_date(series)
    event_indexes = [
        positions[episode["trigger"]] for episode in episodes if not episode["open"]
    ]
    base_indexes = [
        index for index, row in enumerate(series) if row.get("corr20") is not None
    ]
    return {
        "horizons": list(FORWARD_HORIZONS),
        "event": {
            str(horizon): _forward_bucket(series, event_indexes, horizon)
            for horizon in FORWARD_HORIZONS
        },
        "base": {
            str(horizon): _forward_bucket(series, base_indexes, horizon)
            for horizon in FORWARD_HORIZONS
        },
    }


# ── distribution + the current card ───────────────────────────────

def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolated quantile over the sorted sample."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def coefficient_of_variation(values: list[float]) -> Optional[float]:
    """Sample standard deviation over the mean; None when unmeasurable."""
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return statistics.stdev(values) / mean


def _trailing_vix_cov(series: list[dict[str, Any]], index: int) -> Optional[float]:
    if index + 1 < WINDOW:
        return None
    window = series[index + 1 - WINDOW:index + 1]
    return coefficient_of_variation([row["vix_close"] for row in window])


def compute_stats(series: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Full-history distribution of corr20 plus the quiet-tape mechanism split."""
    values = [row["corr20"] for row in series if row.get("corr20") is not None]
    if not values:
        return None
    ordered = sorted(values)
    breakdown_covs: list[float] = []
    coupled_covs: list[float] = []
    for index, row in enumerate(series):
        corr20 = row.get("corr20")
        if corr20 is None:
            continue
        cov = _trailing_vix_cov(series, index)
        if cov is None:
            continue
        if corr20 < BREAKDOWN_TRIGGER:
            breakdown_covs.append(cov)
        elif corr20 >= COUPLED_COV_FLOOR:
            coupled_covs.append(cov)
    return {
        "min": ordered[0],
        "p01": _percentile(ordered, 0.01),
        "p05": _percentile(ordered, 0.05),
        "p10": _percentile(ordered, 0.10),
        "p25": _percentile(ordered, 0.25),
        "median": statistics.median(values),
        "p75": _percentile(ordered, 0.75),
        "p90": _percentile(ordered, 0.90),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "max": ordered[-1],
        "mean": statistics.fmean(values),
        "stddev": statistics.pstdev(values),
        "share_below_zero": sum(1 for value in values if value < 0) / len(values),
        "share_below_trigger": sum(
            1 for value in values if value < BREAKDOWN_TRIGGER
        ) / len(values),
        "vix_cov_breakdown": statistics.fmean(breakdown_covs) if breakdown_covs else None,
        "vix_cov_coupled": statistics.fmean(coupled_covs) if coupled_covs else None,
    }


def classify_regime(corr20: Optional[float]) -> Optional[str]:
    """Strict-inequality regime band. DECOUPLED is a structural state, not a fault."""
    if corr20 is None:
        return None
    if corr20 < BREAKDOWN_TRIGGER:
        return REGIME_DECOUPLED
    if corr20 < LOOSENING_FLOOR:
        return REGIME_LOOSENING
    return REGIME_COUPLED


def build_current(
    series: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    stats: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Latest-session card: reading, one-session change, percentile, regime."""
    if not series:
        return None
    latest = series[-1]
    values = [row["corr20"] for row in series if row.get("corr20") is not None]
    corr20 = latest.get("corr20")
    change_1d = corr20 - values[-2] if corr20 is not None and len(values) > 1 else None
    percentile = (
        sum(1 for value in values if value < corr20) / len(values)
        if corr20 is not None and values
        else None
    )
    open_episode = next(
        (episode for episode in reversed(episodes) if episode["open"]), None
    )
    return {
        "date": latest["date"],
        "vix_close": latest["vix_close"],
        "cor3m_close": latest["cor3m_close"],
        "corr20": corr20,
        "change_1d": change_1d,
        "percentile": percentile,
        "regime": classify_regime(corr20),
        "vix_cov_20d": _trailing_vix_cov(series, len(series) - 1),
        "episode": open_episode,
    }


# ── session arithmetic ────────────────────────────────────────────

def count_vix_sessions_between(
    vix_dates: Iterable[str], after: str, through: str
) -> int:
    """Sessions strictly after ``after`` and at or before ``through``.

    Counted on the VIX session calendar, never in calendar days: a Friday to
    Monday parent gap is one session behind, not three.
    """
    return sum(1 for session in vix_dates if after < session <= through)

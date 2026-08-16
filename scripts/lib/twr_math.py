"""Pure time-weighted-return math. Standard library only.

No I/O, no environment, no network, no database, no third-party numerics: every
value produced here is hand-computable, which is what makes the fixture pins in
tests/test_twr_math.py meaningful.

Sign conventions, normative throughout:

* ``C > 0`` is capital INTO the account (deposit, transfer in); ``C < 0`` is out.
* Returns, alpha, VaR, CVaR and drawdowns are signed decimals. A loss is
  negative. There are no magnitudes.
* MWR cashflows use the INVESTOR's sign — money the investor commits is
  negative. That flip happens in exactly one place, ``build_cashflow_vector``.

Flow-timing convention is END OF DAY: ``r_t = (E_t - C_t - B_t) / B_t``.
"""

from __future__ import annotations

import enum
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from scripts.lib import twr_gates

_ANNUALIZATION = math.sqrt(twr_gates.TRADING_DAYS)
_TOTAL_LOSS_RETURN = -1.0
_XIRR_LOWER_BOUND = -0.9999999
_XIRR_UPPER_SEED = 10.0
_XIRR_UPPER_CAP = 1e6
_XIRR_MAX_ITERATIONS = 200
_XIRR_WIDTH_TOLERANCE = 1e-12
_XIRR_SCAN_STEP = 0.01

# `total_loss` is the one reason that carries a value: -100% is the analytic
# limit, not an absence of information.
_VALUE_BEARING_REASONS = frozenset({"total_loss"})


class UnknownFlowType(ValueError):
    """An IBKR flow `type` outside the classifier's allowlist."""


class DuplicateNavDate(ValueError):
    """Two NAV observations claim the same date; last-write-wins is not a fix."""


class NonFiniteInput(ValueError):
    """A NAV or flow value is NaN or infinite."""


class FlowClass(enum.Enum):
    EXTERNAL = "external"   # investor capital; subtract from the return
    INTERNAL = "internal"   # account earnings/costs; leave inside the return


class FlowsStatus(enum.Enum):
    OK = "ok"                          # fetched, at least one external flow found
    EMPTY_VERIFIED = "empty_verified"  # fetched, genuinely zero external flows
    FAILED = "failed"                  # fetch or parse failed; TWR must not publish


@dataclass(frozen=True)
class NavObservation:
    date: str
    nav: float


@dataclass(frozen=True)
class FlowSet:
    status: FlowsStatus
    by_date: Mapping[str, float] = field(default_factory=dict)
    source: str = ""
    reason: str = ""
    # Flow sections the statement did not carry. Flows were still observed, but
    # only partially: the operator needs to be told which class is unreported.
    missing_sections: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status is FlowsStatus.FAILED:
            if self.by_date:
                raise ValueError("a FAILED FlowSet cannot carry flows")
            if not self.reason:
                raise ValueError("a FAILED FlowSet needs a non-empty reason")
        if self.status is FlowsStatus.EMPTY_VERIFIED and self.by_date:
            raise ValueError("an EMPTY_VERIFIED FlowSet cannot carry flows")

    @classmethod
    def failed(cls, reason: str) -> "FlowSet":
        if not reason:
            raise ValueError("FlowSet.failed requires a reason")
        return cls(status=FlowsStatus.FAILED, by_date={}, source="", reason=reason)

    @classmethod
    def empty_verified(cls, source: str = "") -> "FlowSet":
        return cls(status=FlowsStatus.EMPTY_VERIFIED, by_date={}, source=source)

    @property
    def is_usable(self) -> bool:
        return self.status is not FlowsStatus.FAILED


@dataclass(frozen=True)
class Subperiod:
    date: str
    begin_nav: float
    end_nav: float
    flow: float
    denominator: float
    ret: Optional[float]
    cum_ret: Optional[float]
    gap_days: int
    flags: Tuple[str, ...]
    skip_reason: Optional[str]


@dataclass(frozen=True)
class SubperiodChain:
    subperiods: Tuple[Subperiod, ...]
    returns: Tuple[float, ...]
    cum_return: float
    n_nav_observations: int
    n_subperiods: int
    n_returns: int
    period_start: Optional[str]
    period_end: Optional[str]
    calendar_days: int
    excluded_suspect: bool


@dataclass(frozen=True)
class GatedValue:
    value: Optional[float]
    n: int
    min_n: int
    unavailable_reason: Optional[str]
    low_confidence: bool = False

    def __post_init__(self) -> None:
        if self.unavailable_reason in _VALUE_BEARING_REASONS:
            return
        if (self.value is None) != (self.unavailable_reason is not None):
            raise ValueError(
                f"GatedValue invariant broken: value={self.value!r} "
                f"reason={self.unavailable_reason!r}"
            )

    def as_dict(self) -> Dict[str, object]:
        return {
            "value": self.value,
            "n": self.n,
            "min_n": self.min_n,
            "unavailable_reason": self.unavailable_reason,
            "low_confidence": self.low_confidence,
        }


@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown: GatedValue
    current_drawdown: GatedValue
    trough_date: Optional[str]
    peak_date: Optional[str]
    trough_days: Optional[int]
    recovery_days: Optional[int]
    ongoing: bool


@dataclass(frozen=True)
class AlignedPairs:
    dates: Tuple[str, ...]
    portfolio: Tuple[float, ...]
    benchmark: Tuple[float, ...]
    n_common: int


@dataclass(frozen=True)
class BenchmarkBlock:
    symbol: str
    n_common: int
    coverage: float
    benchmark_return: float
    beta: float
    alpha_annualized: float
    correlation: float
    r_squared: float
    tracking_error: float
    information_ratio: float
    basis: str
    low_confidence: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "n_common": self.n_common,
            "coverage": self.coverage,
            "benchmark_return": self.benchmark_return,
            "beta": self.beta,
            "alpha_annualized": self.alpha_annualized,
            "correlation": self.correlation,
            "r_squared": self.r_squared,
            "tracking_error": self.tracking_error,
            "information_ratio": self.information_ratio,
            "basis": self.basis,
            "low_confidence": self.low_confidence,
        }


@dataclass(frozen=True)
class DatedCashflow:
    date: str
    amount: float


@dataclass(frozen=True)
class XirrResult:
    value: Optional[float]
    unavailable_reason: Optional[str]
    multiple_sign_changes: bool
    iterations: int


@dataclass(frozen=True)
class FlowPlacement:
    by_subperiod: Mapping[str, float]
    shifted: Tuple[Tuple[str, str], ...]
    after_period_end: Tuple[str, ...]
    before_period_start: Tuple[str, ...]


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _finite_returns(returns: Sequence[float]) -> List[float]:
    return [float(r) for r in returns if _is_finite(r)]


def _days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def daily_risk_free(rf_annual: float) -> float:
    """Geometric daily equivalent of an annual rate. Never rf/252."""
    if not _is_finite(rf_annual) or rf_annual <= -1.0:
        return 0.0
    return (1.0 + float(rf_annual)) ** (1.0 / twr_gates.TRADING_DAYS) - 1.0


def chain_returns(returns: Sequence[float]) -> float:
    growth = 1.0
    for r in returns:
        growth *= 1.0 + float(r)
    return growth - 1.0


def subperiod_return(begin_nav: float, end_nav: float, flow: float) -> Optional[float]:
    """EOD sub-period return, or None when the denominator is unusable."""
    if not (_is_finite(begin_nav) and _is_finite(end_nav) and _is_finite(flow)):
        return None
    if begin_nav <= 0:
        return None
    return (float(end_nav) - float(flow) - float(begin_nav)) / float(begin_nav)


@dataclass(frozen=True)
class _DraftSubperiod:
    """A subperiod before the chain has decided whether to trust it."""

    previous: NavObservation
    current: NavObservation
    flow: float
    ret: Optional[float]
    gap_days: int

    @property
    def magnitude(self) -> float:
        return 0.0 if self.ret is None else abs(self.ret)


def _draft_subperiod(
    previous: NavObservation, current: NavObservation, flow: float
) -> _DraftSubperiod:
    amount = float(flow)
    return _DraftSubperiod(
        previous=previous,
        current=current,
        flow=amount,
        ret=subperiod_return(previous.nav, current.nav, amount),
        gap_days=_days_between(previous.date, current.date),
    )


def _is_extreme(draft: _DraftSubperiod) -> bool:
    return draft.magnitude > twr_gates.SUSPECT_RETURN_THRESHOLD


def _is_unexplained(draft: _DraftSubperiod, outlier_bar: float) -> bool:
    """The residual, and only the residual, decides.

    ``r_t = (E - C - B) / B`` has ALREADY netted the recorded flow out, so `r_t`
    IS the unexplained part of the move. Interrogating `C_t` a second time is
    what let a partially recorded ACATS through: capture the cash leg of a
    725k transfer and the leftover 543k still reads as a +209% session, but a
    flow-versus-move ratio calls it explained.

    A wipeout is judged by the same rule. A recorded full withdrawal leaves
    ``r_t = (0 - (-B) - B) / B = 0`` and never comes near the bar; a NAV that
    drops to zero with nothing on file leaves ``r_t = -1.0`` and is the most
    suspect thing the series can contain.
    """
    if draft.ret is None:
        return False
    return _is_extreme(draft) or draft.magnitude > outlier_bar


def _dispersion_sample(drafts: Sequence[_DraftSubperiod]) -> List[float]:
    """|r| over the sessions a market could plausibly have produced."""
    return [d.magnitude for d in drafts if d.ret is not None and not _is_extreme(d)]


def _outlier_threshold(magnitudes: Sequence[float]) -> float:
    """The bar an unexplained session must clear to read as a missing transfer.

    Never absent, never zero, and never conditioned on the sample being large
    enough. A young account, a dormant account and a flat series each carry too
    little dispersion to describe themselves, and a bar that switched itself off
    there is exactly how an unrecorded +22.7% deposit published as `ok`. The
    absolute floor stands in for the sample that isn't there; an account with
    real dispersion is measured against its own typical session instead.
    """
    floor = twr_gates.UNEXPLAINED_ABSOLUTE_FLOOR
    if not magnitudes:
        return floor
    typical = statistics.median(magnitudes)
    return max(typical * twr_gates.UNEXPLAINED_OUTLIER_MULTIPLE, floor)


def _suspect_indices(drafts: Sequence[_DraftSubperiod]) -> set[int]:
    """Subperiods whose residual no market produces and no flow has explained."""
    bar = _outlier_threshold(_dispersion_sample(drafts))
    return {i for i, draft in enumerate(drafts) if _is_unexplained(draft, bar)}


# ---------------------------------------------------------------------------
# chain construction
# ---------------------------------------------------------------------------


def _sorted_observations(nav: Sequence[NavObservation]) -> List[NavObservation]:
    seen: Dict[str, float] = {}
    for observation in nav:
        if not _is_finite(observation.nav):
            raise NonFiniteInput(f"NAV for {observation.date} is not finite")
        if observation.date in seen:
            raise DuplicateNavDate(observation.date)
        seen[observation.date] = float(observation.nav)
    return [NavObservation(date=d, nav=seen[d]) for d in sorted(seen)]


def place_flows(nav_dates: Sequence[str], flows: Mapping[str, float]) -> FlowPlacement:
    """Attach each flow to the first NAV observation that contains the money."""
    ordered = sorted(nav_dates)
    if not ordered:
        return FlowPlacement({}, (), (), tuple(sorted(flows)))

    seed, last = ordered[0], ordered[-1]
    subperiod_dates = ordered[1:]
    by_subperiod: Dict[str, float] = {}
    shifted: List[Tuple[str, str]] = []
    after_end: List[str] = []
    before_start: List[str] = []

    for flow_date in sorted(flows):
        amount = float(flows[flow_date])
        if not _is_finite(amount):
            raise NonFiniteInput(f"flow for {flow_date} is not finite")
        if flow_date <= seed:
            before_start.append(flow_date)
            continue
        if flow_date > last:
            after_end.append(flow_date)
            continue
        applied = next(d for d in subperiod_dates if d >= flow_date)
        if applied != flow_date:
            shifted.append((flow_date, applied))
        by_subperiod[applied] = by_subperiod.get(applied, 0.0) + amount

    return FlowPlacement(by_subperiod, tuple(shifted), tuple(after_end), tuple(before_start))


def _empty_chain() -> SubperiodChain:
    return SubperiodChain((), (), 0.0, 0, 0, 0, None, None, 0, False)


def build_subperiods(
    nav: Sequence[NavObservation], flows: Mapping[str, float]
) -> SubperiodChain:
    observations = _sorted_observations(nav)
    if not observations:
        return _empty_chain()

    dates = [o.date for o in observations]
    placement = place_flows(dates, flows)

    drafts = [
        _draft_subperiod(previous, current, placement.by_subperiod.get(current.date, 0.0))
        for previous, current in zip(observations, observations[1:])
    ]
    suspects = _suspect_indices(drafts)

    subperiods: List[Subperiod] = []
    included: List[float] = []
    growth = 1.0

    for index, draft in enumerate(drafts):
        flags: List[str] = []
        if draft.gap_days > twr_gates.MAX_SUBPERIOD_GAP_DAYS:
            flags.append("nav_gap")

        ret = draft.ret
        skip_reason: Optional[str] = None

        if ret is None:
            skip_reason = "zero_base" if draft.previous.nav == 0 else "negative_base"
            flags.append(skip_reason)
        else:
            if abs(draft.flow) / draft.previous.nav > twr_gates.FLOW_DOMINANT_RATIO:
                flags.append("flow_dominant")
            if index in suspects:
                flags.append("suspect")
                skip_reason = "suspect_no_flow"
                ret = None

        if ret is not None:
            growth *= 1.0 + ret
            included.append(ret)

        subperiods.append(
            Subperiod(
                date=draft.current.date,
                begin_nav=draft.previous.nav,
                end_nav=draft.current.nav,
                flow=draft.flow,
                denominator=draft.previous.nav,
                ret=ret,
                cum_ret=(growth - 1.0) if ret is not None else None,
                gap_days=draft.gap_days,
                flags=tuple(flags),
                skip_reason=skip_reason,
            )
        )

    return SubperiodChain(
        subperiods=tuple(subperiods),
        returns=tuple(included),
        cum_return=growth - 1.0,
        n_nav_observations=len(observations),
        n_subperiods=len(subperiods),
        n_returns=len(included),
        period_start=dates[0],
        period_end=dates[-1],
        calendar_days=_days_between(dates[0], dates[-1]),
        excluded_suspect=bool(suspects),
    )


def consolidate_accounts(
    per_account_nav: Mapping[str, object],
    per_account_flows: Optional[Mapping[str, object]] = None,
) -> Tuple[List[NavObservation], Dict[str, float], List[str]]:
    """Sum NAV/flows across accounts. A date one account is missing is DROPPED."""
    nav_maps = {acct: _as_date_map(series) for acct, series in per_account_nav.items()}
    every_date = sorted({d for series in nav_maps.values() for d in series})
    complete = [d for d in every_date if all(d in series for series in nav_maps.values())]
    gap_dates = [d for d in every_date if d not in complete]

    nav = [
        NavObservation(date=d, nav=sum(series[d] for series in nav_maps.values()))
        for d in complete
    ]

    flows: Dict[str, float] = {}
    for series in (per_account_flows or {}).values():
        for d, amount in _as_date_map(series).items():
            flows[d] = flows.get(d, 0.0) + amount
    return nav, flows, gap_dates


def _as_date_map(series: object) -> Dict[str, float]:
    if hasattr(series, "items"):
        return {d: float(v) for d, v in series.items()}  # type: ignore[union-attr]
    return {o.date: float(o.nav) for o in series}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# return statistics
# ---------------------------------------------------------------------------


def _gate(n: int, min_n: int) -> Optional[str]:
    return "insufficient_n" if n < min_n else None


def annualize_return(cum_return: float, calendar_days: int) -> GatedValue:
    min_days = twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED
    if not _is_finite(cum_return):
        return GatedValue(None, calendar_days, min_days, "invalid_total_return")
    if cum_return == _TOTAL_LOSS_RETURN:
        return GatedValue(_TOTAL_LOSS_RETURN, calendar_days, min_days, "total_loss")
    if cum_return < _TOTAL_LOSS_RETURN:
        return GatedValue(None, calendar_days, min_days, "invalid_total_return")
    if calendar_days < min_days:
        return GatedValue(None, calendar_days, min_days, "period_lt_1y")
    exponent = twr_gates.DAYS_PER_YEAR / calendar_days
    return GatedValue((1.0 + cum_return) ** exponent - 1.0, calendar_days, min_days, None)


def volatility(returns: Sequence[float], *, min_n: Optional[int] = None) -> GatedValue:
    rets = _finite_returns(returns)
    n = len(rets)
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n")
    return GatedValue(statistics.stdev(rets) * _ANNUALIZATION, n, limit, None)


def downside_deviation(
    returns: Sequence[float],
    target: float = 0.0,
    *,
    min_n: Optional[int] = None,
) -> GatedValue:
    rets = _finite_returns(returns)
    n = len(rets)
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n")
    return GatedValue(_downside_daily(rets, target) * _ANNUALIZATION, n, limit, None)


def _downside_daily(returns: Sequence[float], target: float) -> float:
    """Root-mean-square shortfall, divided by N — never by the downside count."""
    shortfalls = [min(float(r) - target, 0.0) ** 2 for r in returns]
    return math.sqrt(sum(shortfalls) / len(returns))


def sharpe_ratio(
    returns: Sequence[float], rf_daily: float, *, min_n: Optional[int] = None
) -> GatedValue:
    rets = _finite_returns(returns)
    n = len(rets)
    limit = twr_gates.MIN_N_RATIO if min_n is None else min_n
    low_confidence = n < twr_gates.RATIO_LOW_CONFIDENCE_N
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n", low_confidence)
    sd = statistics.stdev(rets)
    if sd == 0:
        return GatedValue(None, n, limit, "degenerate", low_confidence)
    excess = statistics.fmean(rets) - rf_daily
    return GatedValue(excess / sd * _ANNUALIZATION, n, limit, None, low_confidence)


def sortino_ratio(
    returns: Sequence[float],
    rf_daily: float,
    target: Optional[float] = None,
    *,
    min_n: Optional[int] = None,
    min_downside: Optional[int] = None,
) -> GatedValue:
    rets = _finite_returns(returns)
    n = len(rets)
    limit = twr_gates.MIN_N_RATIO if min_n is None else min_n
    floor = twr_gates.SORTINO_TARGET if target is None else target
    needed = twr_gates.MIN_DOWNSIDE_OBSERVATIONS if min_downside is None else min_downside
    low_confidence = n < twr_gates.RATIO_LOW_CONFIDENCE_N
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n", low_confidence)
    if sum(1 for r in rets if r < floor) < needed:
        return GatedValue(None, n, limit, "no_downside", low_confidence)
    downside = _downside_daily(rets, floor)
    if downside == 0:
        return GatedValue(None, n, limit, "no_downside", low_confidence)
    excess = statistics.fmean(rets) - rf_daily
    return GatedValue(excess / downside * _ANNUALIZATION, n, limit, None, low_confidence)


def historical_var(
    returns: Sequence[float], level: float = 0.05, *, min_n: Optional[int] = None
) -> GatedValue:
    rets = sorted(_finite_returns(returns))
    n = len(rets)
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    low_confidence = n < twr_gates.VAR_LOW_CONFIDENCE_N
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n", low_confidence)
    position = (n - 1) * level
    lower = math.floor(position)
    upper = math.ceil(position)
    value = rets[lower] + (position - lower) * (rets[upper] - rets[lower])
    return GatedValue(value, n, limit, None, low_confidence)


def conditional_var(
    returns: Sequence[float], level: float = 0.05, *, min_n: Optional[int] = None
) -> GatedValue:
    rets = sorted(_finite_returns(returns))
    n = len(rets)
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    low_confidence = n < twr_gates.VAR_LOW_CONFIDENCE_N
    if n < limit or n < 2:
        return GatedValue(None, n, limit, "insufficient_n", low_confidence)
    tail = max(1, math.floor(level * n))
    return GatedValue(statistics.fmean(rets[:tail]), n, limit, None, low_confidence)


def return_distribution(
    returns: Sequence[float], *, min_n: Optional[int] = None
) -> Dict[str, GatedValue]:
    rets = _finite_returns(returns)
    n = len(rets)
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    names = (
        "hit_rate",
        "best_day",
        "worst_day",
        "average_up_day",
        "average_down_day",
        "win_loss_ratio",
        "positive_days",
        "negative_days",
        "flat_days",
    )
    if n < limit or n < 1:
        return {name: GatedValue(None, n, limit, "insufficient_n") for name in names}

    ups = [r for r in rets if r > 0]
    downs = [r for r in rets if r < 0]
    average_up = statistics.fmean(ups) if ups else None
    average_down = statistics.fmean(downs) if downs else None

    values: Dict[str, Optional[float]] = {
        "hit_rate": len(ups) / n,
        "best_day": max(rets),
        "worst_day": min(rets),
        "average_up_day": average_up,
        "average_down_day": average_down,
        "win_loss_ratio": (
            average_up / abs(average_down) if average_up and average_down else None
        ),
        "positive_days": float(len(ups)),
        "negative_days": float(len(downs)),
        "flat_days": float(n - len(ups) - len(downs)),
    }
    return {
        name: (
            GatedValue(value, n, limit, None)
            if value is not None
            else GatedValue(None, n, limit, "not_computed")
        )
        for name, value in values.items()
    }


# ---------------------------------------------------------------------------
# drawdowns
# ---------------------------------------------------------------------------


def drawdown_path(index_values: Sequence[float]) -> List[float]:
    path: List[float] = []
    peak = float("-inf")
    for value in index_values:
        peak = max(peak, value)
        path.append(value / peak - 1.0 if peak > 0 else 0.0)
    return path


def drawdowns(chain: SubperiodChain, *, min_n: Optional[int] = None) -> DrawdownResult:
    limit = twr_gates.MIN_N_DISPERSION if min_n is None else min_n
    n = chain.n_returns
    if n < limit or n < 1:
        gated = GatedValue(None, n, limit, "insufficient_n")
        return DrawdownResult(gated, gated, None, None, None, None, False)

    dates: List[str] = [chain.period_start or ""]
    index: List[float] = [1.0]
    growth = 1.0
    for subperiod in chain.subperiods:
        if subperiod.ret is None:
            continue
        growth *= 1.0 + subperiod.ret
        dates.append(subperiod.date)
        index.append(growth)

    peak_value, peak_position = index[0], 0
    max_drawdown, trough_position, episode_peak = 0.0, 0, 0
    for position, value in enumerate(index):
        if value > peak_value:
            peak_value, peak_position = value, position
        drop = value / peak_value - 1.0
        if drop < max_drawdown:
            max_drawdown, trough_position, episode_peak = drop, position, peak_position

    episode_peak_value = index[episode_peak]
    recovery = next(
        (p for p in range(trough_position + 1, len(index)) if index[p] >= episode_peak_value),
        None,
    )
    episode_end = recovery if recovery is not None else len(index) - 1
    current = index[-1] / max(index) - 1.0

    return DrawdownResult(
        max_drawdown=GatedValue(max_drawdown, n, limit, None),
        current_drawdown=GatedValue(current, n, limit, None),
        trough_date=dates[trough_position],
        peak_date=dates[episode_peak],
        trough_days=trough_position - episode_peak,
        recovery_days=episode_end - episode_peak,
        ongoing=recovery is None and max_drawdown < 0,
    )


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------


def align_series(
    portfolio: Mapping[str, float], benchmark: Mapping[str, float]
) -> AlignedPairs:
    """Inner join and nothing else. Never zero-fills, never carries a previous."""
    common = sorted(set(portfolio) & set(benchmark))
    return AlignedPairs(
        dates=tuple(common),
        portfolio=tuple(float(portfolio[d]) for d in common),
        benchmark=tuple(float(benchmark[d]) for d in common),
        n_common=len(common),
    )


def returns_from_closes(closes: Mapping[str, float]) -> Dict[str, float]:
    """Consecutive-close returns over the series' OWN dates. No gap filling."""
    ordered = sorted(closes)
    out: Dict[str, float] = {}
    for previous, current in zip(ordered, ordered[1:]):
        base = float(closes[previous])
        if base <= 0 or not _is_finite(base) or not _is_finite(closes[current]):
            continue
        out[current] = float(closes[current]) / base - 1.0
    return out


def build_benchmark_block(
    pairs: AlignedPairs,
    rf_daily: float,
    *,
    n_returns: int,
    symbol: str,
    basis: str,
    min_n: Optional[int] = None,
    min_coverage: Optional[float] = None,
) -> Tuple[Optional[BenchmarkBlock], Optional[str]]:
    """A complete block or a reason. Never partial, never both."""
    limit = twr_gates.MIN_N_BENCHMARK if min_n is None else min_n
    coverage_floor = (
        twr_gates.BENCHMARK_MIN_COVERAGE if min_coverage is None else min_coverage
    )

    if pairs.n_common < 2:
        return None, "benchmark_unavailable"
    if pairs.n_common < limit:
        return None, "insufficient_n"
    coverage = pairs.n_common / n_returns if n_returns else 0.0
    if coverage < coverage_floor:
        return None, "benchmark_coverage"
    if statistics.stdev(pairs.benchmark) == 0:
        return None, "benchmark_degenerate"
    if statistics.stdev(pairs.portfolio) == 0:
        # A dormant account has no variance, so beta and correlation are
        # undefined on this side too. `statistics.correlation` raises on it, and
        # blaming SPY for a flat portfolio would be the wrong copy.
        return None, "not_computed"

    portfolio = list(pairs.portfolio)
    benchmark = list(pairs.benchmark)
    beta = statistics.covariance(portfolio, benchmark) / statistics.variance(benchmark)
    mean_excess_portfolio = statistics.fmean(portfolio) - rf_daily
    mean_excess_benchmark = statistics.fmean(benchmark) - rf_daily
    alpha = (mean_excess_portfolio - beta * mean_excess_benchmark) * twr_gates.TRADING_DAYS
    correlation = statistics.correlation(portfolio, benchmark)
    active = [p - b for p, b in zip(portfolio, benchmark)]
    active_sd = statistics.stdev(active)

    return (
        BenchmarkBlock(
            symbol=symbol,
            n_common=pairs.n_common,
            coverage=coverage,
            benchmark_return=chain_returns(benchmark),
            beta=beta,
            alpha_annualized=alpha,
            correlation=correlation,
            r_squared=correlation**2,
            tracking_error=active_sd * _ANNUALIZATION,
            information_ratio=(
                statistics.fmean(active) / active_sd * _ANNUALIZATION if active_sd else 0.0
            ),
            basis=basis,
            low_confidence=pairs.n_common < twr_gates.RATIO_LOW_CONFIDENCE_N,
        ),
        None,
    )


# ---------------------------------------------------------------------------
# money-weighted return
# ---------------------------------------------------------------------------


def build_cashflow_vector(
    nav: Sequence[NavObservation], flows: Mapping[str, float]
) -> List[DatedCashflow]:
    """Investor-signed, date-unique, ascending. A flow on d_0 is inside N_0."""
    observations = _sorted_observations(nav)
    if not observations:
        return []

    dates = [o.date for o in observations]
    placed = place_flows(dates, flows).by_subperiod
    seed, last = observations[0], observations[-1]

    amounts: Dict[str, float] = {seed.date: -seed.nav}
    for flow_date, amount in placed.items():
        if flow_date == last.date:
            continue
        amounts[flow_date] = amounts.get(flow_date, 0.0) - amount
    terminal = last.nav - placed.get(last.date, 0.0)
    if last.date != seed.date:
        amounts[last.date] = amounts.get(last.date, 0.0) + terminal

    return [DatedCashflow(date=d, amount=amounts[d]) for d in sorted(amounts)]


def modified_dietz(
    begin_nav: float,
    end_nav: float,
    dated_flows: Sequence[Tuple[str, float]],
    period_start: str,
    period_end: str,
    *,
    n_returns: Optional[int] = None,
    min_n: Optional[int] = None,
) -> GatedValue:
    limit = twr_gates.MIN_N_MWR if min_n is None else min_n
    n = 0 if n_returns is None else n_returns
    if n_returns is not None and n_returns < limit:
        return GatedValue(None, n, limit, "insufficient_n")

    span = _days_between(period_start, period_end)
    if span <= 0:
        return GatedValue(None, n, limit, "degenerate")

    total_flow = 0.0
    weighted_flow = 0.0
    for flow_date, amount in dated_flows:
        elapsed = _days_between(period_start, flow_date)
        total_flow += amount
        weighted_flow += (span - elapsed) / span * amount

    denominator = begin_nav + weighted_flow
    if denominator == 0:
        return GatedValue(None, n, limit, "degenerate")
    return GatedValue((end_nav - begin_nav - total_flow) / denominator, n, limit, None)


def _npv(cashflows: Sequence[DatedCashflow], rate: float, origin: str) -> float:
    total = 0.0
    for cashflow in cashflows:
        tau = _days_between(origin, cashflow.date) / twr_gates.DAYS_PER_YEAR
        total += cashflow.amount * (1.0 + rate) ** (-tau)
    return total


def _sign_changes(amounts: Sequence[float]) -> int:
    signs = [1 if a > 0 else -1 for a in amounts if a != 0]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def xirr(
    cashflows: Sequence[DatedCashflow],
    calendar_days: int,
    n_returns: int,
    *,
    min_n: Optional[int] = None,
) -> XirrResult:
    """Act/365 IRR by bisection on a proven bracket. Newton is never used."""
    limit = twr_gates.MIN_N_MWR if min_n is None else min_n
    ordered = sorted(cashflows, key=lambda cf: cf.date)
    multiple = _sign_changes([cf.amount for cf in ordered]) > 1

    if len({cf.date for cf in ordered}) < 2:
        return XirrResult(None, "insufficient_dates", multiple, 0)
    if all(cf.amount == 0 for cf in ordered):
        return XirrResult(None, "degenerate", multiple, 0)
    if all(cf.amount <= 0 for cf in ordered):
        return XirrResult(_TOTAL_LOSS_RETURN, "total_loss", multiple, 0)
    if calendar_days < twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED:
        return XirrResult(None, "period_lt_1y", multiple, 0)
    if n_returns < limit:
        return XirrResult(None, "insufficient_n", multiple, 0)

    origin = ordered[0].date
    bracket = _outer_bracket(ordered, origin)
    if bracket is not None:
        return _bisect(ordered, origin, bracket, multiple)

    roots = [
        _bisect(ordered, origin, inner, multiple)
        for inner in _interior_brackets(ordered, origin)
    ]
    solved = [r for r in roots if r.value is not None]
    if not solved:
        return XirrResult(None, "no_sign_change", multiple, 0)
    return min(solved, key=lambda r: abs(r.value or 0.0))


def _outer_bracket(
    cashflows: Sequence[DatedCashflow], origin: str
) -> Optional[Tuple[float, float]]:
    low, high = _XIRR_LOWER_BOUND, _XIRR_UPPER_SEED
    npv_low, npv_high = _npv(cashflows, low, origin), _npv(cashflows, high, origin)
    while npv_low * npv_high > 0 and high < _XIRR_UPPER_CAP:
        high *= 2.0
        npv_high = _npv(cashflows, high, origin)
    return None if npv_low * npv_high > 0 else (low, high)


def _interior_brackets(
    cashflows: Sequence[DatedCashflow], origin: str
) -> List[Tuple[float, float]]:
    """Sign changes inside the domain, found by scan.

    A stream with two real roots has the same NPV sign at both ends of the
    domain; widening upward never finds it. §D.3 requires solving anyway.
    """
    brackets: List[Tuple[float, float]] = []
    steps = int((_XIRR_UPPER_SEED - _XIRR_LOWER_BOUND) / _XIRR_SCAN_STEP)
    low = _XIRR_LOWER_BOUND
    npv_low = _npv(cashflows, low, origin)
    for step in range(1, steps + 1):
        high = _XIRR_LOWER_BOUND + step * _XIRR_SCAN_STEP
        npv_high = _npv(cashflows, high, origin)
        if npv_low * npv_high <= 0:
            brackets.append((low, high))
        low, npv_low = high, npv_high
    return brackets


def _bisect(
    cashflows: Sequence[DatedCashflow],
    origin: str,
    bracket: Tuple[float, float],
    multiple: bool,
) -> XirrResult:
    low, high = bracket
    npv_low = _npv(cashflows, low, origin)
    for iteration in range(1, _XIRR_MAX_ITERATIONS + 1):
        middle = (low + high) / 2.0
        npv_middle = _npv(cashflows, middle, origin)
        if npv_middle == 0.0 or (high - low) < _XIRR_WIDTH_TOLERANCE:
            return XirrResult(middle, None, multiple, iteration)
        if npv_low * npv_middle <= 0:
            high = middle
        else:
            low, npv_low = middle, npv_middle
    return XirrResult(None, "no_convergence", multiple, _XIRR_MAX_ITERATIONS)

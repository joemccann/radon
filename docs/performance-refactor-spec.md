# Performance / TWR Refactor Specification

Status: authoritative. Implementers follow this literally.
Supersedes the ad-hoc math currently in `scripts/perf_twr_builder.py` and `scripts/portfolio_performance.py`.
Written against the confirmed live defect (production Turso `performance_snapshots`, `taken_at 2026-08-15T14:55:40Z`) in which every `twr_subperiods[].c` was `0.0`, a ~725k transfer was chained as a +294.07% daily return, total TWR rendered +951.28% and annualized TWR rendered +3,288,954.62%, all under `status: "ok"` with `warnings: []` and a five-month-old `nav_source: "disk_cache"`.

---

## 0. Root cause, stated once

| # | Defect | Location today |
|---|---|---|
| 1 | Failed external-flow fetch returns `{}` and is indistinguishable from "no deposits" | `perf_twr_builder.py:736-746` |
| 2 | Flow classification is an inline substring match; `is_external_flow_type` / `EXTERNAL_FLOW_TYPES` are dead, test-only, divergent code | `perf_twr_builder.py:53-65` vs `:576-609` |
| 3 | `<Transfer>` rows read `transferPrice` (a per-share price) as a cash amount, ignore `direction`, ignore `positionAmountInBase` — so the 2026-02-06 ACATS is invisible | `perf_twr_builder.py:602-608` |
| 4 | Annualization has no `total_return <= -1` guard and no plausibility cap; the `365/D` (today `252/N`) exponent turns a contaminated `cum_r` into astronomy | `perf_twr_builder.py:124-128` |
| 5 | A disk cache of unbounded age is served with `status: "ok"` and no warning | `perf_twr_builder.py:612-656, 707-733, 849-852` |
| 6 | Benchmark alignment zero-fills a missing date **and** sets `prev = None`, poisoning the next day too; the benchmark series is then never serialized, so β/α/TE render from a series the chart cannot draw | `perf_twr_builder.py:387-399, 405-414` |
| 7 | `mwr_irr` is never computed; the UI blames the N gate for a missing field | `PerformancePanel.tsx:351-368` |
| 8 | Gate constants are triplicated (Python, `performanceTwr.ts`, inline literals in both panels) and already disagree with the on-screen copy | see §A.4 |
| 9 | `perf_twr_builder.py` has no caller; FastAPI `POST /performance` runs the other builder, which does raw NAV pct-change with zero flow handling | `scripts/api/server.py:3909-3915`, `portfolio_performance.py:553-558` |
| 10 | Two writers, two `taken_at` shapes, one table; the date-only row always loses to the ISO row lexicographically | `perf_twr_builder.py:802`, `portfolio_performance.py:1466`, `scan_mirror.py:118` |

Everything below exists to make each of these structurally impossible, not merely fixed.

---

## A. MODULE LAYOUT

### A.1 New modules

```
scripts/lib/twr_math.py        PURE. stdlib only. No I/O, no Flex, no Turso, no env, no numpy, no pandas.
scripts/lib/flex_flows.py      PURE. stdlib only (xml.etree). Takes XML *strings*, never sockets.
scripts/lib/twr_gates.py       PURE. The single gate table. Imported by both modules above.
```

`scripts/perf_twr_builder.py` retains **only**: env/config resolution, Flex HTTP, Yahoo/benchmark HTTP, disk cache read/write, Turso persistence, payload assembly, CLI. It imports every formula. It contains zero arithmetic beyond calling the pure layer.

Allowed imports in the three pure modules: `math`, `statistics`, `datetime`, `dataclasses`, `enum`, `typing`, `xml.etree.ElementTree` (flex_flows only). Nothing else. A test asserts this by AST-scanning the module's import nodes.

Rationale for stdlib-only: every worked value in §E is hand-computable and must be reproducible without a numeric library's percentile/ddof conventions leaking into the contract.

### A.2 Deleted

| Delete | Reason |
|---|---|
| `perf_twr_builder.EXTERNAL_FLOW_TYPES`, `NON_FLOW_TYPES`, `is_external_flow_type` | Moved to `flex_flows` as the single classifier |
| `perf_twr_builder.consolidate_accounts` | Test-only duplicate of the inline `build_payload:288-318` logic; replaced by `twr_math.consolidate_accounts` |
| `perf_twr_builder.twr_return`, `compute_annualized_return`, and the whole metrics block | Moved to `twr_math` |
| `portfolio_performance._extract_cash_flows`, `_extract_acats_transfers` | Zero callers |
| `portfolio_performance.build_nav_based_curve`'s `data/ib_twr_series.json` override (`:563-609`) | Untracked scrape file silently rewrites every published number |
| `migrate_perf_twr._parse_flex_xml` | Third divergent flow parser; call `flex_flows` |
| skew, kurtosis, tail_ratio from the payload | Computed, never read, never rendered |

### A.3 `scripts/lib/twr_gates.py` — the single gate table

This file is the only place a threshold is written in Python. It is mechanically mirrored to TypeScript (§A.5).

```python
"""Gate thresholds for the performance stack. Mirrored in web/lib/performanceTwr.ts.
Any edit here MUST be reflected there; tests/test_twr_gate_parity.py enforces it."""

TRADING_DAYS: int = 252            # sessions/yr — volatility & ratio scaling ONLY
DAYS_PER_YEAR: float = 365.0       # Act/365 Fixed — return annualization & XIRR ONLY

MIN_N_CHAIN: int = 1               # cum_twr
MIN_N_DISPERSION: int = 20         # volatility, max_drawdown, var_95, cvar_95, hit_rate, best/worst
MIN_N_BENCHMARK: int = 40          # beta, alpha, correlation, r_squared, tracking_error,
                                   #   information_ratio, benchmark_return  (n_common, not n_returns)
MIN_N_RATIO: int = 60              # sharpe, sortino
MIN_N_MWR: int = 20                # mwr_period and mwr_annualized
MIN_DOWNSIDE_OBSERVATIONS: int = 5 # additional sortino condition
MIN_CALENDAR_DAYS_ANNUALIZED: int = 365   # GIPS: never annualize a sub-year period
BENCHMARK_MIN_COVERAGE: float = 0.90      # n_common / n_returns
VAR_LOW_CONFIDENCE_N: int = 100           # below this, var/cvar carry low_confidence: true
RATIO_LOW_CONFIDENCE_N: int = 252         # below this, every ratio carries low_confidence: true

SUSPECT_RETURN_THRESHOLD: float = 0.50    # |r_t| above this => quarantine, whatever C_t is
UNEXPLAINED_OUTLIER_MULTIPLE: float = 5.0 # ...or above this many median sessions (§B.4.1)
UNEXPLAINED_TAIL_QUANTILE: float = 0.95   # the account's own normal extreme session
UNEXPLAINED_TAIL_MULTIPLE: float = 3.0    # ...or this many p95 sessions, whichever is wider
UNEXPLAINED_ABSOLUTE_FLOOR: float = 0.10  # the dispersion bar never falls below this,
                                          #   and never switches itself off
FLOW_DOMINANT_RATIO: float = 0.25         # |C_t| / denominator above this => flag the subperiod
                                          #   DISCLOSURE ONLY. It never exempts a quarantine
                                          #   and never moves the status (§B.4).
IMPLAUSIBLE_ANNUALIZED: float = 10.0      # |annualized| > 1000%/yr => degraded
IMPLAUSIBLE_ALPHA: float = 1.0            # |alpha_annualized| > 100%/yr => degraded
MAX_SUBPERIOD_GAP_DAYS: int = 4           # Fri->Mon is 3; >4 is a missing session
NAV_STALENESS_BUDGET_SESSIONS: int = 2    # Flex settles T+1; 2 sessions of slack.
                                          #   Counted per §C.2's one definition of
                                          #   sessions_behind, and re-derived at READ
                                          #   time, never trusted from the payload.
FLOW_CONVENTION: str = "bod"              # see §B.3
SORTINO_TARGET: float = 0.0
```

#### Deliberate threshold changes from today's behavior

| Metric | Was | Now | Why |
|---|---|---|---|
| Sharpe | 20 (UI only; Python computed at n>=2) | **60** | SE of an annualized Sharpe is ~`sqrt(252/N)`; at N=20 that is ±3.5, larger than any plausible estimate. |
| Sortino | ungated | **60 + >=5 downside observations** | A downside deviation from under 5 points is a single-observation artifact. |
| VaR / CVaR | 60 | **20** (low_confidence below 100) | `k = floor(0.05N) >= 1` requires N>=20. 60 was arbitrary; the honest signal is `low_confidence`, not a dash. |
| Annualized return | `n_returns >= 20` | **`calendar_days >= 365`** | GIPS forbids annualizing sub-year returns. This single change kills the +3,288,954% render. |
| Beta / alpha / TE / IR | `n_returns >= 40` | **`n_common >= 40` + coverage >= 0.90 + `stdev(bench) > 0`** | The gate must be on the *aligned* sample, not the portfolio's. |
| Subperiod quarantine | `\|r_t\| > 0.50` **and** `C_t == 0` | **`\|r_t\| > 0.50` or `\|r_t\| > outlier_bar`, with no `C_t` term at all** | `r_t` already nets the recorded flow out; testing `C_t` again re-admits it. Every flow-shaped exemption was defeated by supplying a flow (§B.4). |
| Dispersion bar | `None` below `MIN_N_DISPERSION` or on a zero median | **always a number; falls back to `UNEXPLAINED_ABSOLUTE_FLOOR` (0.10)** | A bar that switches itself off publishes a young or dormant account's unexplained deposit as `ok` (§B.4.1). |
| Total-loss exemption | unconditional on the final subperiod (`all([])`) | **only a recorded matching withdrawal** | `all([])` is `True`, so `-100.00%` rendered as a confident hero (§B.4.2). |

All on-screen gate copy (`PerformancePanel.tsx:664`, `:790`, `MobilePerformancePanel.tsx:777`, and the 16 inline restatements) is deleted and regenerated from the table (§C.6). No prose threshold is hand-typed anywhere.

### A.4 `scripts/lib/twr_math.py` — complete public surface

Sign conventions, normative everywhere in this document and in the payload:

- `C > 0` = capital **into** the account (deposit, transfer in). `C < 0` = out.
- Returns, alpha, VaR, CVaR, drawdowns are **signed decimals**. A loss is negative. No magnitudes.
- MWR cashflows use the **investor's** sign: money the investor commits is negative. This is the opposite of `C`. The conversion happens in exactly one function (`build_cashflow_vector`).

#### A.4.1 Value objects (all `@dataclass(frozen=True)`)

```python
class FlowClass(enum.Enum):
    EXTERNAL = "external"     # investor capital; subtract from return
    INTERNAL = "internal"     # account earnings/costs; DO NOT subtract
    # there is no UNKNOWN member: an unrecognized type raises

class FlowsStatus(enum.Enum):
    OK = "ok"                     # fetched, at least one external flow found
    EMPTY_VERIFIED = "empty_verified"   # fetched successfully, genuinely zero external flows
    FAILED = "failed"             # fetch or parse failed; TWR must not be published

@dataclass(frozen=True)
class NavObservation:
    date: str        # "YYYY-MM-DD", ISO, no time component
    nav: float       # total equity, base currency, close of `date`

@dataclass(frozen=True)
class FlowSet:
    status: FlowsStatus
    by_date: Mapping[str, float]   # date -> NET external flow, signed, base currency
    source: str                    # "flex_cash_transactions+transfers" | "turso_cash_flows" | ""
    # Constructor invariant, asserted: status is FAILED  <=>  by_date is empty AND unusable.
    # A FailedFlowSet cannot be built from a swallowed exception because
    # `FlowSet.failed(reason)` is the ONLY way to produce status=FAILED and it
    # requires a non-empty reason string.

@dataclass(frozen=True)
class Subperiod:
    date: str                # d_t, the END date of the subperiod
    begin_nav: float         # B_t = N_{t-1}
    end_nav: float           # E_t = N_t
    flow: float              # C_t, signed
    denominator: float       # B_t. NOTE: still B_t, not the BOD denominator B_t + C_t
    ret: float | None        # r_t; None when the subperiod is skipped or quarantined
    cum_ret: float | None    # chained cum through this subperiod; None when excluded
    gap_days: int            # calendar days from d_{t-1} to d_t
    flags: tuple[str, ...]   # subset of: "zero_base","negative_base","suspect","flow_dominant","nav_gap"
    skip_reason: str | None  # None | "zero_base" | "negative_base" | "suspect_no_flow"

@dataclass(frozen=True)
class SubperiodChain:
    subperiods: tuple[Subperiod, ...]
    returns: tuple[float, ...]       # r_t for INCLUDED subperiods only, in date order
    cum_return: float                # PI(1+r) - 1 over `returns`
    n_nav_observations: int
    n_subperiods: int                # == n_nav_observations - 1, always
    n_returns: int                   # == len(returns) <= n_subperiods
    period_start: str                # d_0, the seed date
    period_end: str                  # d_M
    calendar_days: int               # (d_M - d_0).days
    excluded_suspect: bool           # True if any subperiod was quarantined

@dataclass(frozen=True)
class GatedValue:
    value: float | None
    n: int
    min_n: int
    unavailable_reason: str | None   # None iff value is not None
    low_confidence: bool = False
    # Invariant, asserted in __post_init__: (value is None) == (unavailable_reason is not None)

@dataclass(frozen=True)
class DrawdownResult:
    max_drawdown: GatedValue         # <= 0
    current_drawdown: GatedValue     # <= 0, from the SAME return index, never from dollar NAV
    trough_date: str | None
    peak_date: str | None
    trough_days: int | None          # sessions peak -> trough of the deepest episode
    recovery_days: int | None        # longest peak -> full-recovery span; None if never recovered
    ongoing: bool

@dataclass(frozen=True)
class AlignedPairs:
    dates: tuple[str, ...]
    portfolio: tuple[float, ...]
    benchmark: tuple[float, ...]
    n_common: int

@dataclass(frozen=True)
class BenchmarkBlock:
    symbol: str
    n_common: int
    coverage: float
    benchmark_return: float          # PI(1+b_t)-1 over the ALIGNED dates only
    beta: float
    alpha_annualized: float
    correlation: float
    r_squared: float
    tracking_error: float
    information_ratio: float
    basis: str                       # "price_return" | "total_return"
    low_confidence: bool
    # There is no partially-populated BenchmarkBlock. Every field is non-None by type.

@dataclass(frozen=True)
class DatedCashflow:
    date: str
    amount: float    # investor sign: negative = investor pays in

@dataclass(frozen=True)
class XirrResult:
    value: float | None
    unavailable_reason: str | None   # None | "insufficient_dates" | "degenerate" | "no_sign_change"
                                     #      | "no_convergence" | "period_lt_1y" | "insufficient_n"
    multiple_sign_changes: bool
    iterations: int
```

#### A.4.2 Functions

| Function | Signature | Units / sign | Returns |
|---|---|---|---|
| `daily_risk_free` | `(rf_annual: float) -> float` | `rf_annual` decimal/yr | daily decimal, **geometric**: `(1+rf)**(1/252) - 1` |
| `classify_flow_type` | `(raw_type: str) -> FlowClass` | IBKR `type` string, case- and whitespace-normalized | `FlowClass`; **raises `UnknownFlowType`** otherwise |
| `subperiod_return` | `(begin_nav: float, end_nav: float, flow: float) -> float \| None` | all base currency; `flow` signed `C` | `r_t` decimal, or `None` when `begin_nav <= 0` or any input non-finite |
| `build_subperiods` | `(nav: Sequence[NavObservation], flows: Mapping[str, float]) -> SubperiodChain` | `nav` any order, duplicates **raise** `DuplicateNavDate` | full chain, sorted ascending |
| `chain_returns` | `(returns: Sequence[float]) -> float` | decimals | `PI(1+r) - 1`; `0.0` for an empty sequence |
| `annualize_return` | `(cum_return: float, calendar_days: int) -> GatedValue` | Act/365 | see §1.4 guards |
| `volatility` | `(returns: Sequence[float]) -> GatedValue` | decimals | `stdev(ddof=1) * sqrt(252)`, `>= 0` |
| `sharpe_ratio` | `(returns: Sequence[float], rf_daily: float) -> GatedValue` | decimals | dimensionless |
| `sortino_ratio` | `(returns: Sequence[float], rf_daily: float, target: float = 0.0) -> GatedValue` | decimals | dimensionless |
| `downside_deviation` | `(returns: Sequence[float], target: float = 0.0) -> GatedValue` | decimals | annualized, `>= 0` |
| `drawdowns` | `(chain: SubperiodChain) -> DrawdownResult` | uses the **return index**, never dollar NAV | all values `<= 0` |
| `historical_var` | `(returns: Sequence[float], level: float = 0.05) -> GatedValue` | decimals | signed 5th percentile |
| `conditional_var` | `(returns: Sequence[float], level: float = 0.05) -> GatedValue` | decimals | mean of the `k` worst |
| `return_distribution` | `(returns: Sequence[float]) -> dict[str, GatedValue]` | decimals | `hit_rate`, `best_day`, `worst_day`, `average_up_day`, `average_down_day`, `win_loss_ratio`, `positive_days`, `negative_days`, `flat_days` |
| `align_series` | `(portfolio: Mapping[str, float], benchmark: Mapping[str, float]) -> AlignedPairs` | date-keyed returns | **inner join only.** Never zero-fills. Never carries `prev`. |
| `build_benchmark_block` | `(pairs: AlignedPairs, rf_daily: float, n_returns: int, symbol: str, basis: str) -> tuple[BenchmarkBlock \| None, str \| None]` | — | block **or** `(None, reason)`. Never both, never partial. |
| `build_cashflow_vector` | `(nav: Sequence[NavObservation], flows: Mapping[str, float]) -> list[DatedCashflow]` | converts `C` to investor sign | unique ascending dates |
| `xirr` | `(cashflows: Sequence[DatedCashflow], calendar_days: int, n_returns: int) -> XirrResult` | Act/365 | annualized decimal |
| `modified_dietz` | `(begin_nav: float, end_nav: float, dated_flows: Sequence[tuple[str, float]], period_start: str, period_end: str) -> GatedValue` | `C` sign | non-annualized period return |
| `consolidate_accounts` | `(per_account_nav: Mapping[str, Sequence[NavObservation]], per_account_flows: Mapping[str, Mapping[str, float]]) -> tuple[list[NavObservation], dict[str, float], list[str]]` | — | summed NAV/flows plus a list of dates where an account was missing (each becomes an `NAV_ACCOUNT_GAP` warning; the date is **dropped**, never summed short) |

Exceptions defined in `twr_math`: `UnknownFlowType`, `DuplicateNavDate`, `NonFiniteInput`. All three are caught exactly once, at the builder boundary (§C.2), and converted to warnings + a non-`ok` status. None of them may be caught inside the pure layer.

### A.5 TypeScript mirror — `web/lib/performanceTwr.ts`

The file is rewritten so the constants block is machine-comparable. Exact required shape (the parity test parses this literally):

```ts
/** MIRROR OF scripts/lib/twr_gates.py — DO NOT EDIT ONE WITHOUT THE OTHER.
 *  tests/test_twr_gate_parity.py fails the build on drift. */
export const TWR_GATES = {
  TRADING_DAYS: 252,
  DAYS_PER_YEAR: 365,
  MIN_N_CHAIN: 1,
  MIN_N_DISPERSION: 20,
  MIN_N_BENCHMARK: 40,
  MIN_N_RATIO: 60,
  MIN_N_MWR: 20,
  MIN_DOWNSIDE_OBSERVATIONS: 5,
  MIN_CALENDAR_DAYS_ANNUALIZED: 365,
  BENCHMARK_MIN_COVERAGE: 0.9,
  VAR_LOW_CONFIDENCE_N: 100,
  RATIO_LOW_CONFIDENCE_N: 252,
  SUSPECT_RETURN_THRESHOLD: 0.5,
  UNEXPLAINED_OUTLIER_MULTIPLE: 5,
  UNEXPLAINED_TAIL_QUANTILE: 0.95,
  UNEXPLAINED_TAIL_MULTIPLE: 3,
  UNEXPLAINED_ABSOLUTE_FLOOR: 0.1,
  FLOW_DOMINANT_RATIO: 0.25,
  IMPLAUSIBLE_ANNUALIZED: 10,
  IMPLAUSIBLE_ALPHA: 1,
  MAX_SUBPERIOD_GAP_DAYS: 4,
  NAV_STALENESS_BUDGET_SESSIONS: 2,
} as const;
```

Enforcement — `tests/test_twr_gate_parity.py`:

1. Read `web/lib/performanceTwr.ts`, extract the `TWR_GATES` object literal with a regex over `KEY: value,` lines.
2. Import `scripts.lib.twr_gates`, collect every module-level `UPPER_SNAKE` name whose value is `int | float`.
3. Assert the two key sets are identical and every value compares equal as `float`.
4. Assert `FLOW_CONVENTION`, `SORTINO_TARGET` are present in Python and, being non-numeric, are exported from TS as `TWR_CONVENTIONS`.

`performanceTwr.ts` additionally exports **one** gate-evaluation helper per metric family and **one** copy generator:

```ts
export type GatedValue = { value: number | null; n: number; min_n: number;
                           unavailable_reason: string | null; low_confidence?: boolean };

export function gateCopy(g: GatedValue, label: string): string;
// reason -> copy, EXHAUSTIVE switch (TS `never` check on the default branch):
//   "insufficient_n"        -> `needs ${g.min_n} sessions (N=${g.n})`
//   "period_lt_1y"          -> "needs 1 year of history"
//   "no_downside"           -> "no losing sessions yet"
//   "benchmark_unavailable" -> "SPY series unavailable"
//   "benchmark_coverage"    -> `SPY covers ${pct} of sessions`
//   "benchmark_degenerate"  -> "SPY series has no variance"
//   "not_computed"          -> `${label} not computed`
//   "no_flow_data"          -> "external flows unavailable"
//   "no_convergence"        -> "did not converge"
//   "no_sign_change"        -> "no net capital at risk"
//   "degenerate"            -> "no cash flows"
//   "insufficient_dates"    -> "needs 2 dated cash flows"
//   "total_loss"            -> "-100%"
```

`PerformancePanel.tsx` and `MobilePerformancePanel.tsx` MUST import `TWR_GATES` and `gateCopy`. Every inline `20` / `40` / `60` literal and every hand-typed "needs N sessions" string in those two files is deleted. A vitest test greps both panel files and fails on any bare `>= 20 | >= 40 | >= 60` or the substring `sessions (N=`.

---

## B. FLOW HANDLING — the correctness core

### B.1 Canonical classification (`scripts/lib/flex_flows.py`)

Classification is an **exact-match allowlist on the normalized `type` field**. Normalization: `" ".join(raw.split()).strip().casefold()`. Substring matching is banned — it cannot distinguish a `type` attribute from free-text `description`, and it fails open.

```python
EXTERNAL_FLOW_TYPES = frozenset({
    "deposits/withdrawals",
    "deposits & withdrawals",
    "deposits and withdrawals",
    "internal transfers",
    "internal transfer",
})

INTERNAL_FLOW_TYPES = frozenset({
    "dividends",
    "payment in lieu of dividends",
    "withholding tax",
    "broker interest paid",
    "broker interest received",
    "broker fees",
    "bank interest paid",
    "bank interest received",
    "bond interest paid",
    "bond interest received",
    "other fees",
    "advisor fees",
    "commission adjustments",
    "price adjustments",
    "cash fx translation gain/loss",
    "detail",
})
```

`classify_flow_type(raw)` returns `FlowClass.EXTERNAL` / `FlowClass.INTERNAL`, and **raises `UnknownFlowType(raw)`** for anything else. There is no third "not a flow" outcome, because "unrecognized" and "internal" must never collapse into the same behavior — that collapse is defect #2.

Verified against production: live Turso `cash_flows` holds 261 rows across exactly four `raw_type` values — `Deposits/Withdrawals` (24), `Other Fees` (207), `Broker Interest Received/Paid` (25), `Dividends` + `Payment In Lieu Of Dividends` (5). The allowlist covers all four. Adding an IBKR type is a one-line, reviewed change; silently misclassifying one is not possible.

`parse_flows_entries` and `is_external_flow_type` are the same code path by construction: `is_external_flow_type(raw)` is defined as `classify_flow_type(raw) is FlowClass.EXTERNAL`, and `parse_flows_entries` calls `classify_flow_type` directly. Neither may contain its own predicate. A test asserts `flex_flows` contains exactly one `frozenset` of external types and that `perf_twr_builder` defines none.

### B.2 The `<Transfer>` section — where the 725k lives

The 2026-02-06 event (NAV `246,713.50 -> 972,215.53`, delta `+725,502.03`) has **no `CashTransaction` row at all** in production. It is a securities transfer. The current `<Transfer>` handler reads `@cashTransfer` (0 for an in-kind transfer) then falls back to `@transferPrice` — a **per-share price** — and ignores `@direction` entirely.

Required handling for each `<Transfer>` element:

```
raw_type   = @type            # "ACATS" | "FOP" | "INTERNAL" | ...
direction  = @direction       # "IN" | "OUT"    -- REQUIRED; missing => UnknownFlowType
category   = @assetCategory   # "STK" | "OPT" | "CASH" | ...
report_date= @reportDate or @date   # normalized to YYYY-MM-DD

if category == "CASH":
    magnitude = abs(float(@cashTransfer))
else:
    magnitude = abs(float(@positionAmountInBase))        # base currency, market value at transfer
    if @positionAmountInBase missing/blank:
        magnitude = abs(float(@positionAmount))
    if still missing:
        raise UnknownFlowType(f"transfer:{raw_type}:no_amount")

amount = +magnitude if direction == "IN" else -magnitude
```

`@transferPrice` is **never** read as an amount. `@quantity * @transferPrice` is not used either (currency and multiplier ambiguity); `positionAmountInBase` is the only sanctioned value.

All `<Transfer>` rows are EXTERNAL by definition — a position moving between custodians is investor capital, not investment return. `<Transfer type="INTERNAL">` between two accounts of the same consolidated group is netted out by `consolidate_accounts` before it reaches `build_subperiods` (it appears as `+X` in one account and `-X` in the other on the same date).

### B.3 Flow-timing convention: **BEGINNING OF DAY (BOD)**

Declared in the payload as `methodology.flow_convention: "bod"`.

```
r_t = (E_t - C_t - B_t) / (B_t + C_t)          denominator = B_t + C_t
```

The denominator is the capital actually at work over the session: the pre-existing base plus the capital that arrived (or, for `C_t < 0`, less the capital that left).

Justification, specific to IBKR Flex `EquitySummaryByReportDateInBase`:

1. That report emits one NAV per report date, struck at the close. There is no intraday NAV, so no convention can be *derived*; one must be *asserted* and recorded.
2. A deposit dated `t` is inside `NAV(t)` and outside `NAV(t-1)`. That is all the data says.
3. **BOD is what IBKR PortfolioAnalyst reports**, and PortfolioAnalyst is the dashboard the operator reconciles against. A convention that cannot be checked against the broker's own number is not auditable.
4. An in-kind transfer is not idle cash. EOD pretends arriving securities earned nothing that session and credits the entire day's P&L to the pre-existing base alone. On the real 2026-02-06 ACATS that reads as +28.37% on paper against +7.76% on the capital that was actually deployed.

Confirmation on the operator's real YTD series (Flex `Equity Summary in Base`, 2025-12-31 .. 2026-08-14, 162 subperiods):

| Chain | 2026 YTD | 2026-02-05 | 2026-02-06 |
|---|---:|---:|---:|
| EOD | +121.09% | -20.87% | +28.37% |
| **BOD — chosen** | **+90.81%** | **-19.17%** | **+7.76%** |
| IBKR PortfolioAnalyst | +91.15% | **-19.17%** | — |

IBKR's single-session figure for 2026-02-05 is the BOD number to the basis point, which is the proof of convention. The 0.34pp YTD residual is IBKR running to 08-16 on real-time marks against our 08-14 Flex close, not a methodology gap.

Convention sensitivity on the live 2026-02-06 case (`B = 246,713.50`, `E = 972,215.53`, `C = +725,000`; true P&L `E - B - C = 502.03` under all three):

| Convention | Denominator | `r_t` | As % |
|---|---:|---:|---:|
| **BOD (`w=1`) — chosen** | 971,713.50 | **0.000516644** | **+0.0517%** |
| Dietz mid-day (`w=0.5`) | 609,213.50 | 0.000824063 | +0.0824% |
| EOD (`w=0`) — superseded | 246,713.50 | 0.002034870 | +0.2035% |
| Current (broken, `C` dropped) | 246,713.50 | 2.940666 | **+294.07%** |

The broken value reproduces the live `r=+2.9407` to 7 significant figures, which proves the defect is `C` forced to zero, not a convention error.

Denominator validity, which is also how the seed observation is handled:

- The first NAV observation produces **no** return. `M+1` observations yield **exactly `M`** subperiods. No synthetic leading `r=0` may be emitted. `n_subperiods == n_nav_observations - 1` is an asserted invariant.
- A subperiod is valid iff `B_t > 0` **and** `B_t + C_t > 0`.
- `B_t == 0` -> skip, `skip_reason="zero_base"`. "What return did zero dollars earn" is meaningless; a period cannot start with no capital, whatever arrived during it, and `0.0` is not the answer.
- `B_t < 0` -> skip, `skip_reason="negative_base"`.
- `B_t + C_t <= 0` with a **zero** residual is a full withdrawal that emptied the account. It earned nothing, chains harmlessly at `0.0`, and must not break the series. Only a non-positive denominator carrying a **non-zero** residual is undefined — P&L with no capital to have produced it — and that one skips.
- Any non-finite `B_t`, `E_t`, or `C_t` -> `NonFiniteInput`.

### B.4 Hard invariant: UNEXPLAINED subperiods are quarantined, never silently chained

**Interrogate the residual, and nothing else** (audit round 3, DECISION 1). Every earlier
version of this gate asked a question about the RECORDED FLOW — is `C_t` exactly zero, does
`|C_t|` cover a quarter of the move — and every version was defeated by supplying a flow:
one dollar on the ACATS date, then $182,000 of a $725,606 move. The mistake is structural.
`r_t = (E_t - C_t - B_t) / B_t` has ALREADY netted the recorded flow out, so `r_t` **is** the
unexplained residual. Asking about `C_t` a second time re-admits the money the formula just
removed.

```
unexplained   : |r_t| > SUSPECT_RETURN_THRESHOLD (0.50)
                OR |r_t| > outlier_bar
                -> the subperiod is EXCLUDED from `returns`,
                   skip_reason = "suspect_no_flow",
                   flags include "suspect",
                   chain.excluded_suspect = True,
                   payload status >= "degraded",
                   one SUBPERIOD_SUSPECT warning (severity error) per occurrence.

flow_dominant : |C_t| / denominator > FLOW_DOMINANT_RATIO (0.25)
                -> flag + FLOW_DOMINANT (info) only, still chained.
                   This is a DISCLOSURE, never an exemption. It has no effect on
                   the quarantine and no effect on status.
```

`C_t` appears nowhere in the quarantine test. A partially-recorded ACATS whose cash leg was
captured and whose `positionAmountInBase` leg was not still leaves the whole securities value
in `r_t`, and still quarantines.

#### B.4.1 The dispersion bar never disables itself (DECISION 2)

`SUSPECT_RETURN_THRESHOLD` alone is a 50% cliff, and the production series' own contamination
sits under it (`2026-01-26`, an unrecorded 42,995.41 deposit, is `+22.69%`). The second limb is
a bar drawn from the series' own dispersion:

```
magnitudes  = |r| over the returns that are not already past SUSPECT_RETURN_THRESHOLD
              (an extreme session must not raise the bar that judges it)
typical     = median(magnitudes)
tail        = p95(magnitudes)          # only when len >= MIN_N_DISPERSION (20)
outlier_bar = max(typical * UNEXPLAINED_OUTLIER_MULTIPLE (5.0),
                  tail    * UNEXPLAINED_TAIL_MULTIPLE    (3.0),
                  UNEXPLAINED_ABSOLUTE_FLOOR             (0.10))

magnitudes empty (a young account, a dormant account, a flat series)
            -> outlier_bar = UNEXPLAINED_ABSOLUTE_FLOOR
```

**Why the tail term exists, and what it costs.** A multiple of the median
describes a typical session, not an impossible one, so on any account with real
dispersion the median-only bar lands inside that account's own upper tail. On
the operator's real 162-session book: median |r| 0.0274 put the bar at 0.1368,
below the account's own p99 of 0.2787. Five ordinary sessions were quarantined
and the published return read +28.76% against a true +121.09% -- a 92-point
understatement on a book with ~1.1M of genuine trading P&L against 135k of net
external flows. p95 is what a normal extreme looks like for THIS account.

The tail term may only ever WIDEN the bar; the median term stays and the three
are combined with `max`. Below `MIN_N_DISPERSION` a quantile is noise, so the
tail term is not computed and the median term carries the estimate. Collapsing a
short sample straight to the floor is STRICTER than the median-only rule and
quarantines short, legitimately volatile series.

**The documented cost.** On the real series with NO flow data, the bar moves
from 0.1626 to 0.3238, so an unrecorded 42,995.41 deposit on 2026-01-26
(+22.69%) is no longer caught. That is irreducible: +22.69% is 2.1x this
account's p95 of 0.10794 and the account genuinely produces sessions that size.
A bar low enough to catch it quarantines ordinary trading days forever. The two
are the same magnitude and no threshold separates them.

Completeness of flow data is therefore enforced UPSTREAM rather than inferred
from return magnitude: a failed fetch is `FlowsStatus.FAILED` and a statement
missing the Transfers section raises `FLOWS_TRANSFERS_SECTION_ABSENT`, both of
which refuse to publish outright. The quarantine's remaining job is to catch
what no market produces, and `SUSPECT_RETURN_THRESHOLD` (0.50) remains an
absolute ceiling no tail estimate can license. Pinned in
`tests/test_perf_twr_residuals.py::test_r4_the_real_series_quarantines_what_no_market_produces`.

Three properties are load-bearing:

1. **The bar is never `None` and never zero.** Returning "no bar" below `MIN_N_DISPERSION` or on
   a zero median is what let a 15-session account publish an unexplained `+21.86%` deposit and a
   23-session flat account publish `+22.69%`, both under `status: "ok"` with `warnings: []`. An
   account with too little dispersion to describe itself is the LEAST trustworthy sample, not a
   reason to stop checking; the absolute floor stands in for the sample that is not there.
2. **The multiple is 5.0, not 10.0.** On the real production series `median(|r|) = 0.032526`, so
   a 10x bar sits at `0.325` and waves the real `2026-01-26` deposit (`0.22689`) straight
   through. At 5x the bar is `0.16263` and the deposit quarantines. The multiple is calibrated
   against the production series, not chosen for roundness, and §E.8's real-data fixture is what
   holds it there.
3. **`FLOW_DOMINANT_RATIO` is not in this formula.** Nothing about `C_t` enters the bar.

`UNEXPLAINED_OUTLIER_MULTIPLE` and `UNEXPLAINED_ABSOLUTE_FLOOR` are gate constants and live in
`twr_gates.py` with the rest of the table, mirrored to `TWR_GATES` in `performanceTwr.ts` per
§A.5.

#### B.4.2 A total loss is exempt only on evidence (DECISION 4)

`r_t == -1.0` — NAV to exactly zero — was exempted unconditionally, because the guard read
`all(o.nav <= 0 for o in observations[index+2:])` and that slice is EMPTY on the final
subperiod, where `all([])` is `True`. A blank `total` attribute, a `total="0"` row, or a real
full withdrawal all rendered a confident hero of `-100.00%`.

A total loss is exempt **only when a recorded external flow of matching magnitude explains it**,
i.e. a full withdrawal on file. Note what that means arithmetically: a matching withdrawal
produces `r_t = (0 - (-B) - B)/B = 0.0`, not `-1.0`, so the exempt case never reaches the
quarantine at all. `r_t == -1.0` with nothing on file therefore always quarantines, and
`_is_terminal_total_loss` is not a special case in the gate but a statement about the data:
with no withdrawal recorded, NAV going to zero is the single most suspect thing the series can
contain.

This supersedes §E.1 #11's `cum_return == -1.0` pin, which predates DECISION 4.
`annualize_return(-1.0, 730) -> -1.0, "total_loss"` is unchanged; what changed is that a
`-1.0` chain return is no longer reachable from an unexplained wipeout.

#### B.4.3 The inference, and the extreme that survives

Alongside each quarantine the builder emits an **inference, clearly labelled and never used in a published number**:

```
INFERRED_FLOW_CANDIDATE  date=2026-02-06  amount=+725502.03  (E_t - B_t; would make r_t ~ 0)
```

This restores the diagnostic value of the heuristic that `portfolio_performance._extract_acats_transfers` had (NAV jump > 50k with no fills and no deposit), without its silent-magic-threshold behavior. Applying an inferred flow requires an explicit `--allow-inferred-flows` CLI flag, and when used it stamps `methodology.inferred_flows: [...]` in the payload and forces `status: "degraded"`.

`SUBPERIOD_EXTREME` (severity **error**, DECISION 3) covers a chained subperiod whose `|r_t|`
is past `SUSPECT_RETURN_THRESHOLD`. Under DECISION 1 that combination is unreachable through
`build_payload` — such a subperiod quarantines first and its `ret` becomes `None` — so the code
is a backstop against a future chain path, and it is pinned as a unit test on
`_subperiod_warnings` + `_resolve_status` rather than through a payload. It is an `error`
because an extreme session that survives into the chain is a reason to distrust the number, not
an informational note; it floors the status at `degraded`.

Its message states what is actually on file. `"returned -100.00% with a recorded +0.00 flow"`
contradicted itself: a zero flow is the absence of a record, not a record.

### B.5 Flow dates with no NAV row

Today a flow on a date absent from the NAV series is silently dropped. Required: carry it forward to the **next** NAV observation on or after the flow date (that is the first NAV that contains the money), and emit `FLOW_DATE_SHIFTED` (info) with both dates. A flow after `d_M` is dropped with a `FLOW_AFTER_PERIOD_END` (info) warning. A flow before `d_0` is dropped silently — it is already inside `N_0`.

### B.6 Flows are a required input

`build_subperiods` takes a `Mapping[str, float]`, but `perf_twr_builder` may only obtain that mapping from a `FlowSet`. `FlowSet.status == FAILED` short-circuits before any math: the payload publishes `status: "degraded"`, `twr: null`, `summary: {}`, `series: []`, and the `FLOWS_FETCH_FAILED` warning. There is no code path from a caught exception to an empty-but-usable flow map.

`FlowSet.empty_verified()` is a distinct constructor, callable only after a successful fetch that parsed at least one `<CashTransaction>` or `<Transfer>` element (or a well-formed statement with a present-but-empty section). A parse that yields zero elements from a document lacking the section entirely is `FAILED`, reason `flows_section_absent` — because a NAV-only Flex query silently produces exactly that, and that is defect #1's second face.

---

## C. DATA-INTEGRITY GATES

### C.1 Status enum

```
"ok"                 every gate passed; numbers are publishable as-is
"stale"              math is sound but the NAV data is older than the freshness budget
"degraded"           at least one integrity gate failed; some or all numbers are suppressed
"insufficient_data"  fewer than 2 NAV observations
"unavailable"        no NAV series at all (no Flex, no disk, no Turso)
```

Precedence, evaluated in this order; the first match wins:

```
unavailable > insufficient_data > degraded > stale > ok
```

Any warning of severity `error` forces at least `degraded`. Any warning of severity `warn` forces at least `stale`.

### C.2 The three gates a payload must never pass with `status: "ok"`

**Gate 1 — external flows.** `status == "ok"` requires `flows.status in {OK, EMPTY_VERIFIED}`. `FAILED` => `degraded`, `twr: null`. `EMPTY_VERIFIED` publishes normally but stamps `flows_status: "empty_verified"` in the payload so an operator can see that "no deposits" was *observed*, not *assumed*.

**Gate 2 — NAV freshness.** `status == "ok"` requires `sessions_behind(period_end) <= NAV_STALENESS_BUDGET_SESSIONS`. It does **not** require `nav_source == "flex_live"`: revised 2026-08-17 after a cached NAV of `2026-08-14`, read on `2026-08-17`, floored the payload to `stale` and blanked every gated metric — even though IBKR is T+1 and a live fetch would have returned that exact date. Provenance and freshness are different questions and only the second may hide a number; age is separately policed by `_NAV_DISK_MAX_AGE_DAYS` and by the read layer re-deriving `sessionsBehind` from `nav_as_of`. `sessions_behind` uses `scripts/utils/market_calendar` (the same holiday source of truth as `marketCalendar.js`), not naive date subtraction. Any other combination:

| `nav_source` | sessions behind | status | warning |
|---|---|---|---|
| `flex_live` | <= 2 | `ok` | — |
| `flex_live` | > 2 | `stale` | `NAV_STALE` (warn) |
| `disk_cache` | <= 2 | `ok` | `NAV_SOURCE_DISK` (info) |
| `disk_cache` | > 2 | `degraded` | `NAV_SOURCE_DISK` + `NAV_STALE` (error) |
| `turso` | <= 2 | `ok` | `NAV_SOURCE_TURSO` (info) |
| `turso` | > 2 | `degraded` | `NAV_SOURCE_TURSO` + `NAV_STALE` (error) |
| none | — | `unavailable` | `NAV_UNAVAILABLE` (error) |

`load_nav_from_disk` additionally refuses to return a series whose `max(date)` is more than **30 calendar days** behind today; beyond that it returns `None` and the ladder falls through to Turso. The live payload served a 2026-03-20 series on 2026-08-15 — 148 days — under `status: "ok"`. That is the contract violation this gate closes.

The payload always carries `nav_source`, `nav_as_of` (= `period_end`), and `nav_sessions_behind`.

#### C.2.1 `sessions_behind`, defined once (DECISION 6)

> **Trading sessions strictly after `nav_as_of`, up to and including the last COMPLETED
> session.** The last completed session is the most recent session whose **16:00 ET** close has
> passed; weekends and NYSE holidays are not sessions.

Both off-by-one readings have shipped. `cursor <= today` counted the session now in progress
and turned a NAV inside the budget amber at any hour of the trading day; round 2 over-corrected
to `cursor < end`, which fails OPEN by one at every hour — the timer fires at 20:45 ET, after
the close, so `end` is a full session and dropping it under-reports staleness. At `nav_as_of =
2026-08-11` with `2026-08-14` complete the answer is **3**, past the budget, and `NAV_STALE`
must fire.

The boundary is a named function, `perf_twr_builder.last_completed_session(now)`, so the
default argument of `sessions_behind` and the TypeScript mirror share one definition of "now"
rather than each re-deriving it. Python and TypeScript are pinned case-for-case against one
shared table:

```
tests/fixtures/perf/sessions_behind_parity.json
  Python : tests/test_perf_twr_residuals.py::test_r7_sessions_behind_matches_the_shared_parity_table
  TS     : web/tests/performance-read-staleness.test.ts
```

Every case in that table was counted by hand off a 2026 calendar and carries its enumeration in
a `why` field. Adding a case means adding it once, for both stacks.

**Both sides read the same holiday table.** Python takes holidays from
`scripts/utils/market_calendar.load_holidays`; the TypeScript mirror takes them from
`serviceHealthWindows.isUsTradingDay`, which reads the identical
`scripts/config/market_holidays.json`. Neither may fall back to a weekday-only walk. A
weekday-only TS mirror is not a rounding difference — it disagreed with Python by **four**
sessions across a summer window (`2026-03-20 -> 2026-08-14` read 105 instead of 101), and the
parity table is the only thing that catches it, because a holiday-free case window cannot. For
the same reason the TS side derives its boundary with `performanceData.lastCompletedSession`,
which walks `marketSession.lastCompletedSessionDate` (weekday-only by design, for the
scan-storm checks it was written for) back off a holiday that never opened.

#### C.2.2 Staleness is enforced at READ time (DECISION 5)

The writer's `nav_sessions_behind` is a value frozen at build time. A payload generated on
2026-03-27 that said `status: "ok"`, `nav_sessions_behind: 0` was still rendering a confident
hero with no banner five months later, because the reader believed both fields.

The read path therefore:

1. **Always derives** `sessions_behind` from `nav_as_of` / `period_end` against the current
   last completed session, and publishes `max(declared, derived)`. The declared value may only
   make the payload look *worse*, never fresher. A payload that omits the field entirely
   derives it the same way.
2. **Drives `isStale` off that derived number** against `NAV_STALENESS_BUDGET_SESSIONS`,
   independent of the payload's own `status` word. A payload can say `"ok"` and be stale; the
   reader decides.

Neither rule is a substitute for Gate 2 on the writer side. They exist because the writer's
verdict has an expiry date and the reader's does not.

**Gate 3 — benchmark coherence.** `benchmark` is a single nullable object. It is produced by `build_benchmark_block`, whose only input is an `AlignedPairs`, and which returns either a fully-populated `BenchmarkBlock` or `(None, reason)`. No caller may compute beta, alpha, tracking error, information ratio, correlation, r-squared, or `benchmark_return` independently. Consequences:

- `benchmark is None` => **no benchmark-derived field appears anywhere** in the payload, and the UI renders no beta/alpha/TE card, no "SPY REBASED" figure, and no benchmark line — it renders the block's `unavailable_reason`.
- `benchmark is not None` => `benchmark_return` is present **and** the per-point `benchmark_close` / `benchmark_return` arrays are serialized in `series`, so the chart can draw exactly the series the statistics were computed from.

Pre-construction gates (first failure wins):

```
benchmark series missing or fetch failed  -> None, "benchmark_unavailable"
n_common < MIN_N_BENCHMARK (40)           -> None, "insufficient_n"
n_common < 0.90 * n_returns               -> None, "benchmark_coverage"
stdev(benchmark, ddof=1) == 0             -> None, "benchmark_degenerate"
```

The last gate alone kills the live `beta = 23.93` / `alpha = +2190.09%` block. `align_series` performs an **inner join and nothing else** — no zero-fill, no `prev` carry. The `prev = None` poisoning at `perf_twr_builder.py:391-395` is deleted.

Post-construction plausibility: `|alpha_annualized| > IMPLAUSIBLE_ALPHA (1.0)` emits `IMPLAUSIBLE_ALPHA` (error) and forces `degraded`. A broad-index regression producing +2190% alpha is a data defect by definition.

### C.3 Additional plausibility gates

| Condition | Warning | Severity | Effect |
|---|---|---|---|
| `annualized is not None and abs(annualized) > 10.0` | `IMPLAUSIBLE_ANNUALIZED` | error | `degraded`; `annualized` published as `null`, reason `"implausible"` |
| any quarantined subperiod | `SUBPERIOD_SUSPECT` | error | `degraded` |
| a chained subperiod past `SUSPECT_RETURN_THRESHOLD` | `SUBPERIOD_EXTREME` | **error** | `degraded` (DECISION 3; unreachable through `build_payload` under §B.4, kept as a backstop) |
| any `gap_days > 4` | `NAV_GAP` | info | none |
| any `flow_dominant` subperiod | `FLOW_DOMINANT` | info | none — disclosure only, never an exemption |
| fewer than 2 usable NAV observations | `NAV_INSUFFICIENT` | info | none; `_base_status` already resolves `insufficient_data` |
| `UnknownFlowType` raised | `FLOWS_UNKNOWN_TYPE` | error | `degraded`, `flows.status = FAILED` |
| `DuplicateNavDate` raised | `NAV_DUPLICATE_DATE` | error | `degraded` |
| xirr `no_convergence` | `MWR_NO_CONVERGENCE` | warn | `stale`-or-worse; `mwr_annualized` null with reason |
| an account missing on a date in a consolidated group | `NAV_ACCOUNT_GAP` | warn | date dropped, never summed short |

### C.4 Warning object shape

Warnings are structured, not free strings. The UI's current `warnings.join()` regex (`/deposit|withdrawal|flow|acats|transfer/i` at `PerformancePanel.tsx:524-525`) is deleted and replaced by dispatch on `code`.

```jsonc
{ "code": "NAV_STALE",
  "severity": "warn",                 // "info" | "warn" | "error"
  "message": "NAV is 101 sessions behind the last completed session.",
  "context": { "nav_as_of": "2026-03-20", "sessions_behind": 101 } }
```

Allowlisted codes (exhaustive; a TS union type mirrors it and the panels switch exhaustively):

```
NAV_UNAVAILABLE  NAV_STALE  NAV_SOURCE_DISK  NAV_SOURCE_TURSO  NAV_GAP
NAV_DUPLICATE_DATE  NAV_ACCOUNT_GAP  NAV_NON_FINITE  NAV_INSUFFICIENT
FLOWS_FETCH_FAILED  FLOWS_UNKNOWN_TYPE  FLOWS_EMPTY_VERIFIED
FLOWS_TRANSFERS_SECTION_ABSENT  FLOWS_SOURCE_MIRROR
FLOWS_COVERAGE_QUERY_FAILED  FLOWS_SOURCE_DISAGREEMENT
FLOW_DATE_SHIFTED  FLOW_AFTER_PERIOD_END  FLOW_DOMINANT
SUBPERIOD_SUSPECT  SUBPERIOD_SKIPPED  SUBPERIOD_EXTREME  INFERRED_FLOW_CANDIDATE
IMPLAUSIBLE_ANNUALIZED  IMPLAUSIBLE_ALPHA
BENCHMARK_UNAVAILABLE  MWR_NO_CONVERGENCE
```

`NAV_INSUFFICIENT` (info) carries the `insufficient_data` banner's sample count
(§C.6). It was emitted for a round before it was documented, which is the exact
drift the enforcement below exists to catch.

"Exhaustive" is enforced, not asserted in prose:
`tests/test_perf_twr_residuals.py::test_every_emitted_warning_code_is_in_the_spec_allowlist`
AST-scans every `_warning(...)` call in `scripts/perf_twr_builder.py` and fails on any
code that is not a word in the fenced block above. Adding a code means editing this
block and the TS union in the same commit.

`SUBPERIOD_EXTREME` carries severity **`error`**, not `info` (audit round 3, DECISION 3):
an extreme session that survives into the chain is a reason to distrust the number, so
it floors the status at `degraded`.

### C.5 Payload contract (v2)

Single schema. Both the `ok` and the degraded branches emit **the same keys**; only values change. The current code's three divergent shapes (full / `<2`-rows / no-NAV) are collapsed.

```jsonc
{
  "schema_version": 2,
  "status": "ok",                          // C.1 enum
  "generated_at": "2026-08-15T14:55:40.320113Z",   // full ISO instant, UTC, ALWAYS
  "account_id": "U1234567",
  "methodology": {
    "curve_type": "twr_daily_eod",
    "return_basis": "time_weighted",
    "flow_convention": "bod",
    "day_count": "act/365",
    "vol_scaling_days": 252,
    "sortino_target": 0.0,
    "risk_free_rate": 0.0525,              // annual decimal
    "risk_free_source": "fred_dgs3mo" | "fallback_zero",
    "benchmark_basis": "price_return",
    "inferred_flows": []
  },
  "nav_source": "flex_live" | "disk_cache" | "turso" | "none",
  "nav_as_of": "2026-08-14",
  "nav_sessions_behind": 1,
  "flows_status": "ok" | "empty_verified" | "failed",
  "flows_source": "flex_cash_transactions+transfers",
  "period_start": "2025-12-31",
  "period_end": "2026-08-14",
  "calendar_days": 226,
  "counts": { "n_nav_observations": 158, "n_subperiods": 157, "n_returns": 155,
              "n_skipped": 2, "n_suspect": 0 },

  "twr": { "cum_return": 0.1164093, "annualized": { /* GatedValue */ },
           "excludes_suspect": false },
  "mwr": { "period_return": { /* GatedValue */ },
           "annualized": { /* GatedValue */ },
           "multiple_sign_changes": false },

  "risk": { "volatility": {}, "sharpe_ratio": {}, "sortino_ratio": {},
            "downside_deviation": {}, "max_drawdown": {}, "current_drawdown": {},
            "var_95": {}, "cvar_95": {} },          // every value is a GatedValue
  "drawdown_detail": { "trough_date": "...", "peak_date": "...",
                       "trough_days": 12, "recovery_days": 31, "ongoing": false },
  "distribution": { "hit_rate": {}, "best_day": {}, "worst_day": {},
                    "average_up_day": {}, "average_down_day": {},
                    "win_loss_ratio": {}, "positive_days": {}, "negative_days": {},
                    "flat_days": {} },

  "benchmark": null,                        // or a complete BenchmarkBlock (§A.4.1)

  "equity": { "starting": 99492.94, "ending": 220255.43,
              "net_external_flows": 100000.0,
              "investment_pnl": 20762.49 },  // ending - starting - net_external_flows

  "series": [ { "date": "2026-01-02", "nav": 100000.0, "twr_index": 100.0,
                "daily_return": null, "cum_return": 0.0, "drawdown": 0.0,
                "flow": 0.0, "skipped": false,
                "benchmark_close": 592.11, "benchmark_return": null } ],
  "subperiods": [ { "date": "2026-02-06", "b": 246713.50, "e": 972215.53,
                    "c": 725000.0, "denominator": 246713.50, "r": 0.00203487,
                    "cum_r": 0.0123, "gap_days": 1, "flags": ["flow_dominant"],
                    "skip_reason": null } ],
  "warnings": [ /* §C.4 objects */ ]
}
```

Notes that are requirements, not commentary:

- **A `count` is what this run computed; a `GatedValue.n` is what the sample supports.** They
  are different questions and a suppressed payload answers them differently.
  `counts.n_returns` on a branch that chained zero returns is **`0`** — reporting the 60 the NAV
  series *would* have supported is a count of returns that do not exist, and it rendered as
  `N=60` beside an empty series and an ending equity. `risk.*.n` and `mwr.*.n` on that same
  payload stay at the supported sample (60), because a card must never blame its sample size
  for a suppression that has nothing to do with N. `period_start` / `period_end` /
  `calendar_days` / `equity.starting` / `equity.ending` are observed facts and stay populated on
  every branch; `equity.net_external_flows` and `equity.investment_pnl` are `null` with the
  flows unknown, never a fabricated `0`.
- `equity.investment_pnl` replaces `summary.pnl`. Today's `pnl = ending - starting` is a raw NAV delta with the 725k deposit inside it and is presented as P&L. Subtracting `net_external_flows` is mandatory.
- `series[].twr_index` (base 100) is the chart's y-series for the TWR view. `series[].nav` is dollars and is chartable only on an explicitly-labelled "NAV" toggle. `normalizeSeries`'s current `nav -> equity` preference (`performanceData.ts:50`) is inverted: `twr_index` wins.
- `drawdown` in `series` is derived from `twr_index`, never from `nav`. The two-definitions-on-one-row problem (`max_drawdown` from the TWR index vs `current_drawdown` from dollar NAV) is eliminated because both now come from `drawdowns(chain)`.
- Every gated number is a `GatedValue`. A bare `null` with no reason is not acceptable output.
- `warnings` is always present and always an array.

### C.6 What the UI renders per status

`GET /api/performance` **always returns HTTP 200** with a `status` field. Missing or degraded data is never a 4xx and never a 5xx. (An actual server fault — DB unreachable — is the existing 503 path and is out of scope here.)

The route stops being a blind pass-through only insofar as it must forward the new keys; it performs no field rewriting. `normalizePerformanceData` runs **once** (in `usePerformance`), and its return literal must include `status`, `warnings`, `nav_source`, `nav_sessions_behind`, `flows_status`, `counts`, `benchmark`, `subperiods`. The double-normalize in the panels (`PerformancePanel.tsx:267`, `MobilePerformancePanel.tsx:286`) is deleted — it is what strips `status` today and makes both panels' `insufficient_data` branches dead code.

| status | Banner | Hero | Cards | Chart |
|---|---|---|---|---|
| `ok` | none | TWR total + period | all gated cards render value or `gateCopy` reason | TWR index + benchmark line if `benchmark != null` |
| `stale` | amber, `As of {nav_as_of} — {sessions_behind} sessions behind` | renders normally, with the `as of` date next to the value | render normally | renders normally |
| `degraded` | red, first `error` warning's `message`, expandable to all warnings | `--` when `twr.cum_return` is null; otherwise the value with a red `degraded` pill | every card renders `--` with the card's own reason; suppressed cards are never given a fabricated `0` | TWR line drawn only if `twr` non-null; benchmark line omitted when `benchmark == null` |
| `insufficient_data` | neutral, `Needs at least 2 NAV observations (have {n})` | `--` | all `--` | empty state |
| `unavailable` | red, `No NAV series available (Flex, disk and Turso all empty)` | `--` | all `--` | empty state |

`degraded` is loud on purpose: the failure this spec exists to prevent rendered as a confident +951%.

A `stale` banner is driven by the READ-time derivation of §C.2.2, not by the payload's `status`
word. A payload claiming `ok` whose `nav_as_of` is past the budget still gets the amber banner.

**A suppressed benchmark renders its reason (§C.2 Gate 3).** `benchmark: null` means the beta,
alpha and tracking-error cards render `--` plus `gateCopy(reason)` — it does NOT mean the cards
are deleted. Deleting them is what made `benchmark_coverage`, `benchmark_degenerate` and the
benchmark `insufficient_n` copy unreachable: the reason was computed, carried as far as
`benchmarkReason`, and then dropped because both panels only asked `if (benchmark)`. Python
must therefore carry the reason OUT of the `BENCHMARK_UNAVAILABLE` warning context and into a
field the reader reads, and that context must include `n_common`, `n_returns` and `coverage`,
because `gateCopy` cannot word `SPY covers 83% of sessions` or `needs 40 sessions (N=30)`
without the sample. A reason with no sample is a reason that cannot be rendered.

The methodology footer's "Risk-Free" line reads `(FRED DGS3MO)` only when
`methodology.risk_free_source == "fred_dgs3mo"`, and `(fallback 0)` **only when the source is
actually the zero fallback**. Labelling every non-`fred_dgs3mo` source — an absent field
included — as `(fallback 0)` printed `3.74% (fallback 0)` next to a Sharpe card reading
`RF 3.74% DGS3MO` off the same payload. One rate, one source, one label.

Warnings are prepended once. `LEGACY_PAYLOAD` was added at two points on the same read path,
so a v1 row surfaced the same warning twice in one list.

### C.7 A GET must never trigger a Flex rebuild (DECISION 7)

**Highest-severity item in this document.** `isPerformanceBehindPortfolioSync` compared
`last_sync` / `as_of`, keys the v2 payload no longer emits. Both sides read `undefined`, the
comparison declared the payload behind, and `shouldRebuild` became structurally always true:
one GET fired one `POST /performance/background`, three GETs fired three, each one a full Flex
`SendRequest` plus its polling loop against a single query id.

This repo has already taken a 24h-to-168h IBKR throttle embargo from exactly this pattern
(`project_cash_flow_sync_incident_2026_08_04`). Two independent guards, because one of them
already failed:

1. **Teach the freshness comparison the v2 keys** — `generated_at` and `nav_as_of`. A payload
   whose keys the comparison does not recognize is treated as FRESH, not as behind. Failing
   open on an unrecognized schema is what turned a rename into a request loop.
2. **A hard minimum rebuild interval**, enforced regardless of what the comparison concludes.
   `_running_build` only dedupes CONCURRENT builds; sequential GETs slip straight past it. The
   interval is belt and braces so a future schema change cannot reopen the loop.

The interval is `MIN_REBUILD_INTERVAL_MS = 5 * 60_000`, held in
`web/app/api/performance/route.ts` next to `triggerBackgroundRebuild` so the floor sits at the
single point every rebuild passes through, not at the call sites that decide one is warranted.

Pinned by `web/tests/performance-rebuild-loop.test.ts`: 1 GET produces 0 background posts on a
fresh payload, and N GETs in quick succession produce at most 1. Measured on the landed code:
25 sequential GETs against a fresh v2 cache fire **0** rebuilds and 0 `radonFetch` calls at
all; 25 against a genuinely stale one fire **1** while serving the stale payload every time
(SWR), and the next rebuild is only possible 5 minutes later.

---

## D. MWR / IRR

Two numbers, both published, never conflated.

### D.1 Cashflow vector (`build_cashflow_vector`)

Investor perspective. Sign flips relative to `C`.

```
t = d_0 : CF_0  = -N_0                    seeding the account is an investment
t = d_i : CF_i  = -C_i        for each date with C_i != 0 and d_0 < d_i < d_M
t = d_M : CF_M  = +N_M - C_M              terminal NAV net of any same-day flow
```

Two rules that are easy to get wrong and are asserted by tests:

- **A flow dated `d_0` is already inside `N_0`.** It MUST NOT produce a separate `CF`. Double-counting it is the classic XIRR error.
- Duplicate dates are summed into one slot before solving. The vector is unique and ascending.

### D.2 `mwr.period_return` — Modified Dietz over the whole window

Always computable, no annualization, correct for sub-year windows:

```
D   = (d_M - d_0).days
w_i = (D - (d_i - d_0).days) / D
mwr_period = (E - B - SUM C_i) / (B + SUM w_i * C_i)
```

Gate: `n_returns >= MIN_N_MWR (20)`; below that, `insufficient_n`.

### D.3 `mwr.annualized` — XIRR

```
NPV(R) = SUM_i CF_i * (1+R)^(-tau_i),   tau_i = (d_i - d_0).days / 365.0    [Act/365 Fixed]
XIRR   = R such that NPV(R) = 0,        domain R > -1 strictly
```

Act/365 Fixed matches `annualize_return`, so TWR and MWR are directly comparable. That is the point of stating it.

**Solver: bisection on a proven bracket. Never bare Newton.** Newton can step below `R = -1`, where `(1+R)^tau` is undefined for fractional `tau`, and it diverges on flat NPV curves.

```
lo = -0.9999999 ; hi = 10.0
if sign(NPV(lo)) == sign(NPV(hi)):  double `hi` up to a cap of 1e6
if still same sign:                 return None, "no_sign_change"
bisect until |NPV| / SUM|CF| < 1e-10  or  (hi - lo) < 1e-12,  max_iter = 200
non-convergence -> None, "no_convergence"
```

Failure taxonomy, each with an explicit reason string:

| Condition | Result |
|---|---|
| `< 2` unique dates | `None`, `insufficient_dates` |
| all `CF == 0` | `None`, `degenerate` |
| no sign change | `None`, `no_sign_change` |
| all `CF <= 0` (total loss) | `-1.0`, `total_loss` — the analytic limit; report it, do not iterate |
| `> 1` sign change | solve anyway, set `multiple_sign_changes: true`, take the root nearest 0 |
| `calendar_days < 365` | `None`, `period_lt_1y` |
| `n_returns < 20` | `None`, `insufficient_n` |

`period_lt_1y` on XIRR is not optional: IRR is inherently a per-year rate, and publishing it over a 79-day window annualizes a sub-year return through the back door. `mwr.period_return` is what the UI shows in that case.

### D.4 The gate fix

The live "MWR IRR — needs 20 sessions (N=57)" is **not** an inverted comparison. `N >= 20` passes at 57; the value is `null` because **nothing in the repo computes an IRR** (`grep -rniE '\b(irr|mwr|xirr|newton)\b'` returns only labels and docstrings). The card then falls through to the N-gate copy and blames the sample size for a metric that does not exist.

Both halves are fixed:

1. `xirr` and `modified_dietz` are implemented in `twr_math` (§D.2, §D.3) and their results are written to `payload.mwr`.
2. `hasMwr`-style boolean flags are deleted from both panels. Every card reads a `GatedValue` and renders `gateCopy(g, label)`, which is an exhaustive switch on `unavailable_reason`. It is structurally impossible for a missing value to render an N-gate reason, because the reason is produced by the same function that produced the `null`.

Test pins required in both directions:

- `n_returns=57, min_n=20` -> value present, `unavailable_reason is None`
- `n_returns=19, min_n=20` -> `None`, `unavailable_reason == "insufficient_n"`, `gateCopy` -> `needs 20 sessions (N=19)`
- `n_returns=57, flows_status=FAILED` -> `None`, `unavailable_reason == "no_flow_data"`, `gateCopy` -> `external flows unavailable` — and specifically **not** the N-gate string.

---

## E. TEST-FIXTURE CONTRACT

Python: `tests/test_twr_math.py` (pure math), `tests/test_flex_flows.py` (classification + XML), `tests/test_perf_twr_builder.py` (ingest, gates, payload), `tests/test_twr_gate_parity.py`.
TypeScript: `web/tests/performance-twr.test.ts` (gate helpers + `gateCopy`), `web/tests/performance-panel-twr-payload.test.tsx` (v2 payload render per status).

Fixtures live in `tests/fixtures/perf/` as JSON. Every expected value below is hand-computed and must be asserted to the stated precision (`pytest.approx(rel=1e-9)` unless noted "exact").

### E.1 Subperiod / chain scenarios

| # | Scenario | Input | Expected |
|---|---|---|---|
| 1 | **Flat NAV** | NAV `[100000, 100000, 100000]`, no flows | `n_subperiods=2`, `n_returns=2`, `returns=[0.0, 0.0]`, `cum_return=0.0` exact, `volatility` gated `insufficient_n`, `max_drawdown` gated |
| 2 | **Steady growth, no flows** | NAV `[100, 110, 99]` | `r=[0.10, -0.10]` exact, `cum_return = 1.10*0.90 - 1 = -0.01` **exact — assert it is -0.01, not 0.0** |
| 3 | **The 2026-02-06 case** | `B=246713.50`, `E=972215.53`, `C=+725000` | residual `502.03` / denominator `971713.50` -> `r = 0.0005166440519762543`; assert `r < 0.01`; assert explicitly `r != approx(2.940666, rel=1e-3)` and `r != approx(0.0020348704063621486, rel=1e-6)` (the superseded EOD value) |
| 4 | **Withdrawal** | `B=100000`, `E=90000`, `C=-15000` | residual `5000` / denominator `85000` -> `r = 1/17 = 0.058823529411764705` |
| 5 | **Same-day deposit AND withdrawal** | deposits `+50000`, withdrawals `-20000` same date; `B=100000`, `E=131000` | net `C=+30000`, residual `1000` / denominator `130000` -> `r = 1/130 = 0.007692307692307693` |
| 6 | **ACATS in (Transfer element)** | `<Transfer type="ACATS" direction="IN" assetCategory="STK" quantity="1000" transferPrice="725.00" positionAmountInBase="725000.00" cashTransfer="0"/>` | parsed `C=+725000.00`; assert the parser did **not** return `725.00` |
| 7 | **ACATS out** | same with `direction="OUT"`, `positionAmountInBase="725000.00"` | `C = -725000.00` |
| 8 | **Transfer missing direction** | `direction` absent | raises `UnknownFlowType` |
| 9 | **Flow on the first day** | NAV `[d0:100000, d1:101000]`, `C[d0]=+100000` | chain: `r_1 = 0.01` exact — the d0 flow is inside `N_0`, never enters a subperiod, and so leaves `C_1 = 0` and both conventions identical. MWR vector: exactly `[(d0,-100000), (d1,+101000)]` — the `+100000` must NOT appear as a separate `CF` |
| 10 | **Flow on the last day** | NAV `[d0:100000, d1:151000]`, `C[d1]=+50000` | `r_1 = (151000-50000-100000)/(100000+50000) = 1000/150000 = 1/150 = 0.006666666666666667`; MWR `CF_1 = 151000 - 50000 = +101000` |
| 11 | **NAV goes to zero, nothing on file** (revised, DECISION 4) | NAV `[100000, 0, 0]`, no flows | subperiod 1 QUARANTINED, `skip_reason="suspect_no_flow"`, `r_1 is None`; subperiod 2 skipped `zero_base`; `status="degraded"`; the published `cum_return` is **not** `-1.0`. `annualize_return(-1.0, 730)` still returns `-1.0` / `total_loss` as a unit (must not raise, must not produce a complex number) — it is simply no longer reachable from an unexplained wipeout |
| 11b | **NAV goes to zero, withdrawal on file** | NAV `[100000, 0]`, `C[d1] = -100000` | `r_1 = (0 - (-100000) - 100000)/100000 = 0.0` exact; `skip_reason is None`; `n_suspect == 0`. This is the whole of DECISION 4's exemption |
| 12 | **Negative NAV** | NAV `[-5000, 1000]` | subperiod skipped `negative_base`; `n_returns=0`; `status="insufficient_data"` |
| 13 | **Single observation** | NAV `[100000]` | `n_subperiods=0`, `status="insufficient_data"`, `warnings` non-empty, payload has all v2 keys |
| 14 | **Empty series** | NAV `[]` | `status="unavailable"`, `NAV_UNAVAILABLE` warning, HTTP 200 from the route |
| 15 | **Weekend gap** | Fri 2026-01-09 -> Mon 2026-01-12 | `gap_days=3`, **no** `NAV_GAP` warning (<= 4) |
| 16 | **Holiday gap** | Thu 2026-01-08 -> Wed 2026-01-14 | `gap_days=6`, `NAV_GAP` info warning, subperiod still chained |
| 17 | **Duplicate dates** | two rows for `2026-01-13` | raises `DuplicateNavDate`; builder converts to `NAV_DUPLICATE_DATE` (error) + `degraded`. **Assert it is not last-write-wins** |
| 18 | **Unsorted dates** | NAV supplied `[d2, d0, d1]` | sorted ascending internally; identical `cum_return` to the sorted input |
| 19 | **Suspect, no flow** | `B=246713.50`, `E=972215.53`, `C=0` | subperiod excluded, `skip_reason="suspect_no_flow"`, `SUBPERIOD_SUSPECT` error warning, `status="degraded"`, `chain.excluded_suspect is True`, `INFERRED_FLOW_CANDIDATE` context `amount == 725502.03` |
| 20 | **Flow-dominant but explained** | `B=246713.50`, `E=972215.53`, `C=+725000` | chained, flags contain `flow_dominant`, `status` stays `ok` |
| 21 | **Flow date with no NAV row** | `C` on Sat 2026-01-10, NAV on Fri 09 and Mon 12 | flow applied to the Mon 12 subperiod, `FLOW_DATE_SHIFTED` info warning |
| 22 | **Consolidated accounts, one missing a date** | acct A has 2026-01-13, acct B does not | that date is **dropped**, `NAV_ACCOUNT_GAP` warning. Assert the total is not silently short by B's NAV |

### E.2 Flow classification

| # | Input `type` | Expected |
|---|---|---|
| 23 | `"Deposits/Withdrawals"` | `EXTERNAL` |
| 24 | `"  deposits   &   withdrawals "` | `EXTERNAL` (whitespace + case normalized) |
| 25 | `"Other Fees"`, `"Dividends"`, `"Payment In Lieu Of Dividends"`, `"Broker Interest Received"`, `"Broker Interest Paid"`, `"Withholding Tax"` | `INTERNAL` (parametrized) |
| 26 | `"Deposit Advance Reversal"` | raises `UnknownFlowType`. **Assert the old substring matcher would have returned external** — this is the regression pin for defect #2 |
| 27 | `"Sharebuilder"` (invented) | raises `UnknownFlowType` |
| 28 | `is_external_flow_type` identity | `is_external_flow_type(x) == (classify_flow_type(x) is FlowClass.EXTERNAL)` over the full union of both frozensets |
| 29 | Single-classifier structural test | AST-scan: `perf_twr_builder.py` defines no `frozenset` of type strings and no local `_is_external` |
| 30 | `FlowSet` construction | `FlowSet.failed("timeout").status is FAILED`; `FlowSet(status=FAILED, by_date={"d":1.0})` raises; `FlowSet.empty_verified()` has `status is EMPTY_VERIFIED` and `by_date == {}` |
| 31 | Flows section absent from XML | `parse_flows(xml_without_CashTransaction_section)` -> `FlowSet.failed("flows_section_absent")`, not `EMPTY_VERIFIED` |

### E.3 Statistics, pinned on a 5-observation series

`r = [0.01, -0.02, 0.03, 0.00, -0.01]`, `N=5`, `rf_daily = 0`. These bypass the N gates via a `_gate_override` test hook (a `min_n` parameter defaulting to the table value) so the arithmetic is testable independently of the gates.

| # | Quantity | Expected |
|---|---|---|
| 32 | `mean` | `0.002` |
| 33 | `stdev(ddof=1)` | `0.019235384061671343` |
| 34 | `volatility` | `0.3053522555999873` |
| 35 | `sharpe_ratio` | `1.6505527329729042` |
| 36 | `downside_deviation` (target 0, divisor **N**) | daily `0.01`; annualized `0.15874507866387544` |
| 37 | `sortino_ratio` | `3.1749015732775074` — **anti-pin: assert it is NOT `2.0079840636817807`**, which is what dividing by the 2 negative observations produces |
| 38 | `daily_risk_free(0.0525)` | `0.0002030693720416199` — **anti-pin: not `0.00020833333333333332`** (`rf/252`) |
| 39 | `max_drawdown` on `r=[0.10,-0.20,0.05]` | `-0.20` exact; `trough_days=1`; `recovery_days=2`; `ongoing=True` |
| 40 | `annualize_return(0.10, 730)` | `0.04880884817015163` |
| 41 | `annualize_return(0.10, 364)` | `None`, `period_lt_1y` |
| 42 | `annualize_return(9.5128, 79)` | `None`, `period_lt_1y`. Anti-pin: unguarded this is ~`52342` (+5,200,000%) |
| 43 | `annualize_return(-1.5, 400)` | `None`, reason `invalid_total_return` — **must not raise `TypeError` on a complex result**, which today's `(1+(-1.5))**(252/57)` does |
| 44 | VaR/CVaR on the 20-point series in §E.3a | `var_95 = -0.0405`, `cvar_95 = -0.0500`, `k = 1` |
| 45 | VaR invariant | `cvar_95 <= var_95` always; additionally `var_95 <= 0` whenever `count(r < 0) >= ceil(0.05*N)` |

§E.3a series (sorted): `[-0.05,-0.04,-0.03,-0.02,-0.01,0,0,0.01,0.01,0.01,0.01,0.01,0.01,0.01,0.01,0.02,0.02,0.02,0.03,0.04]`, `p = 19*0.05 = 0.95`, `var_95 = -0.05 + 0.95*0.01 = -0.0405`.

### E.4 Benchmark

`r = [0.01,-0.02,0.03,0.00,-0.01]`, `b = [0.008,-0.015,0.02,0.002,-0.005]`, `rf_daily = 0`, `n_common = 5`, gates overridden.

| # | Quantity | Expected |
|---|---|---|
| 46 | `beta` | `1.4469914040114613` |
| 47 | `alpha_annualized` | `-0.22528366762177685` |
| 48 | `correlation` | `0.9937171949545618` |
| 49 | `r_squared` | `0.9874738635483625` |
| 50 | `tracking_error` | `0.09976973488989535` |
| 51 | `information_ratio` | `0.0` (`approx(abs=1e-12)`; mean active is exactly 0) |
| 52 | `benchmark_return` | `0.00968925982399993` |
| 53 | **Missing benchmark** | `build_benchmark_block` with an empty/failed series -> `(None, "benchmark_unavailable")`; payload has `benchmark: null` and **no** `beta` key anywhere |
| 54 | **Benchmark shorter than N** | `n_returns=60`, `n_common=50` (coverage 0.833) -> `(None, "benchmark_coverage")` |
| 55 | **Zero-variance benchmark** | `b = [0.0]*60` -> `(None, "benchmark_degenerate")`. This is the direct pin for the live `beta=23.93` |
| 56 | **Alignment never zero-fills** | portfolio has 2026-01-05..09, benchmark missing 01-07 -> `n_common == 4`, and the 01-08 pair uses the real 01-08 benchmark return. **Anti-pin: assert 01-08 is not `0.0`** (today's `prev = None` poisoning) |
| 57 | **Structural impossibility** | property test over 200 random payloads: `payload["benchmark"] is None` implies no key in `{"beta","alpha","tracking_error","information_ratio","correlation","r_squared","benchmark_return","benchmark_close"}` appears anywhere in the payload JSON |
| 58 | **Implausible alpha** | a fixture producing `alpha_annualized = 21.90` -> `IMPLAUSIBLE_ALPHA` error warning, `status == "degraded"` |

### E.5 MWR

| # | Scenario | Expected |
|---|---|---|
| 59 | **Textbook XIRR** | `CF = [(2026-01-01, -1000), (2027-01-01, -1000), (2028-01-01, +2310)]` -> `0.10` exact to `rel=1e-9`. (`NPV(0.10) = -1000 - 1000/1.1 + 2310/1.21 = 0`) |
| 60 | **No sign change** | `CF = [(d0, +100), (d1, +50)]` -> `None`, `no_sign_change` |
| 61 | **Total loss** | `CF = [(d0,-1000),(d1,-500),(dM, 0)]` -> `-1.0`, `total_loss`, zero iterations |
| 62 | **Sub-year window** | golden fixture (§E.6), `calendar_days=84` -> `None`, `period_lt_1y` |
| 63 | **Below N gate** | `n_returns=19` -> `None`, `insufficient_n` |
| 64 | **Flows failed** | `flows.status = FAILED` -> both `mwr.period_return` and `mwr.annualized` `None` with `no_flow_data`; `gateCopy` renders `external flows unavailable`, **not** an N-gate string |
| 65 | **Modified Dietz** | `B=100`, `D=100` days, `C=+50` at day 40 (`w=0.60`), `E=170` -> `20/130 = 0.15384615384615385` |
| 66 | **Newton anti-pin** | a fixture whose NPV curve is flat near the root; assert the solver converges (bisection) rather than diverging |

### E.6 Golden path — 60 sessions, every metric pinned

Fixture `tests/fixtures/perf/golden_60.json`. Construction (deterministic, reproducible by hand):

- Dates: 61 consecutive weekdays starting **2026-01-02**, ending **2026-03-27**. `calendar_days = 84`.
- Returns: the 6-value pattern `[+0.006, -0.004, +0.002, -0.008, +0.010, -0.001]` repeated exactly 10 times, giving 60 returns.
- Flows: `+100000.00` on **2026-02-13** (index 30), `-25000.00` on **2026-03-06** (index 45).
- NAV built forward as `N_0 = 100000.00`, `N_t = N_{t-1} * (1 + r_t) + C_t`. This is the fixture's INPUT construction and is deliberately left as-is across the BOD change, so the NAV checkpoints below are stable. It means the 58 flow-free sessions reproduce the pattern exactly (`C_t = 0` makes both conventions identical) while the two flow days differ: their published `r_t` is `E_t / (B_t + C_t) - 1`, i.e. `p_t * B_t / (B_t + C_t)`.
  - index 30 (2026-02-13): residual `= -102.5775651064614`, denominator `= 202577.5651064734` -> `r = -0.0005063619214326489` (was `-0.001`).
  - index 45 (2026-03-06): residual `= 409.737624650792`, denominator `= 179868.8123253928` -> `r = 0.0022779803755503406` (was `+0.002`).
- Key NAV values: `N_29 = 102577.56510647341`, `N_30 = 202474.98754136695`, `N_44 = 204868.8123253928`, `N_45 = 180278.54995004358`, `N_60 = 182217.35574011656`.
- `rf_annual = 0.02` -> `rf_daily = 7.85849419846496e-05`.

Pinned expectations (`rel=1e-12` unless noted):

| Field | Value |
|---|---|
| `counts.n_nav_observations` | `61` |
| `counts.n_subperiods` | `60` |
| `counts.n_returns` | `60` |
| `counts.n_skipped` / `n_suspect` | `0` / `0` |
| `twr.cum_return` | `0.05092267338835921` (analytically `(1.006*0.996*1.002*0.992*1.010*0.999)**10 * (1-0.0005063619214326489)/0.999 * (1+0.0022779803755503406)/1.002 - 1`) |
| `twr.annualized` | `None`, `period_lt_1y` (84 calendar days) |
| `risk.volatility` | `0.09621706656735644` |
| `risk.sharpe_ratio` | `2.0104270378243907` |
| `risk.sortino_ratio` | `3.3179719585628065` |
| `risk.downside_deviation` | `0.05829988756473724` |
| `risk.max_drawdown` | `-0.009991936000000146` |
| `risk.var_95` | `-0.008` |
| `risk.cvar_95` | `-0.008` (`k = 3`) |
| `distribution.hit_rate` | `0.5` exact |
| `distribution.best_day` / `worst_day` | `0.010` / `-0.008` exact |
| `mwr.period_return` | `0.05020769210515868` |
| `mwr.annualized` | `None`, `period_lt_1y` (the raw XIRR would be `0.23811347727572396`; assert the *reason*, and assert the solver value in a separate unit test with `calendar_days` overridden to 420) |
| `equity.starting` / `ending` | `100000.00` / `182217.35574011656` |
| `equity.net_external_flows` | `75000.00` exact |
| `equity.investment_pnl` | `7217.35574011656` |
| `status` | `"ok"` |
| `warnings` | `[]` |
| `flows_status` | `"ok"` |

Every `low_confidence` flag is `true` here (`n_returns = 60 < 252`).

### E.7 Gate boundary scenarios

Each is a pair. The N is the number of returns; NAV/flows are synthesized by repeating a `+0.001` day.

| # | Boundary | `N-1` expectation | `N` expectation |
|---|---|---|---|
| 67 | `MIN_N_DISPERSION` 19/20 | `volatility`, `max_drawdown`, `var_95`, `cvar_95` all `None` / `insufficient_n`, `min_n == 20` | all four non-`None` |
| 68 | `MIN_N_MWR` 19/20 | `mwr.period_return` `None` / `insufficient_n` | non-`None` |
| 69 | `MIN_N_BENCHMARK` 39/40 (`n_common`) | `benchmark is None`, reason `insufficient_n` | `benchmark` is a complete block |
| 70 | `MIN_N_RATIO` 59/60 | `sharpe_ratio`, `sortino_ratio` `None` / `insufficient_n` | both non-`None` |
| 71 | `MIN_DOWNSIDE_OBSERVATIONS` 4/5 at `N=60` | `sortino_ratio` `None` / `no_downside` | non-`None` |
| 72 | `VAR_LOW_CONFIDENCE_N` 99/100 | `var_95.low_confidence is True` | `False` |
| 73 | `MIN_CALENDAR_DAYS_ANNUALIZED` 364/365 | `twr.annualized` `None` / `period_lt_1y` | non-`None` |

### E.8 Production regression fixtures

**A fixture that smooths the data cannot pin a dispersion gate.** The 58-point live series was
reconstructed by interpolating 50 of its points geometrically between 8 real anchors, which
flattened `median(|r|)` by 13x (`0.002440` against the real `0.032526`) and was the only reason
`test_74`'s `n_suspect == 3` held. On the REAL values in `data/nav_history_ib.json` — same 58
dates, 50 different numbers — `n_suspect` was 2 and `2026-01-26` (`r = 0.22688613537771407`)
chained unquarantined, while the fixture's comment claimed every unexplained deposit day was
caught.

Required, and pinned in `tests/fixtures/twr_scenarios.py`:

- `real_live_nav()` carries the actual `data/nav_history_ib.json` values and is what every
  production-regression assertion runs against.
- the smoothed series is `interpolated_live_nav()` and is kept only where smoothness is the
  point. The name `live_nav` is gone, and a test asserts it cannot come back
  (`test_r4_the_interpolated_fixture_cannot_be_mistaken_for_the_real_one`).
- under §B.4.1's bar, the real `2026-01-26` quarantines.

| # | Fixture | Assertion |
|---|---|---|
| 74 | `tests/fixtures/perf/live_20260815_nav.json` — the real 58-point disk series (`2025-12-31 .. 2026-03-20`, `N_0 = 99492.93647617`, `N_last = 1045949.48105117`) with **no flows** | `status == "degraded"`; at least one `SUBPERIOD_SUSPECT`; `twr.cum_return` is `None` or excludes the suspect days; **assert the published `cum_return` is not `approx(9.5128, rel=0.01)`** |
| 75 | Same NAV series **with** the three flows supplied. `2026-01-13: +80007.13` is the real production `cash_flows` row (`Deposits/Withdrawals`, `ADJUSTMENT: DEPOSIT ADVANCE`) and must be asserted exactly. `2026-01-26: +42000.00` and `2026-02-06: +725502.03` are fixture stand-ins for the un-fetched rows and are declared as such in the fixture file's `_note` field | `abs(twr.cum_return) < 0.60`; **assert the 2026-01-13 / 01-26 / 02-06 subperiod returns do not chain to `approx(8.4184, rel=0.01)`**; `subperiods["2026-01-13"].r == approx(-0.004993847479630734)` (residual `-932.29` / denominator `186687.72`); `subperiods["2026-02-06"].r == approx(0.0005166440519762543)` (residual `502.03` / denominator `971713.50`). When the real Flex `Transfers` section becomes available, the 02-06 amount is replaced with the reported `positionAmountInBase` and the pin is re-derived, not relaxed |
| 76 | Stale-cache regression | disk series with `max(date) = 2026-03-20`, "today" `2026-08-15` -> `load_nav_from_disk` returns `None` (>30d), ladder falls to Turso; if Turso is also empty, `status == "unavailable"`. Under no circumstances `status == "ok"` |
| 77 | Flex fetch raises | `fetch_flex_xml` raises `TimeoutError` on the flows query -> `FlowSet.failed`, `FLOWS_FETCH_FAILED` error warning, `status == "degraded"`, `twr is None`. **Assert `warnings != []`** — the exact condition the live payload violated |

### E.9 Web-layer tests

| # | Test | Assertion |
|---|---|---|
| 78 | `normalizePerformanceData` key retention | a v2 payload round-trips `status`, `warnings`, `nav_source`, `nav_sessions_behind`, `flows_status`, `counts`, `benchmark`, `subperiods` |
| 79 | Single normalize | `PerformancePanel` and `MobilePerformancePanel` call `normalizePerformanceData` zero times (the hook owns it); enforced by a spy |
| 80 | Status rendering | one test per §C.6 row asserting banner text, hero content, and that no card renders a fabricated `0` under `degraded` |
| 81 | `gateCopy` exhaustiveness | every `unavailable_reason` in the union produces non-empty copy; a TS `never` check fails compilation on an unhandled reason |
| 82 | MWR copy | `{value:null, n:57, min_n:20, unavailable_reason:"no_flow_data"}` renders `external flows unavailable`; **assert the string `needs 20 sessions` is absent** |
| 83 | Benchmark absence | payload with `benchmark: null` renders no beta/alpha/TE cards and no "SPY REBASED" figure; assert `screen.queryByText(/SPY REBASED/i)` is null. **Anti-pin for the live "SPY REBASED == starting equity" fallback** |
| 84 | Chart benchmark line | `buildPerformanceChartModel` never falls back to `startEquity` for `latestBenchmark`; when `benchmark == null` the model returns `latestBenchmark: null` and an empty `benchmarkPath`, and the legend omits the SPY entry |
| 85 | No hardcoded thresholds | grep both panel files for `>= 20`, `>= 40`, `>= 60`, `sessions (N=` -> zero matches |
| 86 | Route status codes | degraded / stale / insufficient_data / unavailable payloads all return **HTTP 200** with the `status` field intact |
| 87 | Freshness | `contentTimestampMs` parses the full-ISO `generated_at`; a payload generated 60s ago is `fresh` (today's date-only `taken_at` makes every row permanently stale) |

---

## F. ACCEPTANCE CRITERIA

Every item is checkable and must be demonstrated with pasted evidence before the work is called done.

**F1 — the headline number.** Replaying the real production NAV series (`data/nav_history_ib.json`, 58 observations, `2025-12-31 .. 2026-03-20`) with the three deposit days correctly classified as external flows produces `twr.cum_return` with `abs(cum_return) < 0.60`, i.e. a plausible single- or double-digit percentage. The current `9.5128` (+951.28%) must be gone. Evidence: the fixture-75 test output.

**F2 — annualization.** With that same series (`calendar_days = 79`), `twr.annualized` is `None` with `unavailable_reason == "period_lt_1y"`. The +3,288,954.62% render is structurally unreachable. Separately, on any series where annualization *is* published, `abs(annualized) <= 10.0` or `status == "degraded"`.

**F3 — no silent zero flows.** A forced Flex failure (fixture 77) yields `status == "degraded"`, `flows_status == "failed"`, `twr` null, and a non-empty `warnings` array containing `FLOWS_FETCH_FAILED`. There is no input that produces `status == "ok"` with `flows_status == "failed"`. Enforced by a property test over the status resolver.

**F4 — no silent stale NAV.** A disk cache whose newest date is more than 30 calendar days old is refused by `load_nav_from_disk`. Any payload whose `nav_source != "flex_live"` or `nav_sessions_behind > 2` has `status != "ok"`. The live combination (`nav_source: "disk_cache"`, `period_end: 2026-03-20`, `status: "ok"`, `warnings: []`) is unreproducible.

**F5 — benchmark coherence.** For 200 randomized payloads, `benchmark is None` implies zero benchmark-derived keys anywhere in the JSON, and `benchmark is not None` implies `series[].benchmark_close` is fully populated over the aligned dates. `beta = 23.93` alongside `BENCHMARK RETURN --` is impossible by construction (fixture 57, 83).

**F6 — MWR exists and reports honestly.** `payload.mwr.period_return.value` is a number on the golden fixture. `payload.mwr.annualized` carries a reason string that names the actual cause. No card anywhere renders an N-gate string for a value that is null for a non-N reason (fixture 82).

**F7 — one classifier.** AST scan shows `EXTERNAL_FLOW_TYPES` defined exactly once in the repo (`scripts/lib/flex_flows.py`). `perf_twr_builder.py`, `portfolio_performance.py`, and `migrate_perf_twr.py` define no flow-type predicate.

**F8 — one gate table.** `tests/test_twr_gate_parity.py` passes. Grep for bare `20`/`40`/`60` gate literals in `PerformancePanel.tsx`, `MobilePerformancePanel.tsx`, `performanceData.ts`, `perf_twr_builder.py` returns nothing (fixture 85).

**F9 — pure module purity.** AST scan of `scripts/lib/twr_math.py` shows imports drawn only from `{math, statistics, datetime, dataclasses, enum, typing, scripts.lib.twr_gates}`; zero occurrences of `os.environ`, `open(`, `requests`, `libsql`, `httpx`, `numpy`, `pandas`.

**F10 — full suite green.** `pytest tests/ scripts/tests/ -q` and `NODE_ENV=test ASSISTANT_MOCK=1 npx vitest run --config ../vitest.config.ts web/tests` both pass from the repo root, with the run summary pasted.

**F11 — live verification.** `chrome-cdp` screenshot of `/performance` on the running app showing: a plausible TWR headline, an explicit banner if the payload is `stale`/`degraded`, no card showing a fabricated `0`, and no benchmark statistics rendered when the SPY series is unavailable.

**F12 — plumbing, without which none of the above ships.** These are separate commits but are part of acceptance:

1. `perf_twr_builder.py` gets a caller: a `radon-perf-twr.{service,timer}` unit in `cloud/services/` (weekday, 20:45 ET, after the Flex settlement window), installed per `reference_vps_root_ssh_unit_installs` including the `installed-units.sha256` bump. A builder with no caller is defect #9 and re-opens on every deploy otherwise.
2. `POST /performance` in `scripts/api/server.py:3909-3915` runs `perf_twr_builder.py`, not `portfolio_performance.py`. Exactly one writer for `performance_snapshots`.
3. `portfolio_performance.py`'s `mirror_scan_snapshot("performance", ...)` is removed or re-pointed at a distinct `scan_type`. Two schemas in one table is unrecoverable at read time.
4. `taken_at` is a **full ISO-8601 UTC instant** from every writer (`payload["generated_at"]`, never a date). The date-only-vs-ISO lexicographic race that made the stale TWR row permanently win is closed.
5. `persist_payload` writes `nav_snapshots`, `external_flows`, and `twr_subperiods` — the three tables migration `0035_perf_twr.sql` created and nothing has ever filled (all 0 rows in production). Verify non-zero row counts in Turso before calling it done, per the repo's data-persistence rule.
6. Turso is the verification target, not `data/*.json`.

---

## G. IMPLEMENTATION ORDER (red/green, one commit per step)

1. `scripts/lib/twr_gates.py` + TS mirror + `tests/test_twr_gate_parity.py`. Red first.
2. `scripts/lib/flex_flows.py` + `tests/test_flex_flows.py` (E.2). Includes the `<Transfer>` fix.
3. `scripts/lib/twr_math.py` + `tests/test_twr_math.py` (E.1, E.3, E.4, E.5, E.7). Golden fixture E.6 last in this step.
4. Rewrite `perf_twr_builder.py` against the pure layer; add the integrity gates and the v2 payload (E.8). Delete everything in §A.2.
5. Web layer: `performanceTwr.ts`, `performanceData.ts`, `performanceChart.ts`, both panels (E.9).
6. Plumbing F12.1–F12.6.
7. Live verification F11, then the acceptance sweep F1–F10.

No step may be skipped or reordered: steps 4 and 5 are only safe once the pure layer is pinned, and step 6 is what makes the fix reach production.

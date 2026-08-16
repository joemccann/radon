"""Gate thresholds for the performance stack. Mirrored in web/lib/performanceTwr.ts.

Any edit here MUST be reflected there; the gate-parity tests fail the build on
drift. This file is the only place a performance threshold is written in Python.
Numeric gates carry an explicit `int` / `float` annotation because that is what
the parity check keys on; the two non-numeric conventions deliberately do not.
"""

TRADING_DAYS: int = 252            # sessions/yr — volatility & ratio scaling ONLY
DAYS_PER_YEAR: float = 365.0       # Act/365 Fixed — return annualization & XIRR ONLY

MIN_N_CHAIN: int = 1               # cum_twr
MIN_N_DISPERSION: int = 20         # volatility, max_drawdown, var_95, cvar_95, distribution
MIN_N_BENCHMARK: int = 40          # every benchmark-derived statistic (n_common, not n_returns)
MIN_N_RATIO: int = 60              # sharpe, sortino
MIN_N_MWR: int = 20                # mwr_period and mwr_annualized
MIN_DOWNSIDE_OBSERVATIONS: int = 5  # additional sortino condition
MIN_CALENDAR_DAYS_ANNUALIZED: int = 365   # GIPS: never annualize a sub-year period
BENCHMARK_MIN_COVERAGE: float = 0.90      # n_common / n_returns
VAR_LOW_CONFIDENCE_N: int = 100           # below this, var/cvar carry low_confidence
RATIO_LOW_CONFIDENCE_N: int = 252         # below this, every ratio carries low_confidence

SUSPECT_RETURN_THRESHOLD: float = 0.50    # |r_t| above this => quarantine, whatever C_t is
UNEXPLAINED_OUTLIER_MULTIPLE: float = 5.0  # |r_t| above this many median sessions => quarantine
UNEXPLAINED_TAIL_QUANTILE: float = 0.95   # the account's own "normal extreme" session
UNEXPLAINED_TAIL_MULTIPLE: float = 3.0    # ...or above this many p95 sessions, whichever is wider
UNEXPLAINED_ABSOLUTE_FLOOR: float = 0.10   # the dispersion bar never falls below this
FLOW_DOMINANT_RATIO: float = 0.25         # |C_t| / denominator above this => flag the subperiod
IMPLAUSIBLE_ANNUALIZED: float = 10.0      # |annualized| > 1000%/yr => degraded
IMPLAUSIBLE_ALPHA: float = 1.0            # |alpha_annualized| > 100%/yr => degraded
MAX_SUBPERIOD_GAP_DAYS: int = 4           # Fri->Mon is 3; >4 is a missing session
NAV_STALENESS_BUDGET_SESSIONS: int = 2    # Flex settles T+1; 2 sessions of slack

FLOW_CONVENTION = "eod"
SORTINO_TARGET = 0.0

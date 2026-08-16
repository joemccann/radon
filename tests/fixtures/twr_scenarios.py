"""Deterministic NAV / flow / Flex-XML scenarios for the TWR refactor suite.

This module holds **inputs only**. Not one expected value lives here — every
expectation is a hand-computed literal written at the assertion site, so a
reader can check the arithmetic without leaving the test.

Two date policies, deliberately mixed:

* **Window-relative** (`calendar_run`, `weekday_run(today_anchored=True)`) for
  scenarios where the absolute date carries no meaning. Hard-coded dates rot
  in CI; consecutive *calendar* days are used so `calendar_days == count - 1`
  stays deterministic regardless of which weekday the suite runs on.
* **Absolute** for the two series the spec pins to real production data: the
  60-session golden path (2026-01-02 .. 2026-03-27) and the live defect replay
  (2025-12-31 .. 2026-03-20, `taken_at 2026-08-15T14:55:40Z`).

Nothing here imports the code under test, so an import error in a test module
is always the module under test, never this file.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def iso(d: date) -> str:
    return d.isoformat()


def calendar_run(count: int, *, end_offset_days: int = 1) -> List[str]:
    """`count` consecutive CALENDAR days ending `end_offset_days` before today.

    Consecutive calendar days keep `calendar_days == count - 1` and every
    `gap_days == 1`, so weekend/holiday behavior never leaks into a scenario
    that is not about weekends.
    """
    end = date.today() - timedelta(days=end_offset_days)
    return [iso(end - timedelta(days=count - 1 - i)) for i in range(count)]


def weekday_run(start: date, count: int) -> List[str]:
    """`count` consecutive Mon-Fri dates starting at (or after) `start`."""
    out: List[str] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(iso(cursor))
        cursor += timedelta(days=1)
    return out


def monday_anchored_weekdays(count: int, *, weeks_back: int = 60) -> List[str]:
    """`count` Mon-Fri dates beginning on the Monday `weeks_back` weeks ago.

    Anchoring on a Monday is what makes the CALENDAR span deterministic while
    the dates stay window-relative: 263 weekdays from a Monday is 52 whole
    weeks plus two days, i.e. exactly 366 calendar days, whichever weekday the
    suite happens to run on. A span pinned above 365 is what lets an
    annualization assertion exist at all.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday(), weeks=weeks_back)
    return weekday_run(monday, count)


def series(dates: Sequence[str], values: Sequence[float]) -> Dict[str, float]:
    if len(dates) != len(values):
        raise ValueError(f"dates/values length mismatch: {len(dates)} vs {len(values)}")
    return dict(zip(dates, values))


# ---------------------------------------------------------------------------
# E.1 — small chain scenarios (window-relative)
# ---------------------------------------------------------------------------


def flat_nav() -> Dict[str, float]:
    """E.1 #1 — three flat observations, no flows."""
    return series(calendar_run(3), [100_000.0, 100_000.0, 100_000.0])


def growth_then_loss_nav() -> Dict[str, float]:
    """E.1 #2 — 100 -> 110 -> 99. Pins that 1.10 * 0.90 - 1 is -0.01, not 0."""
    return series(calendar_run(3), [100.0, 110.0, 99.0])


def nav_to_zero() -> Dict[str, float]:
    """E.1 #11 — total loss then a zero base."""
    return series(calendar_run(3), [100_000.0, 0.0, 0.0])


def negative_nav() -> Dict[str, float]:
    """E.1 #12 — a negative begin NAV must skip, not invert the sign."""
    return series(calendar_run(2), [-5_000.0, 1_000.0])


def single_observation() -> Dict[str, float]:
    """E.1 #13 — one NAV row yields zero subperiods."""
    return series(calendar_run(1), [100_000.0])


def drawdown_nav() -> Dict[str, float]:
    """E.3 #39 — returns +0.10, -0.20, +0.05 with no flows.

    110/100 - 1 = +0.10 ; 88/110 - 1 = -0.20 ; 92.4/88 - 1 = +0.05
    """
    return series(calendar_run(4), [100.0, 110.0, 88.0, 92.4])


# The 2026-02-06 production event, isolated. Kept absolute: the spec pins it.
FEB06_BEGIN_NAV = 246_713.50
FEB06_END_NAV = 972_215.53
FEB06_FLOW = 725_000.00
# 972215.53 - 246713.50 = 725502.03  -> the INFERRED_FLOW_CANDIDATE amount
FEB06_NAV_DELTA = 725_502.03
FEB06_DATE = "2026-02-06"
FEB06_PRIOR_DATE = "2026-02-05"


def feb06_nav() -> Dict[str, float]:
    return {FEB06_PRIOR_DATE: FEB06_BEGIN_NAV, FEB06_DATE: FEB06_END_NAV}


def withdrawal_nav() -> Dict[str, float]:
    """E.1 #4 — B=100000, E=90000, C=-15000."""
    return series(calendar_run(2), [100_000.0, 90_000.0])


def same_day_both_ways_nav() -> Dict[str, float]:
    """E.1 #5 — B=100000, E=131000, deposits +50000 and withdrawals -20000."""
    return series(calendar_run(2), [100_000.0, 131_000.0])


def first_day_flow_nav() -> Dict[str, float]:
    """E.1 #9 — a flow dated d_0 is already inside N_0."""
    return series(calendar_run(2), [100_000.0, 101_000.0])


def last_day_flow_nav() -> Dict[str, float]:
    """E.1 #10 — a flow dated d_M lands inside N_M."""
    return series(calendar_run(2), [100_000.0, 151_000.0])


# Weekend / holiday gaps need real weekday alignment, so these stay absolute.
WEEKEND_GAP_NAV = {"2026-01-09": 100_000.0, "2026-01-12": 101_000.0}  # Fri -> Mon
HOLIDAY_GAP_NAV = {"2026-01-08": 100_000.0, "2026-01-14": 101_000.0}  # Thu -> Wed
SATURDAY_FLOW_NAV = {"2026-01-09": 100_000.0, "2026-01-12": 151_000.0}
SATURDAY_FLOW = {"2026-01-10": 50_000.0}  # Saturday, no NAV row


# ---------------------------------------------------------------------------
# E.1 #22 — consolidation with a missing account-date
# ---------------------------------------------------------------------------

CONSOLIDATED_NAV = {
    "U000A": {"2026-01-12": 120_000.0, "2026-01-13": 125_000.0, "2026-01-14": 126_000.0},
    "U000B": {"2026-01-12": 80_000.0, "2026-01-14": 84_000.0},  # 01-13 missing
}
CONSOLIDATED_FLOWS = {
    "U000A": {"2026-01-14": -10_000.0},
    "U000B": {"2026-01-14": 10_000.0},  # internal transfer, nets to zero
}


# ---------------------------------------------------------------------------
# E.3 — statistics series
# ---------------------------------------------------------------------------

# Five-observation pin series used across E.3.
STATS_RETURNS = [0.01, -0.02, 0.03, 0.00, -0.01]

# E.3a — twenty observations, already sorted ascending.
VAR_RETURNS_SORTED = [
    -0.05, -0.04, -0.03, -0.02, -0.01,
    0.00, 0.00, 0.01, 0.01, 0.01,
    0.01, 0.01, 0.01, 0.01, 0.01,
    0.02, 0.02, 0.02, 0.03, 0.04,
]

# E.4 — benchmark daily returns aligned 1:1 with STATS_RETURNS.
BENCH_RETURNS = [0.008, -0.015, 0.02, 0.002, -0.005]


# ---------------------------------------------------------------------------
# E.6 — golden path, 60 sessions
# ---------------------------------------------------------------------------

GOLDEN_START = date(2026, 1, 2)
GOLDEN_N_OBSERVATIONS = 61
GOLDEN_SEED_NAV = 100_000.00
GOLDEN_RETURN_PATTERN = (0.006, -0.004, 0.002, -0.008, 0.010, -0.001)
GOLDEN_FLOWS: Dict[str, float] = {"2026-02-13": 100_000.00, "2026-03-06": -25_000.00}
GOLDEN_RF_ANNUAL = 0.02

# Spec-declared NAV checkpoints. The generator below must reproduce these; a
# test asserts it, which is what makes the generated series trustworthy input.
GOLDEN_NAV_CHECKPOINTS: Dict[int, float] = {
    29: 102_577.56510647341,
    30: 202_474.98754136695,
    44: 204_868.8123253928,
    45: 180_278.54995004358,
    60: 182_217.35574011656,
}


def golden_dates() -> List[str]:
    return weekday_run(GOLDEN_START, GOLDEN_N_OBSERVATIONS)


def golden_returns() -> List[float]:
    return list(GOLDEN_RETURN_PATTERN) * 10


def golden_nav() -> Dict[str, float]:
    """NAV rolled forward under the EOD convention: N_t = N_{t-1}*(1+r_t) + C_t.

    This is the spec's own construction of the fixture INPUT, not a
    reimplementation of anything under test: it is the inverse of the return
    formula, and the resulting series is pinned against
    `GOLDEN_NAV_CHECKPOINTS`.
    """
    return _roll_forward(GOLDEN_FLOWS)


def golden_nav_no_flows() -> Dict[str, float]:
    """The same 60-session return pattern rolled forward with NO external flows.

    Required by any scenario asserting `FlowsStatus.EMPTY_VERIFIED`: feeding
    `golden_nav()` (which embeds a +100k deposit) alongside "verified: no
    flows" is self-contradictory, and spec §B.4 rightly quarantines the
    resulting +97% flow-free session.
    """
    return _roll_forward({})


def _roll_forward(flows: Dict[str, float]) -> Dict[str, float]:
    dates = golden_dates()
    rets = golden_returns()
    nav: Dict[str, float] = {dates[0]: GOLDEN_SEED_NAV}
    running = GOLDEN_SEED_NAV
    for i, d in enumerate(dates[1:]):
        running = running * (1.0 + rets[i]) + flows.get(d, 0.0)
        nav[d] = running
    return nav


# ---------------------------------------------------------------------------
# E.8 — live production replay (performance_snapshots, 2026-08-15T14:55:40Z)
# ---------------------------------------------------------------------------

LIVE_START = date(2025, 12, 31)
LIVE_N_OBSERVATIONS = 58

# Real values read off the live payload's twr_subperiods. Index positions are
# into `live_dates()`; the anchor dates are asserted in the test module.
LIVE_NAV_ANCHORS: Dict[int, float] = {
    0: 99_492.93647617,     # 2025-12-31, period_start
    8: 106_680.59,          # 2026-01-12, b of the 01-13 subperiod
    9: 185_755.43,          # 2026-01-13, e (live r = +0.7412 with c = 0)
    17: 189_502.12,         # 2026-01-23, b of the 01-26 subperiod
    18: 232_497.53,         # 2026-01-26, e (live r = +0.2269 with c = 0)
    26: 246_713.50,         # 2026-02-05, b of the 02-06 subperiod
    27: 972_215.53,         # 2026-02-06, e (live r = +2.9407 with c = 0)
    57: 1_045_949.48105117,  # 2026-03-20, period_end
}

LIVE_ANCHOR_DATES: Dict[int, str] = {
    0: "2025-12-31",
    8: "2026-01-12",
    9: "2026-01-13",
    17: "2026-01-23",
    18: "2026-01-26",
    26: "2026-02-05",
    27: "2026-02-06",
    57: "2026-03-20",
}

LIVE_FLOWS: Dict[str, float] = {
    # Real production cash_flows row: "Deposits/Withdrawals",
    # "ADJUSTMENT: DEPOSIT ADVANCE".
    "2026-01-13": 80_007.13,
    # Fixture stand-in for an un-fetched Deposits/Withdrawals row.
    "2026-01-26": 42_000.00,
    # Fixture stand-in for the ACATS <Transfer> that has no CashTransaction
    # row at all. 725000.00 (not the 725502.03 NAV delta) is the amount the
    # spec pins r = 0.0020348704063621486 against; the 502.03 residue is the
    # session's real investment P&L. Replace with the reported
    # positionAmountInBase when the Transfers section is available, and
    # re-derive the pin rather than relaxing it.
    "2026-02-06": 725_000.00,
}

LIVE_CONTAMINATED_CUM_RETURN = 9.5128  # what the live payload published
LIVE_CONTAMINATED_ANNUALIZED = 32_889.5462  # +3,288,954.62% on screen


def live_dates() -> List[str]:
    return weekday_run(LIVE_START, LIVE_N_OBSERVATIONS)


def interpolated_live_nav() -> Dict[str, float]:
    """58-point SYNTHETIC series: the real anchors, geometrically interpolated.

    NOT the production series. Only the eight anchors are production values;
    the 50 filler points are a smooth geometric ramp between them, which
    flattens the account's own dispersion by ~13x (median |r| 0.00243973
    against the real 0.03252648). Any test whose subject is dispersion — the
    outlier bar, the suspect quarantine, n_suspect — must use
    `real_live_nav()` instead, or it pins a property of the interpolation.

    Keep this one only where SMOOTHNESS is the point: date/anchor alignment and
    the three anchor jumps in isolation.
    """
    dates = live_dates()
    anchor_idx = sorted(LIVE_NAV_ANCHORS)
    nav: Dict[str, float] = {}
    for lo, hi in zip(anchor_idx, anchor_idx[1:]):
        v_lo, v_hi = LIVE_NAV_ANCHORS[lo], LIVE_NAV_ANCHORS[hi]
        span = hi - lo
        step = (v_hi / v_lo) ** (1.0 / span)
        for k in range(span):
            nav[dates[lo + k]] = v_lo * (step ** k)
        nav[dates[hi]] = v_hi
    return nav


# ---------------------------------------------------------------------------
# E.8b — the REAL production NAV series (audit round 3, R4)
# ---------------------------------------------------------------------------

# Transcribed verbatim from `data/nav_history_ib.json` (58 rows,
# 2025-12-31 .. 2026-03-20) — the same 58 dates as `interpolated_live_nav()`,
# but 50 of the values differ because that fixture interpolates them.
# Do not round, do not smooth, do not regenerate: the point of this series is
# that it carries the account's real session-to-session dispersion.
REAL_LIVE_NAV: Dict[str, float] = {
    "2025-12-31": 99492.93647617,
    "2026-01-01": 99492.93647617,
    "2026-01-02": 100093.43647617,
    "2026-01-05": 102612.81647617,
    "2026-01-06": 104954.69647617,
    "2026-01-07": 103632.34647617,
    "2026-01-08": 103499.08647617,
    "2026-01-09": 106018.23647617,
    "2026-01-12": 106680.58647617,
    "2026-01-13": 185755.42647617,
    "2026-01-14": 183753.42647617,
    "2026-01-15": 182051.59199617,
    "2026-01-16": 178787.13175117,
    "2026-01-19": 178787.13175117,
    "2026-01-20": 168882.30175117,
    "2026-01-21": 186918.16175117,
    "2026-01-22": 186702.64175117,
    "2026-01-23": 189502.12175117,
    "2026-01-26": 232497.52580117,
    "2026-01-27": 254824.85580117,
    "2026-01-28": 242399.81336117,
    "2026-01-29": 224775.79836117,
    "2026-01-30": 241103.96246117,
    "2026-02-02": 266122.87002117,
    "2026-02-03": 274778.91102117,
    "2026-02-04": 280286.37292117,
    "2026-02-05": 246713.49538117,
    "2026-02-06": 972215.52538117,
    "2026-02-09": 1015320.44538117,
    "2026-02-10": 1009388.58538117,
    "2026-02-11": 966850.88538117,
    "2026-02-12": 913374.20538117,
    "2026-02-13": 953424.65669117,
    "2026-02-16": 953424.65669117,
    "2026-02-17": 920151.34669117,
    "2026-02-18": 913724.76669117,
    "2026-02-19": 913009.28669117,
    "2026-02-20": 908988.06669117,
    "2026-02-23": 842340.46669117,
    "2026-02-24": 879380.92669117,
    "2026-02-25": 937005.15029117,
    "2026-02-26": 970650.89029117,
    "2026-02-27": 981474.59029117,
    "2026-03-02": 1090036.88839117,
    "2026-03-03": 1083061.70224117,
    "2026-03-04": 1192477.45745117,
    "2026-03-05": 1191800.81398117,
    "2026-03-06": 1147892.00029117,
    "2026-03-09": 1249059.36279117,
    "2026-03-10": 1294642.28735117,
    "2026-03-11": 1301139.10735117,
    "2026-03-12": 1232373.40992117,
    "2026-03-13": 1171278.48992117,
    "2026-03-16": 1199112.00042117,
    "2026-03-17": 1195547.72042117,
    "2026-03-18": 1147413.47070117,
    "2026-03-19": 1148931.51970117,
    "2026-03-20": 1045949.48105117,
}

# The three sessions the production series cannot explain without a flow.
REAL_UNEXPLAINED_DATES = ("2026-01-13", "2026-01-26", "2026-02-06")


def real_live_nav() -> Dict[str, float]:
    """The production NAV series exactly as IB reported it."""
    return dict(REAL_LIVE_NAV)


# ---------------------------------------------------------------------------
# Audit round 3 — R1: the "flow explains 25% of the move" cliff
# ---------------------------------------------------------------------------

CLIFF_N_OBSERVATIONS = 263
CLIFF_BEGIN_NAV = 259_879.31
CLIFF_END_NAV = 985_485.30
# 985_485.30 - 259_879.31
CLIFF_MOVE = 725_605.99
# Straddles 0.25 * CLIFF_MOVE = 181_401.4975 from either side.
CLIFF_QUARANTINING_FLOW = 181_000.0
CLIFF_ESCAPING_FLOW = 182_000.0


def cliff_dates() -> List[str]:
    return monday_anchored_weekdays(CLIFF_N_OBSERVATIONS)


def cliff_nav() -> Dict[str, float]:
    """262 sessions alternating +/-1% around CLIFF_BEGIN_NAV, then one jump.

    The alternation gives the dispersion machinery a real sample (median
    |r| = 1/1.01 - 1) while keeping the pre-jump chain exactly 1/1.01, so the
    published cum_return is hand-checkable in one line. The last observation
    is the discontinuity the recorded flow is supposed to explain.
    """
    dates = cliff_dates()
    nav = {
        d: (CLIFF_BEGIN_NAV * 1.01 if index % 2 == 0 else CLIFF_BEGIN_NAV)
        for index, d in enumerate(dates[:-1])
    }
    nav[dates[-1]] = CLIFF_END_NAV
    return nav


# ---------------------------------------------------------------------------
# Audit round 3 — R2: NAV to exactly zero on the final subperiod
# ---------------------------------------------------------------------------


def terminal_zero_nav() -> Dict[str, float]:
    """R2 — a live account whose last NAV row is exactly 0.0."""
    return series(calendar_run(2), [100_000.0, 0.0])


# ---------------------------------------------------------------------------
# Audit round 3 — R3: the dispersion bar disabling itself
# ---------------------------------------------------------------------------

YOUNG_N_OBSERVATIONS = 15          # 14 returns, below MIN_N_DISPERSION
YOUNG_BASE_NAV = 196_685.31
UNRECORDED_DEPOSIT = 42_995.41

FLAT_N_OBSERVATIONS = 23           # 22 returns, above MIN_N_DISPERSION
FLAT_BASE_NAV = 189_502.12
FLAT_END_NAV = 232_497.53


def young_account_nav() -> Dict[str, float]:
    """15 sessions, flat, ending on one unrecorded UNRECORDED_DEPOSIT deposit."""
    dates = calendar_run(YOUNG_N_OBSERVATIONS)
    nav = {d: YOUNG_BASE_NAV for d in dates[:-1]}
    nav[dates[-1]] = YOUNG_BASE_NAV + UNRECORDED_DEPOSIT
    return nav


def flat_account_nav() -> Dict[str, float]:
    """23 flat sessions, then the real 189,502.12 -> 232,497.53 deposit jump."""
    dates = calendar_run(FLAT_N_OBSERVATIONS)
    nav = {d: FLAT_BASE_NAV for d in dates[:-1]}
    nav[dates[-1]] = FLAT_END_NAV
    return nav


# ---------------------------------------------------------------------------
# E.7 — gate boundary series
# ---------------------------------------------------------------------------


def boundary_returns(n: int) -> List[float]:
    """`n` returns cycling the golden six-day pattern.

    A varied pattern (rather than a constant +0.001) keeps each boundary test
    about the GATE and not about a degenerate zero-variance series.
    """
    pattern = list(GOLDEN_RETURN_PATTERN)
    return [pattern[i % len(pattern)] for i in range(n)]


def returns_with_downside_count(n: int, downside: int) -> List[float]:
    """`n` returns of which exactly `downside` are negative."""
    if downside > n:
        raise ValueError("downside cannot exceed n")
    return [-0.001] * downside + [0.001] * (n - downside)


def nav_from_returns(returns: Sequence[float], *, seed: float = 100_000.0) -> Dict[str, float]:
    """Roll `returns` into a flow-free NAV series on consecutive calendar days."""
    dates = calendar_run(len(returns) + 1)
    nav = {dates[0]: seed}
    running = seed
    for i, d in enumerate(dates[1:]):
        running *= 1.0 + returns[i]
        nav[d] = running
    return nav


# ---------------------------------------------------------------------------
# Flex XML builders (E.2, B.2)
# ---------------------------------------------------------------------------


def _attrs(mapping: Mapping[str, object]) -> str:
    return " ".join(f'{k}="{v}"' for k, v in mapping.items() if v is not None)


def cash_transaction(
    *, type_: str, amount: float, report_date: str = "2026-01-13", currency: str = "USD"
) -> Mapping[str, object]:
    return {"type": type_, "amount": amount, "reportDate": report_date, "currency": currency}


ACATS_IN = {
    "type": "ACATS",
    "direction": "IN",
    "assetCategory": "STK",
    "reportDate": "2026-02-06",
    "quantity": "1000",
    "transferPrice": "725.00",
    "positionAmountInBase": "725000.00",
    "cashTransfer": "0",
}

ACATS_OUT = dict(ACATS_IN, direction="OUT")

ACATS_NO_DIRECTION = {k: v for k, v in ACATS_IN.items() if k != "direction"}

CASH_TRANSFER_IN = {
    "type": "INTERNAL",
    "direction": "IN",
    "assetCategory": "CASH",
    "reportDate": "2026-02-06",
    "cashTransfer": "42000.00",
    "transferPrice": "0",
}

TRANSFER_NO_AMOUNT = {
    "type": "FOP",
    "direction": "IN",
    "assetCategory": "STK",
    "reportDate": "2026-02-06",
    "quantity": "1000",
    "transferPrice": "725.00",
}


def flex_xml(
    *,
    cash_transactions: Iterable[Mapping[str, object]] = (),
    transfers: Iterable[Mapping[str, object]] = (),
    include_cash_section: bool = True,
    include_transfer_section: bool = True,
    account_id: str = "U1234567",
) -> str:
    """A minimal but structurally faithful Flex activity statement."""
    parts: List[str] = [
        "<FlexQueryResponse queryName='RadonFlows' type='AF'>",
        '<FlexStatements count="1">',
        f'<FlexStatement accountId="{account_id}" fromDate="2026-01-01" toDate="2026-03-20">',
    ]
    if include_cash_section:
        parts.append("<CashTransactions>")
        for ct in cash_transactions:
            parts.append(f"<CashTransaction {_attrs(ct)} />")
        parts.append("</CashTransactions>")
    if include_transfer_section:
        parts.append("<Transfers>")
        for tr in transfers:
            parts.append(f"<Transfer {_attrs(tr)} />")
        parts.append("</Transfers>")
    parts += ["</FlexStatement>", "</FlexStatements>", "</FlexQueryResponse>"]
    return "".join(parts)


def flex_xml_nav_only() -> str:
    """A NAV-only Flex query: no CashTransactions and no Transfers section.

    This is defect #1's second face — the document parses cleanly and yields
    zero flows, which must be FAILED, never EMPTY_VERIFIED.
    """
    return (
        "<FlexQueryResponse queryName='RadonNav' type='AF'>"
        '<FlexStatements count="1">'
        '<FlexStatement accountId="U1234567">'
        "<EquitySummaryInBase>"
        '<EquitySummaryByReportDateInBase reportDate="2026-03-20" total="1045949.48" />'
        "</EquitySummaryInBase>"
        "</FlexStatement></FlexStatements></FlexQueryResponse>"
    )


# IBKR `type` strings observed live in Turso `cash_flows` (261 rows / 4 values).
LIVE_INTERNAL_TYPES = [
    "Other Fees",
    "Dividends",
    "Payment In Lieu Of Dividends",
    "Broker Interest Received",
    "Broker Interest Paid",
    "Withholding Tax",
]

LIVE_EXTERNAL_TYPES = [
    "Deposits/Withdrawals",
    "  deposits   &   withdrawals ",
    "Deposits and Withdrawals",
    "Internal Transfers",
]

# Strings the retired substring matcher would have swallowed as external.
SUBSTRING_TRAP_TYPES = ["Deposit Advance Reversal", "Withdrawal Fee Rebate"]

UNKNOWN_TYPES = ["Sharebuilder", "Fractional Share Liquidation", ""]


# ---------------------------------------------------------------------------
# Adversarial-audit inputs (docs/performance-audit-findings.md)
#
# Each block below is the exact input a reviewer used to reproduce a defect.
# Inputs only, as everywhere else in this module: the expected values live at
# the assertion sites in tests/test_perf_twr_ingest.py.
# ---------------------------------------------------------------------------

AUDIT_START = date(2026, 1, 2)

DORMANT_N_OBSERVATIONS = 46
DORMANT_NAV_VALUE = 100_000.0


def dormant_nav() -> Dict[str, float]:
    """math-D1 — 46 weekday rows of an unchanged NAV (a cash-only account).

    46 observations give 45 returns, which clears MIN_N_BENCHMARK (40) at full
    coverage, so the benchmark block is built from a CONSTANT portfolio series.
    """
    return {d: DORMANT_NAV_VALUE for d in weekday_run(AUDIT_START, DORMANT_N_OBSERVATIONS)}


def moving_benchmark_closes(dates: Sequence[str], *, seed: float = 500.0) -> Dict[str, float]:
    """A SPY close series that moves on every date, rolled off the golden pattern."""
    closes: Dict[str, float] = {}
    running = seed
    for i, d in enumerate(dates):
        if i:
            running *= 1.0 + GOLDEN_RETURN_PATTERN[(i - 1) % len(GOLDEN_RETURN_PATTERN)]
        closes[d] = running
    return closes


ZEROED_ROW_N_OBSERVATIONS = 30
ZEROED_ROW_INDEX = 15
ZEROED_ROW_DAILY_RETURN = 0.002


def nav_with_a_zeroed_row() -> Dict[str, float]:
    """math-D2 — 30 weekday NAVs rising 0.2%/day with row 15 zeroed.

    A blank Flex `total` attribute is what manufactures the 0.0 (flows-D6);
    this fixture is the resulting series.
    """
    dates = weekday_run(AUDIT_START, ZEROED_ROW_N_OBSERVATIONS)
    nav: Dict[str, float] = {}
    running = 100_000.0
    for i, d in enumerate(dates):
        if i:
            running *= 1.0 + ZEROED_ROW_DAILY_RETURN
        nav[d] = 0.0 if i == ZEROED_ROW_INDEX else running
    return nav


def zeroed_row_date() -> str:
    return weekday_run(AUDIT_START, ZEROED_ROW_N_OBSERVATIONS)[ZEROED_ROW_INDEX]


# flows-D1 — a $1 CashTransaction on the same date as the unrecorded 725,502.03
# ACATS. Any nonzero flow currently switches the suspect quarantine off.
FEB06_TOKEN_FLOW = 1.00

# flows-D3 — the real 2026-01-23 -> 2026-01-26 pair (LIVE_NAV_ANCHORS 17 / 18).
JAN26_NAV: Dict[str, float] = {"2026-01-23": 189_502.12, "2026-01-26": 232_497.53}

# tests-D2 — a doubling session whose transfer is only partially recorded.
DOUBLING_NAV: Dict[str, float] = {"2026-01-05": 100_000.0, "2026-01-06": 200_000.0}
DOUBLING_TOKEN_FLOW = 1.00
DOUBLING_MATERIAL_FLOW = 40_000.00

# flows-D2 — external CashTransaction rows IBKR really emits that the parser
# drops on the floor, turning "we could not read this deposit" into
# "this account has no deposits".
MALFORMED_EXTERNAL_CASH_ROWS: Dict[str, Mapping[str, object]] = {
    "comma_amount": {
        "type": "Deposits/Withdrawals",
        "amount": "725,502.03",
        "reportDate": "20260206",
        "currency": "USD",
    },
    "amount_in_base_only": {
        "type": "Deposits/Withdrawals",
        "amountInBase": "725502.03",
        "reportDate": "20260206",
        "currency": "USD",
    },
    "us_format_date": {
        "type": "Deposits/Withdrawals",
        "amount": "725502.03",
        "reportDate": "02/06/2026",
        "currency": "USD",
    },
}

INTERNAL_FEE_ROW: Mapping[str, object] = {
    "type": "Other Fees",
    "amount": "-1.25",
    "reportDate": "20260126",
    "currency": "USD",
}

# flows-D5 — two accounts in one statement. NAV is last-statement-wins today
# while flows sum every account, which published a -284.5% TWR.
MULTI_ACCOUNT_NAV: Dict[str, Dict[str, object]] = {
    "U111": {"2026-01-05": 100_000.0, "2026-01-06": 101_000.0},
    "U222": {"2026-01-05": 20_000.0, "2026-01-06": 20_100.0},
}
MULTI_ACCOUNT_DEPOSITS: Dict[str, List[Mapping[str, object]]] = {
    "U111": [
        {
            "type": "Deposits/Withdrawals",
            "amount": "50000.00",
            "reportDate": "20260106",
            "currency": "USD",
        }
    ],
    "U222": [
        {
            "type": "Deposits/Withdrawals",
            "amount": "7000.00",
            "reportDate": "20260106",
            "currency": "USD",
        }
    ],
}

# tests-D5 — the same group with U222 missing 2026-01-06 entirely.
ACCOUNT_GAP_NAV: Dict[str, Dict[str, object]] = {
    "U111": {"2026-01-05": 100_000.0, "2026-01-06": 101_000.0, "2026-01-07": 102_000.0},
    "U222": {"2026-01-05": 20_000.0, "2026-01-07": 20_400.0},
}

# flows-D6 — a blank `total` attribute, which `_safe_float` turns into 0.0.
BLANK_TOTAL_NAV: Dict[str, Dict[str, object]] = {
    "U111": {"2026-01-05": 100_000.0, "2026-01-06": "", "2026-01-07": 101_500.0}
}


def flex_statement_xml(
    *,
    accounts: Mapping[str, Mapping[str, object]],
    cash_transactions: Optional[Mapping[str, Iterable[Mapping[str, object]]]] = None,
    include_cash_section: bool = True,
    include_transfer_section: bool = False,
) -> str:
    """A Flex activity statement carrying NAV, and optionally flows, per account.

    One `<FlexStatement>` per account is exactly how IBKR emits a consolidated
    query, and is what makes the last-statement-wins NAV parse observable.
    """
    parts: List[str] = [
        "<FlexQueryResponse queryName='RadonPerf' type='AF'>",
        f'<FlexStatements count="{len(accounts)}">',
    ]
    for account_id, nav in accounts.items():
        parts.append(
            f'<FlexStatement accountId="{account_id}" fromDate="2026-01-01" toDate="2026-03-20">'
        )
        parts.append("<EquitySummaryInBase>")
        for report_date, total in nav.items():
            parts.append(
                "<EquitySummaryByReportDateInBase "
                f'accountId="{account_id}" reportDate="{report_date}" total="{total}" />'
            )
        parts.append("</EquitySummaryInBase>")
        if include_cash_section:
            parts.append("<CashTransactions>")
            for ct in (cash_transactions or {}).get(account_id, ()):
                parts.append(f'<CashTransaction accountId="{account_id}" {_attrs(ct)} />')
            parts.append("</CashTransactions>")
        if include_transfer_section:
            parts.append("<Transfers></Transfers>")
        parts.append("</FlexStatement>")
    parts += ["</FlexStatements>", "</FlexQueryResponse>"]
    return "".join(parts)

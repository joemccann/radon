"""Pure TWR math — the fixture contract from the Performance/TWR spec §E.

RED SUITE. `scripts.lib.twr_math` and `scripts.lib.twr_gates` do not exist yet;
an ImportError here is the expected first failure.

Every expected value below is hand-computed and written as a literal with the
arithmetic in a comment. No expectation is produced by calling the function
under test, and no formula is reimplemented inline to derive one.

Assumed public surface (spec §A.4.2). Each statistic accepts a `min_n` keyword
that defaults to the value in `scripts.lib.twr_gates`; tests that pin
arithmetic override it so the numbers are testable independently of the gates.
"""

from __future__ import annotations

import ast
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.lib import twr_gates
from scripts.lib.twr_math import (
    AlignedPairs,
    DatedCashflow,
    DuplicateNavDate,
    GatedValue,
    NavObservation,
    NonFiniteInput,
    align_series,
    annualize_return,
    build_benchmark_block,
    build_cashflow_vector,
    build_subperiods,
    chain_returns,
    conditional_var,
    consolidate_accounts,
    daily_risk_free,
    downside_deviation,
    drawdowns,
    historical_var,
    modified_dietz,
    return_distribution,
    sharpe_ratio,
    sortino_ratio,
    subperiod_return,
    volatility,
    xirr,
)

from fixtures import twr_scenarios as fx  # noqa: E402  (tests/ dir, inserted above)

REL = 1e-9
TIGHT = 1e-12


# ---------------------------------------------------------------------------
# helpers (construction only — never expectation-producing)
# ---------------------------------------------------------------------------


def observations(nav_map):
    """Sorted-by-nothing NavObservation tuple; build_subperiods owns ordering."""
    return tuple(NavObservation(date=d, nav=float(v)) for d, v in nav_map.items())


def by_date(chain):
    return {sp.date: sp for sp in chain.subperiods}


def shift(iso_date: str, days: int) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


# ===========================================================================
# E.1 — subperiod / chain scenarios
# ===========================================================================


def test_01_flat_nav_yields_two_zero_returns():
    nav = fx.flat_nav()
    chain = build_subperiods(observations(nav), {})

    assert chain.n_nav_observations == 3
    assert chain.n_subperiods == 2  # invariant: observations - 1, no synthetic seed
    assert chain.n_returns == 2
    # (100000 - 0 - 100000) / 100000 = 0.0, twice
    assert chain.returns == (0.0, 0.0)
    assert chain.cum_return == 0.0  # exact: 1.0 * 1.0 - 1
    assert chain.excluded_suspect is False

    # N=2 is below MIN_N_DISPERSION (20)
    vol = volatility(list(chain.returns))
    assert vol.value is None
    assert vol.unavailable_reason == "insufficient_n"
    assert vol.min_n == twr_gates.MIN_N_DISPERSION

    dd = drawdowns(chain)
    assert dd.max_drawdown.value is None
    assert dd.max_drawdown.unavailable_reason == "insufficient_n"


def test_02_growth_then_loss_chains_to_minus_one_percent():
    chain = build_subperiods(observations(fx.growth_then_loss_nav()), {})

    # 110/100 - 1 = +0.10 ; 99/110 - 1 = -0.10
    assert chain.returns == pytest.approx((0.10, -0.10), rel=REL)
    # 1.10 * 0.90 - 1 = 0.99 - 1 = -0.01   (NOT 0.0 — the classic naive answer)
    assert chain.cum_return == pytest.approx(-0.01, rel=REL)
    assert chain.cum_return != pytest.approx(0.0, abs=1e-6)


def test_03_feb06_deposit_is_not_a_294_percent_return():
    """The headline defect. B=246713.50 E=972215.53 C=+725000."""
    r = subperiod_return(fx.FEB06_BEGIN_NAV, fx.FEB06_END_NAV, fx.FEB06_FLOW)

    # (972215.53 - 725000.00 - 246713.50) / 246713.50
    #   = 502.03 / 246713.50
    #   = 0.0020348704063621486
    assert r == pytest.approx(0.0020348704063621486, rel=REL)
    assert r < 0.01
    # The live payload published +2.9407 for this session by forcing C to 0:
    #   (972215.53 - 0 - 246713.50) / 246713.50 = 725502.03 / 246713.50 = 2.940666
    assert r != pytest.approx(2.940666, rel=1e-3)


def test_04_withdrawal_is_removed_from_the_return():
    r = subperiod_return(100_000.0, 90_000.0, -15_000.0)
    # (90000 - (-15000) - 100000) / 100000 = 5000 / 100000 = 0.05
    assert r == pytest.approx(0.05, rel=REL)


def test_05_same_day_deposit_and_withdrawal_net_before_the_return():
    nav = fx.same_day_both_ways_nav()
    flow_date = sorted(nav)[1]
    # +50000 deposit and -20000 withdrawal on the same date net to +30000
    chain = build_subperiods(observations(nav), {flow_date: 50_000.0 - 20_000.0})
    sp = by_date(chain)[flow_date]

    assert sp.flow == pytest.approx(30_000.0, rel=REL)
    # (131000 - 30000 - 100000) / 100000 = 1000 / 100000 = 0.01
    assert sp.ret == pytest.approx(0.01, rel=REL)


def test_09_flow_on_the_first_day_is_already_inside_n0():
    nav = fx.first_day_flow_nav()
    d0, d1 = sorted(nav)
    chain = build_subperiods(observations(nav), {d0: 100_000.0})

    assert chain.n_subperiods == 1
    # (101000 - 0 - 100000) / 100000 = 0.01 — the d0 flow never enters a subperiod
    assert chain.returns == pytest.approx((0.01,), rel=REL)

    vector = build_cashflow_vector(observations(nav), {d0: 100_000.0})
    # Investor sign: CF_0 = -N_0 = -100000 ; CF_M = +N_M = +101000.
    # The +100000 must NOT appear as a third cashflow (classic XIRR double count).
    assert len(vector) == 2
    assert vector[0] == DatedCashflow(date=d0, amount=-100_000.0)
    assert vector[1] == DatedCashflow(date=d1, amount=101_000.0)


def test_10_flow_on_the_last_day_is_netted_out_of_the_terminal_cashflow():
    nav = fx.last_day_flow_nav()
    d0, d1 = sorted(nav)
    flows = {d1: 50_000.0}
    chain = build_subperiods(observations(nav), flows)

    # (151000 - 50000 - 100000) / 100000 = 1000 / 100000 = 0.01
    assert chain.returns == pytest.approx((0.01,), rel=REL)

    vector = build_cashflow_vector(observations(nav), flows)
    assert vector[0] == DatedCashflow(date=d0, amount=-100_000.0)
    # CF_M = +N_M - C_M = 151000 - 50000 = +101000
    assert vector[1] == DatedCashflow(date=d1, amount=101_000.0)


def test_11_nav_to_zero_with_nothing_on_file_is_quarantined_not_chained():
    """E.1 #11, revised by DECISION 4 (audit round 3).

    Round 1 exempted `r == -1.0` outright so this fixture could chain to -100%.
    That exemption was unconditional, and a blank `total=""` attribute parsing
    to 0.0 published -100% as `ok`. A NAV that goes to zero with no recorded
    flow behind it is the most suspect thing the series can contain, so the
    residual gate treats it like any other unexplained move.
    """
    nav = fx.nav_to_zero()
    d0, d1, d2 = sorted(nav)
    chain = build_subperiods(observations(nav), {})

    # (0 - 0 - 100000) / 100000 = -1.0, unexplained: |−1.0| > 0.50.
    assert by_date(chain)[d1].ret is None
    assert by_date(chain)[d1].skip_reason == "suspect_no_flow"
    assert "suspect" in by_date(chain)[d1].flags
    assert by_date(chain)[d2].ret is None
    assert by_date(chain)[d2].skip_reason == "zero_base"
    assert "zero_base" in by_date(chain)[d2].flags
    assert chain.n_subperiods == 2
    assert chain.n_returns == 0
    assert chain.cum_return != pytest.approx(-1.0, rel=1e-6)

    # Annualizing a total loss must not raise and must not go complex.
    ann = annualize_return(-1.0, 730)
    assert ann.value == pytest.approx(-1.0, rel=REL)
    assert ann.unavailable_reason == "total_loss"


def test_11b_nav_to_zero_explained_by_a_recorded_withdrawal_chains_at_zero():
    """The other half of DECISION 4: evidence, not position in the series.

    A full withdrawal on the same date leaves no residual at all -
    r = (0 - (-100000) - 100000) / 100000 = 0.0 - so the very same NAV pair
    chains cleanly once the flow that explains it is on file.
    """
    nav = fx.nav_to_zero()
    d0, d1, d2 = sorted(nav)
    chain = build_subperiods(observations(nav), {d1: -100_000.0})

    assert by_date(chain)[d1].ret == pytest.approx(0.0, abs=1e-12)
    assert by_date(chain)[d1].skip_reason is None
    assert "suspect" not in by_date(chain)[d1].flags
    assert chain.n_returns == 1
    assert chain.cum_return == pytest.approx(0.0, abs=1e-12)


def test_12_negative_begin_nav_is_skipped_not_sign_inverted():
    nav = fx.negative_nav()
    d0, d1 = sorted(nav)
    chain = build_subperiods(observations(nav), {})

    sp = by_date(chain)[d1]
    assert sp.ret is None
    assert sp.skip_reason == "negative_base"
    assert "negative_base" in sp.flags
    assert chain.n_returns == 0
    # Naive arithmetic would have produced (1000 - (-5000)) / -5000 = -1.2
    assert chain.cum_return == 0.0


def test_13_single_observation_produces_zero_subperiods():
    chain = build_subperiods(observations(fx.single_observation()), {})
    assert chain.n_nav_observations == 1
    assert chain.n_subperiods == 0
    assert chain.n_returns == 0
    assert chain.subperiods == ()
    assert chain.cum_return == 0.0


def test_15_weekend_gap_is_three_days_and_not_flagged():
    chain = build_subperiods(observations(fx.WEEKEND_GAP_NAV), {})
    sp = by_date(chain)["2026-01-12"]
    # Fri 2026-01-09 -> Mon 2026-01-12 is 3 calendar days; MAX_SUBPERIOD_GAP_DAYS is 4
    assert sp.gap_days == 3
    assert "nav_gap" not in sp.flags


def test_16_holiday_gap_is_flagged_but_still_chained():
    chain = build_subperiods(observations(fx.HOLIDAY_GAP_NAV), {})
    sp = by_date(chain)["2026-01-14"]
    # Thu 2026-01-08 -> Wed 2026-01-14 is 6 calendar days, > 4
    assert sp.gap_days == 6
    assert "nav_gap" in sp.flags
    # 101000/100000 - 1 = 0.01 — still chained
    assert sp.ret == pytest.approx(0.01, rel=REL)
    assert sp.skip_reason is None


def test_17_duplicate_nav_dates_raise_rather_than_last_write_wins():
    dupes = (
        NavObservation(date="2026-01-13", nav=185_755.43),
        NavObservation(date="2026-01-13", nav=106_680.59),
        NavObservation(date="2026-01-14", nav=186_000.00),
    )
    with pytest.raises(DuplicateNavDate):
        build_subperiods(dupes, {})


def test_18_unsorted_input_sorts_internally_to_the_same_chain():
    nav = fx.growth_then_loss_nav()
    d0, d1, d2 = sorted(nav)
    scrambled = (
        NavObservation(date=d2, nav=nav[d2]),
        NavObservation(date=d0, nav=nav[d0]),
        NavObservation(date=d1, nav=nav[d1]),
    )
    chain = build_subperiods(scrambled, {})

    assert [sp.date for sp in chain.subperiods] == [d1, d2]
    assert chain.period_start == d0
    assert chain.period_end == d2
    # Same arithmetic as test_02: 1.10 * 0.90 - 1 = -0.01
    assert chain.cum_return == pytest.approx(-0.01, rel=REL)


def test_19_suspect_subperiod_with_no_flow_is_quarantined():
    chain = build_subperiods(observations(fx.feb06_nav()), {})
    sp = by_date(chain)[fx.FEB06_DATE]

    # (972215.53 - 0 - 246713.50) / 246713.50 = 2.940666 > SUSPECT_RETURN_THRESHOLD
    assert sp.skip_reason == "suspect_no_flow"
    assert "suspect" in sp.flags
    assert sp.ret is None
    assert sp.cum_ret is None
    assert chain.excluded_suspect is True
    assert chain.n_subperiods == 1
    assert chain.n_returns == 0
    # 972215.53 - 246713.50 = 725502.03  (the INFERRED_FLOW_CANDIDATE amount)
    assert sp.end_nav - sp.begin_nav == pytest.approx(725_502.03, rel=REL)


def test_20_flow_dominant_but_explained_is_chained_and_only_flagged():
    chain = build_subperiods(observations(fx.feb06_nav()), {fx.FEB06_DATE: fx.FEB06_FLOW})
    sp = by_date(chain)[fx.FEB06_DATE]

    # |725000| / 246713.50 = 2.9386 > FLOW_DOMINANT_RATIO (0.25)
    assert "flow_dominant" in sp.flags
    assert "suspect" not in sp.flags
    assert sp.skip_reason is None
    assert sp.ret == pytest.approx(0.0020348704063621486, rel=REL)
    assert chain.excluded_suspect is False
    assert chain.n_returns == 1


def test_21_flow_without_a_nav_row_carries_to_the_next_observation():
    chain = build_subperiods(observations(fx.SATURDAY_FLOW_NAV), fx.SATURDAY_FLOW)
    sp = by_date(chain)["2026-01-12"]

    # Saturday 2026-01-10 has no NAV row; the money is first visible on Mon 01-12
    assert sp.flow == pytest.approx(50_000.0, rel=REL)
    # (151000 - 50000 - 100000) / 100000 = 0.01
    assert sp.ret == pytest.approx(0.01, rel=REL)


def test_22_consolidation_drops_a_date_an_account_is_missing():
    nav, flows, gap_dates = consolidate_accounts(fx.CONSOLIDATED_NAV, fx.CONSOLIDATED_FLOWS)
    totals = {o.date: o.nav for o in nav}

    assert gap_dates == ["2026-01-13"]
    assert "2026-01-13" not in totals  # dropped, never summed short at 125000
    # 120000 + 80000 = 200000 ; 126000 + 84000 = 210000
    assert totals["2026-01-12"] == pytest.approx(200_000.0, rel=REL)
    assert totals["2026-01-14"] == pytest.approx(210_000.0, rel=REL)
    # -10000 + 10000 = 0 — an internal transfer nets out before the chain
    assert flows.get("2026-01-14", 0.0) == pytest.approx(0.0, abs=1e-9)


def test_non_finite_input_raises():
    with pytest.raises(NonFiniteInput):
        build_subperiods(
            (
                NavObservation(date="2026-01-12", nav=100_000.0),
                NavObservation(date="2026-01-13", nav=float("nan")),
            ),
            {},
        )


def test_subperiod_return_guards_return_none_not_zero():
    assert subperiod_return(0.0, 100_000.0, 0.0) is None
    assert subperiod_return(-5_000.0, 1_000.0, 0.0) is None
    assert subperiod_return(100_000.0, float("inf"), 0.0) is None


def test_chain_returns_of_empty_sequence_is_zero():
    assert chain_returns([]) == 0.0
    # 1.10 * 0.90 - 1 = -0.01
    assert chain_returns([0.10, -0.10]) == pytest.approx(-0.01, rel=REL)


# ===========================================================================
# E.3 — statistics, pinned on the five-observation series
# ===========================================================================
# r = [0.01, -0.02, 0.03, 0.00, -0.01], N = 5, rf_daily = 0
#   sum      = 0.01 - 0.02 + 0.03 + 0.00 - 0.01 = 0.01
#   mean     = 0.01 / 5 = 0.002
#   devs     = [0.008, -0.022, 0.028, -0.002, -0.012]
#   sq devs  = [6.4e-05, 4.84e-04, 7.84e-04, 4.0e-06, 1.44e-04]  sum = 1.48e-03
#   var(ddof=1) = 1.48e-03 / 4 = 3.7e-04
#   sqrt(252) = 15.874507866387544


def test_32_to_35_mean_stdev_volatility_sharpe():
    vol = volatility(fx.STATS_RETURNS, min_n=2)
    # sqrt(3.7e-04) = 0.019235384061671343 ; * 15.874507866387544
    assert vol.value == pytest.approx(0.3053522555999873, rel=REL)
    assert vol.unavailable_reason is None
    assert vol.value >= 0

    sharpe = sharpe_ratio(fx.STATS_RETURNS, 0.0, min_n=2)
    # (0.002 - 0) / 0.019235384061671343 * 15.874507866387544
    assert sharpe.value == pytest.approx(1.6505527329729042, rel=REL)


def test_36_downside_deviation_divides_by_n_not_by_the_downside_count():
    dd = downside_deviation(fx.STATS_RETURNS, target=0.0, min_n=2)
    # negatives: -0.02, -0.01 -> 4.0e-04 + 1.0e-04 = 5.0e-04
    # divisor is N = 5 (not 2): 5.0e-04 / 5 = 1.0e-04 ; sqrt = 0.01 daily
    # annualized: 0.01 * 15.874507866387544
    assert dd.value == pytest.approx(0.15874507866387544, rel=REL)


def test_37_sortino_uses_the_full_n_divisor():
    s = sortino_ratio(fx.STATS_RETURNS, 0.0, min_n=2, min_downside=1)
    # 0.002 / 0.01 * 15.874507866387544 = 0.2 * 15.874507866387544
    assert s.value == pytest.approx(3.1749015732775074, rel=REL)
    # Anti-pin: dividing by the 2 negative observations gives
    #   sqrt(5.0e-04 / 2) = 0.015811388300841896
    #   0.002 / 0.015811388300841896 * 15.874507866387544 = 2.0079840636817807
    assert s.value != pytest.approx(2.0079840636817807, rel=1e-6)


def test_38_daily_risk_free_is_geometric_not_arithmetic():
    rf = daily_risk_free(0.0525)
    # 1.0525 ** (1/252) - 1
    assert rf == pytest.approx(0.0002030693720416199, rel=REL)
    # Anti-pin: 0.0525 / 252 = 0.00020833333333333332
    assert rf != pytest.approx(0.00020833333333333332, rel=1e-6)


def test_39_max_drawdown_from_the_return_index():
    chain = build_subperiods(observations(fx.drawdown_nav()), {})
    dd = drawdowns(chain, min_n=1)

    # index: 1.00 -> 1.10 -> 0.88 -> 0.924 ; peak 1.10, trough 0.88
    #   0.88 / 1.10 - 1 = -0.20
    assert dd.max_drawdown.value == pytest.approx(-0.20, rel=REL)
    assert dd.max_drawdown.value <= 0
    assert dd.trough_days == 1
    assert dd.recovery_days == 2
    assert dd.ongoing is True
    # 0.924 / 1.10 - 1 = -0.16
    assert dd.current_drawdown.value == pytest.approx(-0.16, rel=REL)
    assert dd.current_drawdown.value <= 0


def test_40_annualize_two_year_ten_percent():
    ann = annualize_return(0.10, 730)
    # 1.10 ** (365/730) - 1 = 1.10 ** 0.5 - 1 = 1.0488088481701516 - 1
    assert ann.value == pytest.approx(0.04880884817015163, rel=REL)
    assert ann.unavailable_reason is None


def test_41_annualize_refuses_a_364_day_window():
    ann = annualize_return(0.10, 364)
    assert ann.value is None
    assert ann.unavailable_reason == "period_lt_1y"
    assert ann.min_n == twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED


def test_42_the_live_contaminated_annualization_is_unreachable():
    ann = annualize_return(9.5128, 79)
    assert ann.value is None
    assert ann.unavailable_reason == "period_lt_1y"
    # Anti-pin: unguarded, 10.5128 ** (365/79) - 1 is ~5.2e04 (+5,200,000%/yr),
    # the same class of number the live page rendered as +3,288,954.62%.
    assert ann.value != pytest.approx(52_342.0, rel=0.5)


def test_43_annualize_of_a_total_return_below_minus_one_does_not_go_complex():
    ann = annualize_return(-1.5, 400)
    # (1 + (-1.5)) ** fractional is a complex number in Python; today's code
    # raises TypeError comparing it. This must be a clean gated None.
    assert ann.value is None
    assert ann.unavailable_reason == "invalid_total_return"


def test_44_var_and_cvar_on_the_twenty_point_series():
    var = historical_var(fx.VAR_RETURNS_SORTED, level=0.05, min_n=2)
    cvar = conditional_var(fx.VAR_RETURNS_SORTED, level=0.05, min_n=2)

    # N = 20 ; p = (20-1) * 0.05 = 0.95 -> interpolate sorted[0] .. sorted[1]
    #   -0.05 + 0.95 * (-0.04 - (-0.05)) = -0.05 + 0.95 * 0.01 = -0.0405
    assert var.value == pytest.approx(-0.0405, rel=REL)
    # k = floor(0.05 * 20) = 1 -> mean of the single worst observation
    assert cvar.value == pytest.approx(-0.05, rel=REL)


def test_45_var_sign_and_ordering_invariants():
    var = historical_var(fx.VAR_RETURNS_SORTED, level=0.05, min_n=2)
    cvar = conditional_var(fx.VAR_RETURNS_SORTED, level=0.05, min_n=2)

    assert cvar.value <= var.value
    # 5 of 20 observations are negative, well above ceil(0.05 * 20) = 1
    assert var.value <= 0


def test_return_distribution_counts_and_extremes():
    dist = return_distribution(fx.STATS_RETURNS, min_n=2)

    # positives: 0.01, 0.03 -> 2 ; negatives: -0.02, -0.01 -> 2 ; flat: 0.00 -> 1
    assert dist["positive_days"].value == 2
    assert dist["negative_days"].value == 2
    assert dist["flat_days"].value == 1
    assert dist["hit_rate"].value == pytest.approx(2 / 5, rel=REL)  # 0.4
    assert dist["best_day"].value == pytest.approx(0.03, rel=REL)
    # min of [0.01, -0.02, 0.03, 0.00, -0.01]
    assert dist["worst_day"].value == pytest.approx(-0.02, rel=REL)
    # up mean: (0.01 + 0.03) / 2 = 0.02 ; down mean: (-0.02 + -0.01) / 2 = -0.015
    assert dist["average_up_day"].value == pytest.approx(0.02, rel=REL)
    assert dist["average_down_day"].value == pytest.approx(-0.015, rel=REL)
    # 0.02 / 0.015 = 1.3333333333333333
    assert dist["win_loss_ratio"].value == pytest.approx(1.3333333333333333, rel=REL)


# ===========================================================================
# E.4 — benchmark
# ===========================================================================
# r = [0.01, -0.02, 0.03, 0.00, -0.01]   mean_r = 0.002
# b = [0.008, -0.015, 0.02, 0.002, -0.005]
#   sum_b = 0.008 - 0.015 + 0.02 + 0.002 - 0.005 = 0.01 ; mean_b = 0.002
#   dev_b = [0.006, -0.017, 0.018, 0.000, -0.007]
#   cov(ddof=1) = (0.008*0.006 + (-0.022)(-0.017) + 0.028*0.018
#                  + (-0.002)(0.0) + (-0.012)(-0.007)) / 4
#               = (4.8e-05 + 3.74e-04 + 5.04e-04 + 0 + 8.4e-05) / 4
#               = 1.01e-03 / 4 = 2.525e-04
#   var_b(ddof=1) = (3.6e-05 + 2.89e-04 + 3.24e-04 + 0 + 4.9e-05) / 4
#                 = 6.98e-04 / 4 = 1.745e-04


def _stats_pairs():
    dates = fx.calendar_run(5)
    port = dict(zip(dates, fx.STATS_RETURNS))
    bench = dict(zip(dates, fx.BENCH_RETURNS))
    return align_series(port, bench)


def test_46_to_52_benchmark_block_statistics():
    pairs = _stats_pairs()
    assert pairs.n_common == 5

    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=5, symbol="SPY", basis="price_return", min_n=2, min_coverage=0.0
    )
    assert reason is None
    assert block is not None

    # beta = 2.525e-04 / 1.745e-04
    assert block.beta == pytest.approx(1.4469914040114613, rel=REL)
    # alpha = (0.002 - beta * 0.002) * 252
    #       = 0.002 * (1 - 1.4469914040114613) * 252
    #       = -0.0008939828080229226 * 252
    assert block.alpha_annualized == pytest.approx(-0.22528366762177685, rel=REL)
    # corr = 2.525e-04 / (0.019235384061671343 * sqrt(1.745e-04))
    assert block.correlation == pytest.approx(0.9937171949545618, rel=REL)
    assert block.r_squared == pytest.approx(0.9874738635483625, rel=REL)
    # active = r - b = [0.002, -0.005, 0.010, -0.002, -0.005] ; mean = 0.0
    #   sq devs = [4e-06, 2.5e-05, 1.0e-04, 4e-06, 2.5e-05] sum = 1.58e-04
    #   var(ddof=1) = 3.95e-05 ; sd = 0.006284902544988268
    #   TE = 0.006284902544988268 * 15.874507866387544
    assert block.tracking_error == pytest.approx(0.09976973488989535, rel=REL)
    # mean active is exactly zero
    assert block.information_ratio == pytest.approx(0.0, abs=1e-12)
    # 1.008 * 0.985 * 1.02 * 1.002 * 0.995 - 1
    #   1.008 * 0.985      = 0.99288
    #   * 1.02             = 1.0127376
    #   * 1.002            = 1.0147630752
    #   * 0.995            = 1.0096892598239999
    assert block.benchmark_return == pytest.approx(0.00968925982399993, rel=REL)
    assert block.symbol == "SPY"
    assert block.basis == "price_return"
    assert block.n_common == 5


def test_53_missing_benchmark_returns_none_and_a_reason():
    pairs = align_series(dict(zip(fx.calendar_run(5), fx.STATS_RETURNS)), {})
    assert pairs.n_common == 0

    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=5, symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "benchmark_unavailable"


def test_54_benchmark_coverage_below_ninety_percent_is_refused():
    dates = fx.calendar_run(60)
    port = dict(zip(dates, fx.boundary_returns(60)))
    # 50 of 60 aligned -> coverage 50/60 = 0.8333 < BENCHMARK_MIN_COVERAGE (0.90)
    bench = dict(zip(dates[:50], fx.boundary_returns(50)))
    pairs = align_series(port, bench)
    assert pairs.n_common == 50

    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=60, symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "benchmark_coverage"


def test_55_zero_variance_benchmark_is_refused():
    """Direct pin for the live beta = 23.93 alongside BENCHMARK RETURN '--'."""
    dates = fx.calendar_run(60)
    port = dict(zip(dates, fx.boundary_returns(60)))
    bench = dict.fromkeys(dates, 0.0)
    pairs = align_series(port, bench)
    assert pairs.n_common == 60

    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=60, symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "benchmark_degenerate"


def test_56_alignment_is_an_inner_join_and_never_zero_fills():
    port = {
        "2026-01-05": 0.011,
        "2026-01-06": 0.012,
        "2026-01-07": 0.013,
        "2026-01-08": 0.014,
        "2026-01-09": 0.015,
    }
    bench = {
        "2026-01-05": 0.001,
        "2026-01-06": 0.002,
        # 2026-01-07 missing
        "2026-01-08": 0.004,
        "2026-01-09": 0.005,
    }
    pairs = align_series(port, bench)

    assert isinstance(pairs, AlignedPairs)
    assert pairs.n_common == 4
    assert pairs.dates == ("2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09")
    assert pairs.benchmark == pytest.approx((0.001, 0.002, 0.004, 0.005), rel=REL)
    assert pairs.portfolio == pytest.approx((0.011, 0.012, 0.014, 0.015), rel=REL)
    # Anti-pin: today's zero-fill + `prev = None` poisons 01-08 to 0.0
    assert pairs.benchmark[2] != pytest.approx(0.0, abs=1e-12)


# ===========================================================================
# E.5 — MWR
# ===========================================================================


def test_59_textbook_xirr_is_ten_percent():
    cfs = [
        DatedCashflow(date="2026-01-01", amount=-1000.0),
        DatedCashflow(date="2027-01-01", amount=-1000.0),
        DatedCashflow(date="2028-01-01", amount=2310.0),
    ]
    # Act/365: 2026 and 2027 are both 365 days, so tau = 0, 1, 2 exactly.
    # NPV(0.10) = -1000 - 1000/1.1 + 2310/1.21
    #           = -1000 - 909.0909090909091 + 1909.090909090909 = 0
    result = xirr(cfs, calendar_days=730, n_returns=500)
    assert result.value == pytest.approx(0.10, rel=REL)
    assert result.unavailable_reason is None
    assert result.multiple_sign_changes is False


def test_60_no_sign_change():
    cfs = [
        DatedCashflow(date="2026-01-01", amount=100.0),
        DatedCashflow(date="2027-06-01", amount=50.0),
    ]
    result = xirr(cfs, calendar_days=516, n_returns=400)
    assert result.value is None
    assert result.unavailable_reason == "no_sign_change"


def test_math_d4_two_real_roots_are_solved_not_reported_as_no_sign_change():
    """audit math-D4. The bracket only widens upward, so a curve with the same
    NPV sign at both ends and two roots between them bails out. §D.3 requires
    solving anyway and taking the root nearest 0, and `no_sign_change` renders
    as "no net capital at risk" for a stream with $1,000 at risk."""
    cfs = [
        DatedCashflow(date="2026-01-01", amount=-1000.0),
        DatedCashflow(date="2027-01-01", amount=2500.0),
        DatedCashflow(date="2028-01-01", amount=-1560.0),
    ]
    # Act/365: both spans are 365 days, so tau = 0, 1, 2 exactly and with
    # x = 1/(1+R) the NPV is  -1000 + 2500x - 1560x**2 = 0
    #   x = (2500 +/- sqrt(2500**2 - 4*1560*1000)) / (2*1560)
    #     = (2500 +/- 100) / 3120  ->  x = 0.8333... or x = 0.7692...
    #   1/x - 1                    ->  R = 0.20      or R = 0.30
    result = xirr(cfs, calendar_days=730, n_returns=60)

    assert result.multiple_sign_changes is True
    assert result.unavailable_reason is None
    assert result.value == pytest.approx(0.20, rel=1e-6)  # the root nearest 0


def test_61_total_loss_is_reported_analytically_not_iterated():
    cfs = [
        DatedCashflow(date="2026-01-01", amount=-1000.0),
        DatedCashflow(date="2026-07-01", amount=-500.0),
        DatedCashflow(date="2027-06-01", amount=0.0),
    ]
    result = xirr(cfs, calendar_days=516, n_returns=400)
    assert result.value == pytest.approx(-1.0, rel=REL)
    assert result.unavailable_reason == "total_loss"
    assert result.iterations == 0


def test_62_sub_year_window_refuses_to_annualize():
    cfs = [
        DatedCashflow(date="2026-01-02", amount=-100_000.0),
        DatedCashflow(date="2026-03-27", amount=182_217.35574011656),
    ]
    result = xirr(cfs, calendar_days=84, n_returns=60)
    assert result.value is None
    assert result.unavailable_reason == "period_lt_1y"


def test_63_below_the_mwr_n_gate():
    cfs = [
        DatedCashflow(date="2026-01-01", amount=-1000.0),
        DatedCashflow(date="2027-06-01", amount=1200.0),
    ]
    result = xirr(cfs, calendar_days=516, n_returns=19)
    assert result.value is None
    assert result.unavailable_reason == "insufficient_n"


def test_65_modified_dietz():
    start = fx.calendar_run(101)[0]  # 100 days before the end
    mid = shift(start, 40)
    end = shift(start, 100)

    result = modified_dietz(100.0, 170.0, [(mid, 50.0)], start, end)
    # D = 100 ; w = (100 - 40) / 100 = 0.60
    # numerator   = 170 - 100 - 50 = 20
    # denominator = 100 + 0.60 * 50 = 130
    # 20 / 130 = 0.15384615384615385
    assert result.value == pytest.approx(0.15384615384615385, rel=REL)
    assert result.unavailable_reason is None


def test_66_solver_converges_on_a_flat_npv_curve():
    """Newton anti-pin: a near-degenerate curve must bisect, not diverge."""
    start = fx.calendar_run(1827)[0]
    end = fx.calendar_run(1)[0]
    cfs = [
        DatedCashflow(date=start, amount=-1_000_000.0),
        DatedCashflow(date=end, amount=1_000_000.01),
    ]
    # (1+R) ** (1826/365) = 1.00000001 -> R is on the order of 2e-09
    result = xirr(cfs, calendar_days=1826, n_returns=1200)
    assert result.unavailable_reason is None
    assert result.value is not None
    assert abs(result.value) < 1e-6


# ===========================================================================
# E.6 — golden path, 60 sessions
# ===========================================================================


def test_golden_fixture_reproduces_the_spec_nav_checkpoints():
    """Validates the INPUT before anything is asserted against it."""
    dates = fx.golden_dates()
    nav = fx.golden_nav()

    assert len(dates) == 61
    assert dates[0] == "2026-01-02"
    assert dates[60] == "2026-03-27"
    assert dates[30] == "2026-02-13"  # the +100000 flow date
    assert dates[45] == "2026-03-06"  # the -25000 flow date
    for idx, expected in fx.GOLDEN_NAV_CHECKPOINTS.items():
        assert nav[dates[idx]] == pytest.approx(expected, rel=TIGHT)


def test_golden_chain_counts_and_cumulative_return():
    chain = build_subperiods(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)

    assert chain.n_nav_observations == 61
    assert chain.n_subperiods == 60
    assert chain.n_returns == 60
    assert chain.excluded_suspect is False
    assert chain.period_start == "2026-01-02"
    assert chain.period_end == "2026-03-27"
    # 2026-01-02 -> 2026-03-27: 29 (rest of Jan) + 28 (Feb) + 27 = 84
    assert chain.calendar_days == 84
    # (1.006 * 0.996 * 1.002 * 0.992 * 1.010 * 0.999) ** 10 - 1
    assert chain.cum_return == pytest.approx(0.050112307160331326, rel=TIGHT)


def test_golden_flow_days_carry_no_phantom_return():
    chain = build_subperiods(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    marks = by_date(chain)

    # index 30 is pattern position 29 % 6 = 5 -> -0.001
    assert marks["2026-02-13"].ret == pytest.approx(-0.001, rel=TIGHT)
    assert marks["2026-02-13"].flow == pytest.approx(100_000.00, rel=TIGHT)
    # |100000| / 102577.56510647341 = 0.9749 > 0.25
    assert "flow_dominant" in marks["2026-02-13"].flags
    # index 45 is pattern position 44 % 6 = 2 -> +0.002
    assert marks["2026-03-06"].ret == pytest.approx(0.002, rel=TIGHT)
    assert marks["2026-03-06"].flow == pytest.approx(-25_000.00, rel=TIGHT)


def test_golden_risk_statistics():
    chain = build_subperiods(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    rets = list(chain.returns)
    # (1 + 0.02) ** (1/252) - 1
    rf_daily = 7.85849419846496e-05

    # mean = (0.006 - 0.004 + 0.002 - 0.008 + 0.010 - 0.001) / 6 = 0.005/6
    #      = 0.0008333333333333334
    # per-cycle sq devs sum = 2.1683333333e-04 ; * 10 cycles = 2.1683333333e-03
    # var(ddof=1) = 2.1683333333e-03 / 59 = 3.6751412429e-05
    # sd = 0.006062294649 ; * 15.874507866387544
    assert volatility(rets).value == pytest.approx(0.09623593888045874, rel=1e-9)
    assert sharpe_ratio(rets, rf_daily).value == pytest.approx(1.9763572406782934, rel=1e-9)
    assert sortino_ratio(rets, rf_daily).value == pytest.approx(3.2608857445808517, rel=1e-9)
    # negatives per cycle: -0.004, -0.008, -0.001 -> 1.6e-05 + 6.4e-05 + 1e-06
    #   = 8.1e-05 ; * 10 = 8.1e-04 ; / 60 = 1.35e-05 ; sqrt = 0.003674234614...
    #   * 15.874507866387544
    assert downside_deviation(rets).value == pytest.approx(0.05832666628567074, rel=1e-9)

    dd = drawdowns(chain)
    # within a cycle: 0.996 * 1.002 * 0.992 = 0.990008064 -> -0.009991936
    assert dd.max_drawdown.value == pytest.approx(-0.009991936000000146, rel=1e-9)

    # sorted 60 returns: ten each of -0.008, -0.004, -0.001, 0.002, 0.006, 0.010
    # p = 59 * 0.05 = 2.95 -> interpolates between two -0.008 values
    assert historical_var(rets).value == pytest.approx(-0.008, rel=1e-9)
    # k = floor(0.05 * 60) = 3 -> mean of three -0.008 values
    assert conditional_var(rets).value == pytest.approx(-0.008, rel=1e-9)


def test_golden_distribution():
    chain = build_subperiods(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    dist = return_distribution(list(chain.returns))
    # 3 of the 6 pattern values are positive -> 30/60
    assert dist["hit_rate"].value == pytest.approx(0.5, rel=TIGHT)
    assert dist["best_day"].value == pytest.approx(0.010, rel=TIGHT)
    assert dist["worst_day"].value == pytest.approx(-0.008, rel=TIGHT)


def test_golden_annualized_is_gated_off_by_the_calendar_year_rule():
    ann = annualize_return(0.050112307160331326, 84)
    assert ann.value is None
    assert ann.unavailable_reason == "period_lt_1y"


def test_golden_low_confidence_flags_are_set():
    chain = build_subperiods(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    rets = list(chain.returns)
    rf_daily = 7.85849419846496e-05

    # n_returns = 60 < RATIO_LOW_CONFIDENCE_N (252) and < VAR_LOW_CONFIDENCE_N (100)
    assert sharpe_ratio(rets, rf_daily).low_confidence is True
    assert sortino_ratio(rets, rf_daily).low_confidence is True
    assert historical_var(rets).low_confidence is True
    assert conditional_var(rets).low_confidence is True


def test_golden_modified_dietz():
    nav = fx.golden_nav()
    result = modified_dietz(
        100_000.00,
        182_217.35574011656,
        [("2026-02-13", 100_000.00), ("2026-03-06", -25_000.00)],
        "2026-01-02",
        "2026-03-27",
    )
    assert nav["2026-03-27"] == pytest.approx(182_217.35574011656, rel=TIGHT)
    # D = 84
    # 2026-02-13 is day 42 -> w = (84 - 42) / 84 = 0.50
    # 2026-03-06 is day 63 -> w = (84 - 63) / 84 = 0.25
    # numerator   = 182217.35574011656 - 100000 - 75000 = 7217.35574011656
    # denominator = 100000 + 0.50 * 100000 + 0.25 * (-25000)
    #             = 100000 + 50000 - 6250 = 143750
    # 7217.35574011656 / 143750 = 0.05020769210515868
    assert result.value == pytest.approx(0.05020769210515868, rel=TIGHT)


def test_golden_xirr_reason_is_the_period_gate_not_the_math():
    vector = build_cashflow_vector(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    gated = xirr(vector, calendar_days=84, n_returns=60)
    assert gated.value is None
    assert gated.unavailable_reason == "period_lt_1y"

    # Same vector, gate overridden: the solver itself does converge.
    solved = xirr(vector, calendar_days=420, n_returns=60)
    assert solved.value == pytest.approx(0.23811347727572396, rel=1e-9)


def test_golden_cashflow_vector_shape():
    vector = build_cashflow_vector(observations(fx.golden_nav()), fx.GOLDEN_FLOWS)
    amounts = {cf.date: cf.amount for cf in vector}

    # CF_0 = -N_0 ; interior flows flip sign ; CF_M = +N_M (no flow on 03-27)
    assert amounts["2026-01-02"] == pytest.approx(-100_000.00, rel=TIGHT)
    assert amounts["2026-02-13"] == pytest.approx(-100_000.00, rel=TIGHT)
    assert amounts["2026-03-06"] == pytest.approx(25_000.00, rel=TIGHT)
    assert amounts["2026-03-27"] == pytest.approx(182_217.35574011656, rel=TIGHT)
    assert len(vector) == 4
    assert [cf.date for cf in vector] == sorted(amounts)


# ===========================================================================
# E.7 — gate boundaries
# ===========================================================================


@pytest.mark.parametrize("fn", [volatility, historical_var, conditional_var])
def test_67_dispersion_gate_19_then_20(fn):
    below = fn(fx.boundary_returns(19))
    assert below.value is None
    assert below.unavailable_reason == "insufficient_n"
    assert below.min_n == 20
    assert below.n == 19

    at = fn(fx.boundary_returns(20))
    assert at.value is not None
    assert at.unavailable_reason is None


def test_67b_max_drawdown_dispersion_gate_19_then_20():
    below = drawdowns(build_subperiods(observations(fx.nav_from_returns(fx.boundary_returns(19))), {}))
    assert below.max_drawdown.value is None
    assert below.max_drawdown.unavailable_reason == "insufficient_n"

    at = drawdowns(build_subperiods(observations(fx.nav_from_returns(fx.boundary_returns(20))), {}))
    assert at.max_drawdown.value is not None


@pytest.mark.parametrize("fn", [sharpe_ratio, sortino_ratio])
def test_70_ratio_gate_59_then_60(fn):
    below = fn(fx.boundary_returns(59), 0.0)
    assert below.value is None
    assert below.unavailable_reason == "insufficient_n"
    assert below.min_n == 60

    at = fn(fx.boundary_returns(60), 0.0)
    assert at.value is not None
    assert at.unavailable_reason is None


def test_71_sortino_needs_five_downside_observations():
    below = sortino_ratio(fx.returns_with_downside_count(60, 4), 0.0)
    assert below.value is None
    assert below.unavailable_reason == "no_downside"

    at = sortino_ratio(fx.returns_with_downside_count(60, 5), 0.0)
    assert at.value is not None
    assert at.unavailable_reason is None


def test_69_benchmark_gate_39_then_40_on_n_common():
    dates = fx.calendar_run(40)
    port = dict(zip(dates, fx.boundary_returns(40)))
    bench = dict(zip(dates, fx.BENCH_RETURNS * 8))

    below = align_series({d: port[d] for d in dates[:39]}, {d: bench[d] for d in dates[:39]})
    assert below.n_common == 39
    block, reason = build_benchmark_block(
        below, 0.0, n_returns=39, symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "insufficient_n"

    at = align_series(port, bench)
    assert at.n_common == 40
    block2, reason2 = build_benchmark_block(
        at, 0.0, n_returns=40, symbol="SPY", basis="price_return"
    )
    assert reason2 is None
    assert block2 is not None
    assert block2.n_common == 40


@pytest.mark.parametrize("fn", [historical_var, conditional_var])
def test_72_var_low_confidence_boundary_99_then_100(fn):
    assert fn(fx.boundary_returns(99)).low_confidence is True
    assert fn(fx.boundary_returns(100)).low_confidence is False


def test_73_annualization_calendar_gate_364_then_365():
    below = annualize_return(0.10, 364)
    assert below.value is None
    assert below.unavailable_reason == "period_lt_1y"

    at = annualize_return(0.10, 365)
    # 1.10 ** (365/365) - 1 = 0.10 exactly
    assert at.value == pytest.approx(0.10, rel=TIGHT)
    assert at.unavailable_reason is None


def test_gated_value_invariant_holds_both_ways():
    ok = GatedValue(value=0.5, n=60, min_n=20, unavailable_reason=None)
    assert ok.value is not None

    gated = GatedValue(value=None, n=5, min_n=20, unavailable_reason="insufficient_n")
    assert gated.value is None

    with pytest.raises((AssertionError, ValueError)):
        GatedValue(value=None, n=5, min_n=20, unavailable_reason=None)
    with pytest.raises((AssertionError, ValueError)):
        GatedValue(value=0.5, n=60, min_n=20, unavailable_reason="insufficient_n")


# ===========================================================================
# F9 — pure-module purity
# ===========================================================================

ALLOWED_PURE_IMPORTS = {
    "math",
    "statistics",
    "datetime",
    "dataclasses",
    "enum",
    "typing",
    "__future__",
    "scripts.lib.twr_gates",
    "scripts.lib",
}

FORBIDDEN_SUBSTRINGS = ("os.environ", "open(", "requests", "libsql", "httpx", "numpy", "pandas")


def _module_path(dotted: str) -> Path:
    root = Path(__file__).resolve().parent.parent
    return root.joinpath(*dotted.split(".")).with_suffix(".py")


@pytest.mark.parametrize("dotted", ["scripts.lib.twr_math", "scripts.lib.twr_gates"])
def test_f9_pure_modules_import_only_stdlib(dotted):
    path = _module_path(dotted)
    assert path.exists(), f"{dotted} must live at {path}"
    source = path.read_text()
    tree = ast.parse(source)

    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            seen.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside scripts.lib
                continue
            seen.add(node.module or "")

    roots = {name.split(".")[0] if not name.startswith("scripts") else name for name in seen}
    assert roots <= ALLOWED_PURE_IMPORTS, f"{dotted} imports outside the pure allowlist: {roots}"

    for bad in FORBIDDEN_SUBSTRINGS:
        assert bad not in source, f"{dotted} contains forbidden I/O token {bad!r}"


def test_f9_twr_math_has_no_module_level_side_effects():
    tree = ast.parse(_module_path("scripts.lib.twr_math").read_text())
    for node in tree.body:
        assert not isinstance(node, ast.Expr) or isinstance(node.value, ast.Constant), (
            "twr_math must not execute expressions at import time"
        )


def test_gate_table_values_are_the_spec_values():
    assert twr_gates.TRADING_DAYS == 252
    assert twr_gates.DAYS_PER_YEAR == 365.0
    assert twr_gates.MIN_N_CHAIN == 1
    assert twr_gates.MIN_N_DISPERSION == 20
    assert twr_gates.MIN_N_BENCHMARK == 40
    assert twr_gates.MIN_N_RATIO == 60
    assert twr_gates.MIN_N_MWR == 20
    assert twr_gates.MIN_DOWNSIDE_OBSERVATIONS == 5
    assert twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED == 365
    assert twr_gates.BENCHMARK_MIN_COVERAGE == 0.90
    assert twr_gates.VAR_LOW_CONFIDENCE_N == 100
    assert twr_gates.RATIO_LOW_CONFIDENCE_N == 252
    assert twr_gates.SUSPECT_RETURN_THRESHOLD == 0.50
    assert twr_gates.FLOW_DOMINANT_RATIO == 0.25
    assert twr_gates.IMPLAUSIBLE_ANNUALIZED == 10.0
    assert twr_gates.IMPLAUSIBLE_ALPHA == 1.0
    assert twr_gates.MAX_SUBPERIOD_GAP_DAYS == 4
    assert twr_gates.NAV_STALENESS_BUDGET_SESSIONS == 2
    assert twr_gates.FLOW_CONVENTION == "eod"
    assert twr_gates.SORTINO_TARGET == 0.0
    # sqrt(252) is the only scaling constant the ratios may use
    assert math.isclose(math.sqrt(twr_gates.TRADING_DAYS), 15.874507866387544, rel_tol=1e-12)

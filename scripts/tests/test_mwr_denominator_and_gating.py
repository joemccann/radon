"""REL-060 / R-147 (P1) — `modified_dietz` accepts a negative denominator.

`subperiod_return`, in the SAME module and for the same `B + C` quantity,
guards `if denominator <= 0` with the docstring "a period cannot start with
no capital". `modified_dietz` guards only `== 0`. A weighted outflow larger
than `begin_nav` — plausible when a Flex NAV document double-counts a
transfer across its three sections, the hazard CLAUDE.md flags for
`IB_FLEX_NAV_QUERY_ID` — yields a NEGATIVE denominator and a sign-inverted
return, which the MWR/IRR card then renders beside a TWR that was correctly
suppressed (`twr.cum_return` and `twr.annualized` are plausibility-gated;
`mwr.period_return` and `mwr.annualized` pass through `gatedValueFrom` with
no bar at all).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from lib import twr_math


class TestModifiedDietzDenominator:
    def test_a_negative_denominator_is_degenerate_not_a_return(self):
        """begin_nav 100k, a 150k outflow on day 1 of a 30-day period:
        B + C is negative, and the old guard let it through sign-inverted."""
        result = twr_math.modified_dietz(
            begin_nav=100_000.0,
            end_nav=10_000.0,
            dated_flows=[("2026-08-02", -150_000.0)],
            period_start="2026-08-01",
            period_end="2026-08-31",
            n_returns=60,
        )
        assert result.value is None, (
            f"a negative B + C produced a return of {result.value}"
        )
        assert result.unavailable_reason == "degenerate"

    def test_zero_stays_degenerate(self):
        result = twr_math.modified_dietz(
            begin_nav=0.0,
            end_nav=0.0,
            dated_flows=[],
            period_start="2026-08-01",
            period_end="2026-08-31",
            n_returns=60,
        )
        assert result.value is None
        assert result.unavailable_reason == "degenerate"

    def test_an_ordinary_period_still_computes(self):
        result = twr_math.modified_dietz(
            begin_nav=100_000.0,
            end_nav=112_000.0,
            dated_flows=[("2026-08-16", 10_000.0)],
            period_start="2026-08-01",
            period_end="2026-08-31",
            n_returns=60,
        )
        assert result.value is not None
        assert 0 < result.value < 0.1

    def test_the_two_guards_now_agree(self):
        """Both functions bound the same B + C quantity, so a denominator
        that is degenerate for one cannot be valid for the other."""
        import inspect

        dietz = inspect.getsource(twr_math.modified_dietz)
        subperiod = inspect.getsource(twr_math.subperiod_return)
        assert "denominator <= 0" in dietz
        assert "denominator <= 0" in subperiod

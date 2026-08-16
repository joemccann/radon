"""Audit round 3 — the nine residual performance defects, Python half.

RED SUITE. Every test in this module fails against the tree as it stands and
each one names the defect it reproduces (R1..R8 of the round-3 register).

Design decisions this suite encodes, so an implementer never has to guess:

* **DECISION 1** — a subperiod is UNEXPLAINED purely on its own residual.
  ``r_t = (E - B - C)/B`` has already netted the recorded flow out, so the flow
  is not part of the test a second time. ``|r| > SUSPECT_RETURN_THRESHOLD`` or
  ``|r| > outlier_bar`` and it quarantines, whatever `C` happens to be.
* **DECISION 2** — the dispersion bar never disables itself. A young account, a
  dormant account and a flat series all still have a bar; the absolute floor is
  0.10.
* **DECISION 3** — ``SUBPERIOD_EXTREME`` is severity ``error``.
* **DECISION 4** — a NAV of zero is exempt from quarantine only when a recorded
  external flow of matching magnitude explains it.
* **DECISION 6** — ``sessions_behind`` counts COMPLETED sessions only, strictly
  after ``nav_as_of`` and up to and including the last completed session, and
  Python and TypeScript agree case for case.

No network, no Turso, no Flex: every input is a literal or a fixture.

Expected values are hand-computed at the assertion site. Nothing here derives an
expectation by calling the code under test.
"""

from __future__ import annotations

import ast
import json
import re
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scripts.perf_twr_builder as builder
from scripts.lib import twr_gates
from scripts.lib.twr_math import (
    FlowSet,
    FlowsStatus,
    NavObservation,
    Subperiod,
)
from scripts.perf_twr_builder import build_payload, sessions_behind

from fixtures import twr_scenarios as fx  # noqa: E402  (tests/ dir, inserted above)

REL = 1e-9
REPO_ROOT = Path(__file__).resolve().parent.parent
PARITY_TABLE = Path(__file__).resolve().parent / "fixtures" / "perf" / "sessions_behind_parity.json"
ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def observations(nav_map):
    return tuple(NavObservation(date=d, nav=float(v)) for d, v in sorted(nav_map.items()))


def flows_on(mapping):
    return FlowSet(
        status=FlowsStatus.OK,
        by_date=dict(mapping),
        source="flex_cash_transactions+transfers",
    )


def codes(payload):
    return [w["code"] for w in payload["warnings"]]


def severities_of(payload, code):
    return [w["severity"] for w in payload["warnings"] if w["code"] == code]


def subperiod_on(payload, iso_date):
    for sp in payload["subperiods"]:
        if sp["date"] == iso_date:
            return sp
    raise AssertionError(f"no subperiod for {iso_date}: {[s['date'] for s in payload['subperiods']]}")


def magnitudes_of(nav_map):
    """|r| for every consecutive pair of a flow-free NAV map. Pure arithmetic."""
    dates = sorted(nav_map)
    return [
        abs(nav_map[end] / nav_map[start] - 1.0)
        for start, end in zip(dates, dates[1:])
    ]


# ===========================================================================
# R1 — the "flow explains 25% of the move" cliff
# ===========================================================================
#
# 263 weekday NAVs. The final subperiod runs B = 259,879.31 -> E = 985,485.30,
# a move of 725,605.99. `_is_unexplained` today asks whether the recorded flow
# covers FLOW_DOMINANT_RATIO (0.25) of that move, i.e. 181,401.4975:
#
#   C = 181,000 (24.94% of the move) -> unexplained -> quarantined
#   C = 182,000 (25.08% of the move) -> "explained"  -> chained at r = +209.18%
#
# 1,000 dollars of recorded cash decide whether a 725k discontinuity is
# published. That is the cliff.


def test_r1_a_flow_covering_a_quarter_of_the_move_does_not_explain_the_residual():
    payload = build_payload(
        observations(fx.cliff_nav()),
        flows_on({fx.cliff_dates()[-1]: fx.CLIFF_ESCAPING_FLOW}),
    )
    jump = subperiod_on(payload, fx.cliff_dates()[-1])

    # The residual the flow leaves behind:
    #   (985,485.30 - 182,000.00 - 259,879.31) / 259,879.31
    #     = 543,605.99 / 259,879.31 = 2.091763249640766
    # A +209.18% session is not a market outcome at any flow size, and
    # SUSPECT_RETURN_THRESHOLD is 0.50.
    assert abs(2.091763249640766) > twr_gates.SUSPECT_RETURN_THRESHOLD
    assert jump["skip_reason"] == "suspect_no_flow"
    assert jump["r"] is None
    assert "suspect" in jump["flags"]
    assert payload["counts"]["n_suspect"] == 1

    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    assert severities_of(payload, "SUBPERIOD_SUSPECT") == ["error"]

    # What is left is the pre-jump chain alone. 262 observations alternate
    # 259,879.31 * 1.01 / 259,879.31, starting high and ending low, so
    # 130 factors of 1.01 and 131 factors of 1/1.01 multiply to exactly 1/1.01:
    #   1/1.01 - 1 = -0.009900990099009901
    assert payload["twr"]["cum_return"] == pytest.approx(-0.009900990099009901, rel=REL)
    assert payload["twr"]["excludes_suspect"] is True

    # Anti-pin: the number this test exists to make unpublishable.
    #   (1/1.01) * (1 + 2.091763249640766) - 1 = 2.0611517323175903
    assert payload["twr"]["cum_return"] != pytest.approx(2.0611517323175903, rel=1e-6)
    assert abs(payload["twr"]["cum_return"]) < 0.5


def test_r1_annualized_is_not_published_off_a_quarantined_discontinuity():
    payload = build_payload(
        observations(fx.cliff_nav()),
        flows_on({fx.cliff_dates()[-1]: fx.CLIFF_ESCAPING_FLOW}),
    )
    # 263 weekdays from a Monday is 52 whole weeks plus two days = 366 calendar
    # days, so the sub-year annualization gate does NOT protect this payload.
    assert payload["calendar_days"] == 366

    annualized = payload["twr"]["annualized"]["value"]
    # Anti-pin: (1 + 2.0611517323175903) ** (365/366) - 1 = 2.0518086706344043
    assert annualized != pytest.approx(2.0518086706344043, rel=1e-6)
    assert annualized is None or abs(annualized) < 0.5


def test_r1_control_a_smaller_flow_on_the_same_series_also_quarantines():
    """The fix must not merely move the cliff: both sides quarantine."""
    payload = build_payload(
        observations(fx.cliff_nav()),
        flows_on({fx.cliff_dates()[-1]: fx.CLIFF_QUARANTINING_FLOW}),
    )
    assert subperiod_on(payload, fx.cliff_dates()[-1])["skip_reason"] == "suspect_no_flow"
    assert payload["status"] == "degraded"


# ===========================================================================
# R2 / DECISION 4 — the unconditional total-loss exemption
# ===========================================================================
#
# `_is_terminal_total_loss` reads `all(o.nav <= 0 for o in observations[i+2:])`.
# On the FINAL subperiod that slice is empty and `all([])` is True, so the
# exemption is unconditional: a NAV that lands on exactly 0.0 is chained as a
# genuine -100% session and the page renders a confident hero of -100.00%.


def test_r2_terminal_nav_of_zero_with_no_recorded_flow_is_quarantined():
    payload = build_payload(observations(fx.terminal_zero_nav()), FlowSet.empty_verified())
    last = fx.terminal_zero_nav()
    end_date = sorted(last)[-1]
    zero_day = subperiod_on(payload, end_date)

    # (0.0 - 0.0 - 100,000.00) / 100,000.00 = -1.0 with nothing recorded to
    # explain it. With no matching withdrawal on file, NAV going to zero is the
    # single most suspect thing the series can contain.
    assert zero_day["skip_reason"] == "suspect_no_flow"
    assert zero_day["r"] is None
    assert payload["counts"]["n_suspect"] == 1
    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)

    published = None if payload["twr"] is None else payload["twr"]["cum_return"]
    assert published != -1.0  # the hero must not read -100.00%


def test_r2_control_a_recorded_full_withdrawal_explains_the_zero():
    """DECISION 4's exemption is evidence-based: the withdrawal is on file."""
    nav = fx.terminal_zero_nav()
    end_date = sorted(nav)[-1]
    payload = build_payload(observations(nav), flows_on({end_date: -100_000.0}))

    # (0.0 - (-100,000.00) - 100,000.00) / 100,000.00 = 0.0 exactly.
    assert subperiod_on(payload, end_date)["r"] == pytest.approx(0.0, abs=1e-12)
    assert subperiod_on(payload, end_date)["skip_reason"] is None
    assert payload["counts"]["n_suspect"] == 0
    assert "SUBPERIOD_SUSPECT" not in codes(payload)


def test_r2_nav_to_zero_then_zero_is_quarantined_too():
    """The same `all([])` hole one index earlier: [100000, 0, 0].

    Supersedes spec E.1 #11's `cum_return = -1.0` pin, which was written before
    DECISION 4. A wipeout with no withdrawal on file is not a return.
    """
    payload = build_payload(observations(fx.nav_to_zero()), FlowSet.empty_verified())
    dates = sorted(fx.nav_to_zero())

    assert subperiod_on(payload, dates[1])["skip_reason"] == "suspect_no_flow"
    published = None if payload["twr"] is None else payload["twr"]["cum_return"]
    assert published != -1.0


# ===========================================================================
# DECISION 3 + the self-contradictory SUBPERIOD_EXTREME message
# ===========================================================================


def _chained_extreme(ret: float, flow: float) -> Subperiod:
    return Subperiod(
        date="2026-02-06",
        begin_nav=100_000.0,
        end_nav=100_000.0 * (1.0 + ret) + flow,
        flow=flow,
        denominator=100_000.0,
        ret=ret,
        cum_ret=ret,
        gap_days=1,
        flags=("extreme",),
        skip_reason=None,
    )


def _chain_of(subperiod: Subperiod):
    from scripts.lib.twr_math import SubperiodChain

    return SubperiodChain(
        subperiods=(subperiod,),
        returns=(subperiod.ret,) if subperiod.ret is not None else (),
        cum_return=subperiod.ret or 0.0,
        n_nav_observations=2,
        n_subperiods=1,
        n_returns=1 if subperiod.ret is not None else 0,
        period_start="2026-02-05",
        period_end="2026-02-06",
        calendar_days=1,
        excluded_suspect=False,
    )


def test_decision3_subperiod_extreme_is_an_error_not_an_informational_note():
    warnings = builder._subperiod_warnings(_chain_of(_chained_extreme(0.99999, 1.0)))
    extreme = [w for w in warnings if w["code"] == "SUBPERIOD_EXTREME"]
    assert extreme, f"expected SUBPERIOD_EXTREME, got {[w['code'] for w in warnings]}"
    assert extreme[0]["severity"] == "error"
    # severity error floors the status at degraded (§C.1).
    assert builder._resolve_status("ok", extreme) == "degraded"


def test_decision3_extreme_message_never_claims_a_recorded_zero_flow():
    """R2's second half: "with a recorded +0.00 flow" contradicts itself."""
    warnings = builder._subperiod_warnings(_chain_of(_chained_extreme(-1.0, 0.0)))
    messages = " ".join(w["message"] for w in warnings)
    assert "+0.00 flow" not in messages
    assert "recorded +0.00" not in messages


# ===========================================================================
# R3 / DECISION 2 — the dispersion bar disabling itself
# ===========================================================================
#
# `_outlier_threshold` returns None when `len(magnitudes) < MIN_N_DISPERSION`
# and again when the median is 0. Both make the bar vanish, and an unexplained
# deposit below SUSPECT_RETURN_THRESHOLD publishes as `ok` with no warnings.
# DECISION 2: the bar falls back to UNEXPLAINED_ABSOLUTE_FLOOR = 0.10, never to
# "no bar".

UNEXPLAINED_ABSOLUTE_FLOOR = 0.10


def test_r3_a_young_account_below_the_dispersion_gate_still_has_a_bar():
    nav = fx.young_account_nav()
    payload = build_payload(observations(nav), FlowSet.empty_verified())
    end_date = sorted(nav)[-1]

    # 15 observations -> 14 returns, below MIN_N_DISPERSION (20).
    assert payload["counts"]["n_subperiods"] == 14
    assert 14 < twr_gates.MIN_N_DISPERSION

    # 42,995.41 / 196,685.31 = 0.21860000627398155 — under the 0.50 absolute
    # threshold, over the 0.10 floor.
    assert 0.21860000627398155 < twr_gates.SUSPECT_RETURN_THRESHOLD
    assert 0.21860000627398155 > UNEXPLAINED_ABSOLUTE_FLOOR

    assert subperiod_on(payload, end_date)["skip_reason"] == "suspect_no_flow"
    assert payload["counts"]["n_suspect"] == 1
    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)


def test_r3_a_dormant_flat_account_still_has_a_bar():
    nav = fx.flat_account_nav()
    payload = build_payload(observations(nav), FlowSet.empty_verified())
    end_date = sorted(nav)[-1]

    # 23 observations -> 22 returns, ABOVE MIN_N_DISPERSION, but 21 of them are
    # exactly 0.0 so the median is 0 and the bar switches itself off.
    assert payload["counts"]["n_subperiods"] == 22
    assert statistics.median(magnitudes_of(nav)) == 0.0

    # (232,497.53 - 189,502.12) / 189,502.12
    #   = 42,995.41 / 189,502.12 = 0.22688616887241153
    assert subperiod_on(payload, end_date)["skip_reason"] == "suspect_no_flow"
    assert payload["counts"]["n_suspect"] == 1
    assert payload["status"] == "degraded"
    # Anti-pin: the +22.69% deposit must not be published as a return.
    published = None if payload["twr"] is None else payload["twr"]["cum_return"]
    assert published != pytest.approx(0.22688616887241153, rel=1e-6)


# ===========================================================================
# R4 — the interpolated fixture flattens the dispersion 13x
# ===========================================================================


def test_r4_the_real_series_is_thirteen_times_more_dispersed_than_the_fixture():
    """Fixture property, computed off the NAV maps alone — no code under test."""
    real = [m for m in magnitudes_of(fx.real_live_nav()) if m <= 0.5]
    interpolated = [m for m in magnitudes_of(fx.interpolated_live_nav()) if m <= 0.5]

    # Both series carry the same two >50% sessions (2026-01-13, 2026-02-06), so
    # the same 55 magnitudes are in play; only the values differ.
    assert len(real) == len(interpolated) == 55

    assert statistics.median(real) == pytest.approx(0.032526482971235726, rel=REL)
    assert statistics.median(interpolated) == pytest.approx(0.0024397322355678153, rel=REL)
    # 0.032526482971235726 / 0.0024397322355678153 = 13.33...
    assert statistics.median(real) / statistics.median(interpolated) > 13.0


def test_r4_the_real_series_quarantines_the_2026_01_26_deposit():
    """The claim test_74 makes on the interpolated series, made on production.

    232,497.52580117 / 189,502.12175117 - 1 = 0.22688613537771407 — an
    unrecorded 42,995.41 deposit, chained today because the real series' own
    median |r| of 0.03252648 lifts the dispersion bar above it.
    """
    payload = build_payload(observations(fx.real_live_nav()), FlowSet.empty_verified())

    for suspect_date in fx.REAL_UNEXPLAINED_DATES:
        sp = subperiod_on(payload, suspect_date)
        assert sp["skip_reason"] == "suspect_no_flow", suspect_date
        assert sp["r"] is None, suspect_date

    assert payload["counts"]["n_suspect"] >= 3
    assert payload["status"] == "degraded"

    published = None if payload["twr"] is None else payload["twr"]["cum_return"]
    if published is not None:
        # Anti-pin: the live +951.28%, and the +22.69% the 01-26 session alone
        # would still inject.
        assert published != pytest.approx(fx.LIVE_CONTAMINATED_CUM_RETURN, rel=0.01)
        assert abs(published) < 0.30


def test_r4_the_interpolated_fixture_cannot_be_mistaken_for_the_real_one():
    assert not hasattr(fx, "live_nav"), (
        "the geometrically interpolated series must not be reachable under a "
        "name that reads like production data; use interpolated_live_nav()"
    )
    assert fx.real_live_nav() != fx.interpolated_live_nav()
    assert sorted(fx.real_live_nav()) == sorted(fx.interpolated_live_nav())


# ===========================================================================
# R7 / DECISION 6 — sessions_behind counts COMPLETED sessions, once
# ===========================================================================


def parity_cases():
    return json.loads(PARITY_TABLE.read_text())["cases"]


@pytest.mark.parametrize("case", parity_cases(), ids=lambda c: f"{c['nav_as_of']}->{c['last_completed_session']}")
def test_r7_sessions_behind_matches_the_shared_parity_table(case):
    """The single definition, pinned here and mirrored in web/tests.

    Every `expected` in the table was counted by hand off a 2026 calendar; the
    `why` field on each row carries the enumeration.
    """
    got = sessions_behind(
        case["nav_as_of"], date.fromisoformat(case["last_completed_session"])
    )
    assert got == case["expected"], case["why"]


def test_r7_last_completed_session_honours_the_1600_et_close():
    """DECISION 6's boundary, defined once so the default argument can use it."""
    last_completed_session = getattr(builder, "last_completed_session", None)
    assert last_completed_session is not None, (
        "perf_twr_builder must expose last_completed_session(now) so "
        "sessions_behind's default and the TS mirror share one 16:00 ET boundary"
    )

    cases = [
        # Fri 2026-08-14 one minute before the close: Thursday is the last
        # session that has actually finished.
        (datetime(2026, 8, 14, 15, 59, tzinfo=ET), date(2026, 8, 13)),
        (datetime(2026, 8, 14, 16, 0, tzinfo=ET), date(2026, 8, 14)),
        # The radon-perf-twr timer hour.
        (datetime(2026, 8, 14, 20, 45, tzinfo=ET), date(2026, 8, 14)),
        # Saturday and the following Monday morning both still read Friday.
        (datetime(2026, 8, 15, 12, 0, tzinfo=ET), date(2026, 8, 14)),
        (datetime(2026, 8, 17, 9, 0, tzinfo=ET), date(2026, 8, 14)),
        # MLK Monday after the close: the holiday never opened.
        (datetime(2026, 1, 19, 20, 0, tzinfo=ET), date(2026, 1, 16)),
    ]
    for now, expected in cases:
        assert last_completed_session(now) == expected, now.isoformat()


def test_r7_nav_stale_fires_once_three_sessions_have_completed():
    """The timer runs at 20:45 ET, after the close, so `end` is a full session.

    nav_as_of 2026-08-11 with 2026-08-14 complete is 3 sessions behind, which is
    past NAV_STALENESS_BUDGET_SESSIONS (2). Under-counting by one is what keeps
    the amber banner off a NAV that has aged out of the budget.
    """
    behind = sessions_behind("2026-08-11", date(2026, 8, 14))
    assert behind == 3
    assert behind > twr_gates.NAV_STALENESS_BUDGET_SESSIONS

    warnings = builder._freshness_warnings("flex_live", behind, "2026-08-11")
    assert [w["code"] for w in warnings] == ["NAV_STALE"]


# ===========================================================================
# R8 (builder half) — a suppressed payload must not report a count it never
# computed
# ===========================================================================


def test_r8_a_suppressed_payload_does_not_invent_a_return_count():
    payload = build_payload(observations(fx.golden_nav()), FlowSet.failed("timeout"))

    assert payload["status"] == "degraded"
    assert payload["series"] == []
    assert payload["subperiods"] == []
    # Zero subperiods were chained on this branch, so zero returns exist. The
    # 60 the builder reports today is rendered verbatim as "N=60" next to an
    # ending equity, on a payload with no series at all.
    assert payload["counts"]["n_returns"] == 0
    assert payload["counts"]["n_nav_observations"] == 61
    assert payload["counts"]["n_subperiods"] == 60
    assert payload["counts"]["n_skipped"] == 0
    assert payload["counts"]["n_suspect"] == 0


def test_r8_the_suppressed_payload_shape_the_web_fixture_must_mirror():
    """Every literal the TS `flowsFailedDegradedPayload()` fixture has to match.

    The fixture claims calendar_days 0, equity {} and n 0; these are what the
    builder actually emits, transcribed from a real call.
    """
    payload = build_payload(observations(fx.golden_nav()), FlowSet.failed("timeout"))

    # 2026-01-02 -> 2026-03-27
    assert payload["period_start"] == "2026-01-02"
    assert payload["period_end"] == "2026-03-27"
    assert payload["calendar_days"] == 84

    assert payload["equity"] == {
        "starting": 100_000.0,
        "ending": pytest.approx(182_217.35574011656, rel=1e-12),
        "net_external_flows": None,
        "investment_pnl": None,
    }

    # The GatedValue `n` stays the sample the NAV series supports, so the card
    # never blames its sample size for a flows failure.
    assert payload["risk"]["volatility"]["n"] == 60
    assert payload["risk"]["volatility"]["unavailable_reason"] == "no_flow_data"
    assert payload["mwr"]["period_return"]["n"] == 60
    assert payload["mwr"]["period_return"]["unavailable_reason"] == "no_flow_data"
    assert payload["flows_status"] == "failed"


# ===========================================================================
# §C.4 — the warning-code allowlist is exhaustive, and enforced
# ===========================================================================

SPEC = REPO_ROOT / "docs" / "performance-refactor-spec.md"


def spec_allowlisted_codes() -> set[str]:
    text = SPEC.read_text()
    marker = "Allowlisted codes (exhaustive"
    start = text.index(marker)
    block = re.search(r"```\n(.*?)```", text[start:], re.DOTALL)
    assert block, "§C.4 must keep the allowlist in a fenced block"
    return set(block.group(1).split())


def emitted_warning_codes() -> set[str]:
    tree = ast.parse((REPO_ROOT / "scripts" / "perf_twr_builder.py").read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "_warning" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.add(first.value)
    return out


def test_the_spec_allowlist_parses_and_covers_the_known_codes():
    allowed = spec_allowlisted_codes()
    assert "FLOWS_TRANSFERS_SECTION_ABSENT" in allowed
    assert "SUBPERIOD_SUSPECT" in allowed
    assert len(allowed) >= 23


def test_every_emitted_warning_code_is_in_the_spec_allowlist():
    emitted = emitted_warning_codes()
    assert emitted, "the AST scan found no _warning() calls; the scan is broken"
    unlisted = sorted(emitted - spec_allowlisted_codes())
    assert unlisted == [], (
        f"{unlisted} are emitted but absent from §C.4's exhaustive allowlist. "
        "Either add them to the spec block and the TS union, or stop emitting them."
    )

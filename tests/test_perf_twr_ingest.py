"""Ingest, integrity-gate and wiring defects from the adversarial audit.

RED SUITE. Every test here reproduces a defect recorded in
`docs/performance-audit-findings.md` with the reviewer's own input, and asserts
the behavior the spec requires instead. Each one fails against the current tree.

Naming: `test_<lens>_d<n>_...` so a failure names the register entry it belongs
to. The math lens is `math-D*`, the flow lens `flows-D*`, the test lens
`tests-D*`.

No network, no Turso, no Flex: `fetch_flex_xml`, the benchmark fetch and the
risk-free fetch are all monkeypatched, and every disk path is redirected into
`tmp_path`.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.lib import twr_gates
from scripts.lib.flex_flows import parse_flows
from scripts.lib.twr_math import FlowSet, FlowsStatus, NavObservation

import scripts.perf_twr_builder as builder
from scripts.perf_twr_builder import build_payload, parse_nav_entries, sessions_behind

from fixtures import twr_scenarios as fx  # noqa: E402  (tests/ dir, inserted above)

REL = 1e-9
ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# helpers (mirrors of the ones in test_perf_twr_flows.py)
# ---------------------------------------------------------------------------


def observations(nav_map):
    return tuple(NavObservation(date=d, nav=float(v)) for d, v in sorted(nav_map.items()))


def ok_flows(mapping=None):
    return FlowSet(
        status=FlowsStatus.OK,
        by_date=dict(mapping or {}),
        source="flex_cash_transactions+transfers",
    )


def codes(payload):
    return [w["code"] for w in payload["warnings"]]


def warning_with(payload, code):
    matches = [w for w in payload["warnings"] if w["code"] == code]
    assert matches, f"expected a {code} warning, got {codes(payload)}"
    return matches[0]


def subperiod_on(payload, iso_date):
    for sp in payload["subperiods"]:
        if sp["date"] == iso_date:
            return sp
    raise AssertionError(f"no subperiod for {iso_date}")


def flex_backed_payload(monkeypatch, tmp_path, xml, *, calls=None):
    """Run the real `build_and_persist` ingest path over one canned statement."""
    monkeypatch.setenv("IB_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1497709")
    monkeypatch.delenv("IB_FLEX_FLOWS_QUERY_ID", raising=False)
    monkeypatch.setattr(builder, "_NAV_CACHE_PATH", tmp_path / "nav_history_ib.json")
    monkeypatch.setattr(builder, "_IB_NAV_CACHE_DIR", tmp_path / "absent")
    monkeypatch.setattr(builder, "load_benchmark_closes", lambda *a, **kw: {})
    monkeypatch.setattr(builder, "get_risk_free_rate", lambda *a, **kw: (0.0, "stub"))

    recorded = [] if calls is None else calls

    def _fetch(token, query_id, **_kw):
        recorded.append(query_id)
        return xml

    monkeypatch.setattr(builder, "fetch_flex_xml", _fetch)
    return builder.build_and_persist(persist=False)


# ===========================================================================
# LENS: math
# ===========================================================================


def test_math_d1_a_constant_portfolio_series_degrades_instead_of_raising():
    """`statistics.correlation` raises when EITHER input is constant.

    `build_benchmark_block` guards only the benchmark side, and nothing above it
    catches `StatisticsError`, so a dormant cash-only account gets a traceback
    instead of the `benchmark: null` payload §C.6 promises for every status.
    """
    nav = fx.dormant_nav()
    benchmark = fx.moving_benchmark_closes(sorted(nav))

    payload = build_payload(observations(nav), ok_flows(), benchmark=benchmark)

    # 46 observations -> 45 returns, all exactly 0.0; n_common 45 clears the
    # 40-session benchmark gate at 100% coverage, so the block IS attempted.
    assert payload["counts"]["n_returns"] == 45
    assert payload["benchmark"] is None
    assert warning_with(payload, "BENCHMARK_UNAVAILABLE")["context"]["reason"]


def test_math_d2_a_nav_of_zero_is_not_a_publishable_minus_one_hundred_percent():
    """`_is_suspect` exempts exactly `r == -1.0`, which is the one value the
    ingest manufactures from a blank Flex attribute. The exemption also pins
    `growth` at 0 forever: every later `growth *= (1 + r)` stays 0."""
    payload = build_payload(
        observations(fx.nav_with_a_zeroed_row()),
        ok_flows(),
        nav_source="flex_live",
    )
    zeroed = fx.zeroed_row_date()

    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    assert payload["counts"]["n_suspect"] == 1
    assert subperiod_on(payload, zeroed)["skip_reason"] == "suspect_no_flow"
    assert subperiod_on(payload, zeroed)["r"] is None

    # 30 rows -> 29 subperiods: 27 of them are the +0.2% day, one is the zeroed
    # row (quarantined) and one has a zero base (skipped).
    # 1.002 ** 27 - 1 = 0.05542768340241899
    assert payload["counts"]["n_returns"] == 27
    assert payload["twr"]["cum_return"] == pytest.approx(0.05542768340241899, rel=REL)
    assert payload["twr"]["cum_return"] != pytest.approx(-1.0, rel=1e-6)


def test_math_d5_sessions_behind_does_not_count_the_in_progress_session():
    """The original D5 defect, re-pinned onto DECISION 6's single definition.

    Round 2 read the second argument as "today, possibly still open"; DECISION 6
    redefines it as `through`, a COMPLETED session, and moves the "is the
    current session over yet" question into `last_completed_session(now)`. The
    in-progress session must still never be counted, so the pin now runs
    through the default argument with a frozen pre-close clock, which is the
    only place that question is now answered.
    """
    friday_pre_close = datetime(2026, 8, 14, 15, 59, tzinfo=ET)

    # Fri 08-14 has not closed, so Thu 08-13 is the last completed session.
    assert builder.last_completed_session(friday_pre_close) == date(2026, 8, 13)

    # Wed 08-12 -> Thu 08-13 completed: one session behind.
    assert (
        sessions_behind("2026-08-12", builder.last_completed_session(friday_pre_close))
        == 1
    )
    # Tue 08-11 -> Wed 08-12 and Thu 08-13 completed: two.
    assert (
        sessions_behind("2026-08-11", builder.last_completed_session(friday_pre_close))
        == 2
    )
    # The budget is 2 sessions; over-reporting turns a fresh NAV amber.
    assert (
        sessions_behind("2026-08-12", builder.last_completed_session(friday_pre_close))
        <= twr_gates.NAV_STALENESS_BUDGET_SESSIONS
    )

    # After the close the same Friday counts, which is the other half of
    # DECISION 6: `through` includes the last completed session (parity table).
    friday_post_close = datetime(2026, 8, 14, 20, 45, tzinfo=ET)
    assert builder.last_completed_session(friday_post_close) == date(2026, 8, 14)
    assert (
        sessions_behind("2026-08-11", builder.last_completed_session(friday_post_close))
        == 3
    )


# ===========================================================================
# LENS: flows
# ===========================================================================


def test_flows_d1_a_one_dollar_flow_does_not_disable_the_suspect_quarantine():
    """§B.4 is implemented as an exact-zero test (`flow != 0.0`), so a $1 row on
    the same date as the unrecorded 725,502.03 ACATS publishes r=+2.9407 clean."""
    payload = build_payload(
        observations(fx.feb06_nav()),
        ok_flows({fx.FEB06_DATE: fx.FEB06_TOKEN_FLOW}),
        nav_source="flex_live",
        nav_sessions_behind=0,
    )
    sp = subperiod_on(payload, fx.FEB06_DATE)

    # (972215.53 - 1.00 - 246713.50) / 246713.50 = 2.9406620634865948
    # The recorded flow explains 1.00 of a 725,502.03 move: nothing.
    assert payload["status"] == "degraded"
    assert payload["warnings"] != []
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    assert sp["skip_reason"] == "suspect_no_flow"
    assert sp["r"] is None
    assert payload["twr"]["cum_return"] != pytest.approx(2.9406620634865948, rel=1e-3)


@pytest.mark.parametrize("case", sorted(fx.MALFORMED_EXTERNAL_CASH_ROWS))
def test_flows_d2_a_malformed_external_row_is_never_empty_verified(case):
    """A row found and classified EXTERNAL, then discarded for an unreadable
    amount or date, currently leaves `external_rows == 0` -> EMPTY_VERIFIED,
    whose documented meaning is "genuinely zero external flows"."""
    xml = fx.flex_xml(cash_transactions=[fx.MALFORMED_EXTERNAL_CASH_ROWS[case]])

    flowset = parse_flows(xml)

    assert flowset.status is not FlowsStatus.EMPTY_VERIFIED
    if flowset.status is FlowsStatus.OK:
        # 725,502.03 on 2026-02-06 however IBKR spelled it.
        assert dict(flowset.by_date) == pytest.approx({"2026-02-06": 725_502.03})


def test_flows_d3_a_cash_transactions_only_statement_is_not_fully_observed():
    """`_has_flow_section` is an OR, so the production configuration (a
    CashTransactions query, no Transfers section) parses as fully observed and
    hides the entire Transfer class."""
    xml = fx.flex_xml(
        cash_transactions=[fx.INTERNAL_FEE_ROW], include_transfer_section=False
    )

    flowset = parse_flows(xml)
    assert flowset.status is FlowsStatus.FAILED
    assert flowset.reason

    payload = build_payload(observations(fx.JAN26_NAV), flowset)
    # 232497.53 / 189502.12 - 1 = 0.22688616887241153 -- a 42,995.41 deposit
    # published as investment P&L under status "ok" with warnings [].
    assert payload["status"] == "degraded"
    assert payload["twr"] is None
    assert "FLOWS_FETCH_FAILED" in codes(payload)


def test_flows_d5_a_multi_account_statement_sums_nav_as_well_as_flows(
    monkeypatch, tmp_path
):
    """NAV is last-statement-wins (`out[report_date] = ...`) while flows sum
    every `<FlexStatement>`, which published a TWR of -284.5% as "ok"."""
    xml = fx.flex_statement_xml(
        accounts=fx.MULTI_ACCOUNT_NAV, cash_transactions=fx.MULTI_ACCOUNT_DEPOSITS
    )

    payload = flex_backed_payload(monkeypatch, tmp_path, xml)
    series = payload["series"]

    # 100000 + 20000 = 120000 ; 101000 + 20100 = 121100
    assert [point["nav"] for point in series] == [
        pytest.approx(120_000.0, rel=REL),
        pytest.approx(121_100.0, rel=REL),
    ]
    # residual    = 121100 - 57000 - 120000 = -55900
    # denominator = 120000 + 57000          = 177000
    #   -55900 / 177000 = -0.315819209039548
    assert subperiod_on(payload, "2026-01-06")["r"] == pytest.approx(
        -0.315819209039548, rel=REL
    )
    # Superseded EOD figure: -55900 / 120000 = -0.4658333333333333
    assert subperiod_on(payload, "2026-01-06")["r"] != pytest.approx(
        -0.4658333333333333, rel=1e-6
    )
    assert payload["twr"]["cum_return"] >= -1.0
    assert payload["twr"]["cum_return"] != pytest.approx(-2.845, rel=1e-3)
    assert payload["equity"]["net_external_flows"] == pytest.approx(57_000.0, rel=REL)


def test_flows_d6_a_blank_nav_attribute_is_not_a_nav_of_zero(monkeypatch, tmp_path):
    """`_safe_float(node.get("total"))` defaults to 0.0, so a blank attribute
    becomes a real NAV of zero and chains as a -100% session."""
    xml = fx.flex_statement_xml(accounts=fx.BLANK_TOTAL_NAV, include_cash_section=False)

    parsed = parse_nav_entries(xml)

    assert parsed.get("2026-01-05") == pytest.approx(100_000.0, rel=REL)
    assert parsed.get("2026-01-07") == pytest.approx(101_500.0, rel=REL)
    assert "2026-01-06" not in parsed
    assert 0.0 not in parsed.values()


def test_flows_d7_one_flex_fetch_per_distinct_query_id(monkeypatch, tmp_path):
    """`IB_FLEX_FLOWS_QUERY_ID` is unset in production, so NAV and flows resolve
    to the same query id and the builder requests it twice, seconds apart.
    IBKR throttles repeat requests for one query id into an embargo."""
    xml = fx.flex_statement_xml(
        accounts={"U111": {"2026-01-05": 100_000.0, "2026-01-06": 101_000.0}}
    )
    calls: list[str] = []

    flex_backed_payload(monkeypatch, tmp_path, xml, calls=calls)

    assert calls, "the builder never called Flex at all"
    assert len(calls) == len(set(calls)), f"the same query id was fetched twice: {calls}"
    assert set(calls) == {"1497709"}


# ===========================================================================
# LENS: tests
# ===========================================================================


def test_tests_d1_a_flow_dominant_subperiod_emits_the_flow_dominant_warning():
    """§C.3 requires one FLOW_DOMINANT info warning per flow-dominant
    subperiod. `_subperiod_warnings` handles only gaps, suspects and skips."""
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))

    # 2026-02-13: |C| / B = 100000 / 102577.56510647341 = 0.9749 > 0.25
    # 2026-03-06: |C| / B =  25000 / 204868.8123253928  = 0.1220 < 0.25
    assert "flow_dominant" in subperiod_on(payload, "2026-02-13")["flags"]
    dominant = [w for w in payload["warnings"] if w["code"] == "FLOW_DOMINANT"]
    assert len(dominant) == 1
    assert dominant[0]["severity"] == "info"
    assert dominant[0]["context"]["date"] == "2026-02-13"
    # An info warning does not move the status floor.
    assert payload["status"] == "ok"


@pytest.mark.parametrize(
    "flow,expect_flow_dominant",
    [
        # The cash leg of an ACATS is captured, the positionAmountInBase leg is
        # not. Round 2 let C != 0 defeat the suspect gate and |C|/B = 1e-5
        # defeat the flow-dominant flag, so a +100% session published clean.
        (fx.DOUBLING_TOKEN_FLOW, False),
        # (200000 - 40000 - 100000) / 100000 = 0.60 exact. Round 2 chained this
        # one with a SUBPERIOD_EXTREME note because C was material. DECISION 1
        # deletes that carve-out: the residual r is what is unexplained, and
        # 0.60 > SUSPECT_RETURN_THRESHOLD whatever C is, so it quarantines too.
        (fx.DOUBLING_MATERIAL_FLOW, True),
    ],
)
def test_tests_d2_an_extreme_session_never_publishes_silently(
    flow, expect_flow_dominant
):
    # KNOWN RED under the BOD convention, deliberately left failing rather than
    # re-pinned: the DOUBLING_MATERIAL_FLOW case is the whole point of DECISION 1
    # and BOD lets it escape. residual = 200000 - 40000 - 100000 = 60000 and
    # denominator = 100000 + 40000 = 140000, so r = 0.42857142857142855, which is
    # under SUSPECT_RETURN_THRESHOLD (0.50) where the EOD 0.60 was over it. The
    # sample is one return, so the dispersion bar is 5 * 0.42857 = 2.14 and does
    # not catch it either. A partially recorded transfer now publishes as `ok`
    # with an info-severity FLOW_DOMINANT and nothing else. Changing this
    # expectation to "not suspect" would invert the assertion's purpose; the fix
    # belongs in the gate, not in the pin.
    payload = build_payload(
        observations(fx.DOUBLING_NAV), ok_flows({"2026-01-06": flow})
    )
    sp = subperiod_on(payload, "2026-01-06")

    assert payload["warnings"] != []
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    assert payload["status"] == "degraded"
    assert sp["skip_reason"] == "suspect_no_flow"
    assert sp["r"] is None
    # The flow-dominant flag is descriptive only; it no longer gates anything.
    assert ("flow_dominant" in sp["flags"]) is expect_flow_dominant


def test_tests_d3_a_degraded_payload_still_reports_its_period_and_equity():
    """`_suppressed_payload` hardcodes `calendar_days=0` and leaves `equity`
    empty, so the hero renders "Ending equity $0.00 / N=0" on a 61-observation
    series. A suppressed metric is null with a reason, never a fabricated 0."""
    payload = build_payload(observations(fx.golden_nav()), FlowSet.failed("timeout"))
    equity = payload["equity"]

    assert payload["period_start"] == "2026-01-02"
    assert payload["period_end"] == "2026-03-27"
    # 2026-01-02 -> 2026-03-27 is 84 calendar days.
    assert payload["calendar_days"] == 84
    assert set(equity) == {"starting", "ending", "net_external_flows", "investment_pnl"}
    assert equity["starting"] == pytest.approx(100_000.0, rel=1e-12)
    assert equity["ending"] == pytest.approx(182_217.35574011656, rel=1e-12)
    # Without flows neither of these is knowable, and 0 would be a lie.
    assert equity["net_external_flows"] is None
    assert equity["investment_pnl"] is None
    # R8 / §C.5 split the two numbers that round 2 conflated. `counts.n_returns`
    # is what this run actually chained, which on the flows-failed branch is
    # zero; `GatedValue.n` stays the sample the NAV series supports, so the card
    # never blames its sample size for a flows failure.
    assert payload["counts"]["n_returns"] == 0
    assert payload["mwr"]["period_return"]["n"] == 60
    assert payload["mwr"]["period_return"]["unavailable_reason"] == "no_flow_data"


def test_tests_d5_a_consolidated_group_drops_a_gap_date_and_says_so(
    monkeypatch, tmp_path
):
    """`consolidate_accounts` has no production caller, so NAV_ACCOUNT_GAP is
    unreachable and a date one account is missing is summed short."""
    # Both flow sections are present and empty, so the flows are `empty_verified`
    # and this test stays about consolidation rather than about flows-D3.
    xml = fx.flex_statement_xml(
        accounts=fx.ACCOUNT_GAP_NAV, include_transfer_section=True
    )

    payload = flex_backed_payload(monkeypatch, tmp_path, xml)

    assert "NAV_ACCOUNT_GAP" in codes(payload)
    assert warning_with(payload, "NAV_ACCOUNT_GAP")["context"]["date"] == "2026-01-06"
    # U222 has no 2026-01-06 row, so that date is dropped, never summed short.
    assert [point["date"] for point in payload["series"]] == ["2026-01-05", "2026-01-07"]
    assert payload["counts"]["n_nav_observations"] == 2
    # 100000 + 20000 = 120000 ; 102000 + 20400 = 122400 ; 122400/120000 - 1 = 0.02
    assert subperiod_on(payload, "2026-01-07")["r"] == pytest.approx(0.02, rel=REL)

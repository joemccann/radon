"""TWR portfolio performance — the v2 contract.

This suite is the successor to the v1 twenty-case suite. Every scenario the v1
suite covered is preserved here; the ones the refactor spec deliberately
inverts (annualization gate, VaR gate, benchmark gate, flow classification) are
re-pinned against the NEW behavior rather than dropped, so no coverage is lost:

  v1 scenario                       v2 expression
  --------------------------------  ------------------------------------------
  zero flows                        test_zero_flows_chains_cleanly
  golden no-flow fixture            test_golden_no_flow_fixture_total_return
  deposit / withdrawal excluded     test_deposit_..., test_withdrawal_...
  ACATS +/- 50k                     test_acats_...  (now a <Transfer> element)
  split, no phantom return          test_split_adjusted_nav_has_no_phantom_return
  dividend is return, not flow      test_dividend_is_internal_not_an_external_flow
  inception is first NAV            test_inception_is_the_first_nav_date
  N gate: annualized                test_annualized_is_gated_on_calendar_days   (was N>=20)
  N gate: beta                      test_beta_is_gated_on_n_common              (was n_returns)
  N gate: VaR                       test_var_is_gated_at_min_n_dispersion       (was N>=60)
  multi-account internal transfer   test_multi_account_internal_transfer_nets_to_zero
  no hard-coded 2025-12-31          test_no_hardcoded_inception_date
  Flex missing                      test_missing_nav_is_unavailable_...
  holiday calendar                  test_calendar_is_nav_dates_only
  borrow fee is not a flow          test_fees_and_interest_are_internal
  Rf fallback                       test_risk_free_fallback_is_labelled
  twr_return edge cases             test_subperiod_return_edge_cases
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.lib import twr_gates
from scripts.lib.flex_flows import (
    classify_flow_type,
    flows_from_rows,
    is_external_flow_type,
    parse_flows,
)
from scripts.lib.twr_math import (
    AlignedPairs,
    FlowClass,
    FlowSet,
    NavObservation,
    UnknownFlowType,
    align_series,
    build_benchmark_block,
    build_subperiods,
    consolidate_accounts,
    subperiod_return,
)
from scripts.perf_twr_builder import CURVE_TYPE, RETURN_BASIS, build_payload

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "perf_nav_tw_no_flows.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def observations(nav: dict) -> list[NavObservation]:
    return [NavObservation(date=d, nav=float(v)) for d, v in sorted(nav.items())]


def chain_of(nav: dict, flows: dict | None = None):
    return build_subperiods(observations(nav), flows or {})


def payload_of(nav: dict, flows: dict | None = None, **kwargs):
    flow_set = flows_from_rows(flows, source="test") if flows else FlowSet.empty_verified()
    return build_payload(observations(nav), flow_set, **kwargs)


def returns_by_date(chain) -> dict:
    return {sp.date: sp.ret for sp in chain.subperiods}


# ---------------------------------------------------------------------------
# 1. Zero flows — base TWR correctness
# ---------------------------------------------------------------------------

def test_zero_flows_chains_cleanly():
    nav = {"2026-01-02": 100_000, "2026-01-05": 110_000, "2026-01-06": 121_000}
    chain = chain_of(nav)

    assert chain.n_nav_observations == 3
    assert chain.n_subperiods == 2
    assert chain.returns == pytest.approx((0.10, 0.10))
    assert chain.cum_return == pytest.approx(0.21)

    payload = payload_of(nav)
    assert payload["status"] == "ok"
    assert payload["twr"]["cum_return"] == pytest.approx(0.21)
    assert payload["methodology"]["curve_type"] == CURVE_TYPE
    assert payload["methodology"]["return_basis"] == RETURN_BASIS
    assert payload["equity"]["starting"] == pytest.approx(100_000)
    assert payload["equity"]["ending"] == pytest.approx(121_000)
    assert payload["equity"]["net_external_flows"] == pytest.approx(0.0)
    assert payload["equity"]["investment_pnl"] == pytest.approx(21_000)


def test_golden_no_flow_fixture_total_return():
    data = json.loads(FIXTURE_PATH.read_text())
    payload = payload_of(data["nav_snapshots"], data["external_flows"] or None)
    assert payload["twr"]["cum_return"] == pytest.approx(
        data["expected_total_return"], abs=1e-8
    )


# ---------------------------------------------------------------------------
# 2-3. Deposit / withdrawal excluded from return
# ---------------------------------------------------------------------------

def test_deposit_is_excluded_from_return():
    nav = {"2026-03-13": 200_000, "2026-03-16": 260_000, "2026-03-17": 265_000}
    chain = chain_of(nav, {"2026-03-16": 50_000})
    rets = returns_by_date(chain)

    assert rets["2026-03-16"] == pytest.approx(0.05)
    assert rets["2026-03-17"] == pytest.approx(5_000 / 260_000)
    assert chain.cum_return == pytest.approx(1.05 * (1 + 5_000 / 260_000) - 1)


def test_withdrawal_is_excluded_from_return():
    nav = {"2026-06-01": 500_000, "2026-06-02": 380_000, "2026-06-03": 390_000}
    chain = chain_of(nav, {"2026-06-02": -100_000})
    rets = returns_by_date(chain)

    assert rets["2026-06-02"] == pytest.approx(-0.04)
    assert chain.cum_return == pytest.approx(0.96 * (1 + 10_000 / 380_000) - 1)


def test_payload_reports_flows_separately_from_investment_pnl():
    """The raw NAV delta is 60k; only 10k of it was earned."""
    nav = {"2026-03-13": 200_000, "2026-03-16": 260_000}
    payload = payload_of(nav, {"2026-03-16": 50_000})

    assert payload["status"] == "ok"
    assert payload["flows_status"] == "ok"
    assert payload["twr"]["cum_return"] == pytest.approx(0.05)
    assert payload["equity"]["net_external_flows"] == pytest.approx(50_000)
    assert payload["equity"]["investment_pnl"] == pytest.approx(10_000)
    assert payload["subperiods"][0]["c"] == pytest.approx(50_000)


# ---------------------------------------------------------------------------
# 4-5. ACATS in / out — now a <Transfer> element, not a `type` string
# ---------------------------------------------------------------------------

ACATS_XML = """<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement>
      <CashTransactions/>
      <Transfers>
        <Transfer type="ACATS" direction="{direction}" assetCategory="STK"
                  reportDate="2026-04-02" quantity="1000" transferPrice="50.00"
                  positionAmountInBase="50000.00" cashTransfer="0"/>
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_acats_in_is_an_external_flow_at_position_value():
    flows = parse_flows(ACATS_XML.format(direction="IN"))
    assert flows.by_date == {"2026-04-02": pytest.approx(50_000.00)}
    # positionAmountInBase, never the per-share transferPrice
    assert flows.by_date["2026-04-02"] != pytest.approx(50.00)

    chain = chain_of({"2026-04-01": 300_000, "2026-04-02": 360_000}, dict(flows.by_date))
    assert chain.returns[0] == pytest.approx(10_000 / 300_000)


# ---------------------------------------------------------------------------
# A real ACATS is a BASKET, and some of its legs are genuinely negative.
#
# Transcribed from the operator's Flex export for 2026-02-06 (query 1442520,
# accountId U4698258). Two legs carry negative amounts under direction="IN":
# a short ETHA call, which is a liability being taken on, and the cash leg,
# which left the account as part of the transfer. `abs()`-ing the magnitude
# before applying the direction sign flipped both, inflating the flow from
# 655,497.16 to 1,282,260.84 -- a 626,763.68 error, exactly 2x the two
# negative legs. Only real data exposed this; the prior fixtures were all
# positive-amount, so IN and OUT both looked correct.
# ---------------------------------------------------------------------------

REAL_ACATS_BASKET = """<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement>
      <CashTransactions/>
      <Transfers>
        <Transfer type="ACATS" direction="IN" assetCategory="STK" symbol="MSFT"
                  reportDate="2026-02-06" positionAmountInBase="393670" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="STK" symbol="NFLX"
                  reportDate="2026-02-06" positionAmountInBase="363915" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="STK" symbol="RR"
                  reportDate="2026-02-06" positionAmountInBase="30400" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="STK" symbol="URTY"
                  reportDate="2026-02-06" positionAmountInBase="117260" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="ETHA 15C"
                  reportDate="2026-02-06" positionAmountInBase="49600" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="ETHA 30C"
                  reportDate="2026-02-06" positionAmountInBase="-7434" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="SOFI 45C"
                  reportDate="2026-02-06" positionAmountInBase="14034" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="CASH" symbol="--"
                  reportDate="2026-02-06" positionAmountInBase="0" cashTransfer="-305947.84"/>
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_acats_basket_preserves_negative_legs():
    flows = parse_flows(REAL_ACATS_BASKET)
    # 393670 + 363915 + 30400 + 117260 = 905,245 long stock
    #   + 49600 - 7434 + 14034        =  56,200 net options (the 30C is short)
    #   - 305947.84                    = -305,947.84 cash leaving with the transfer
    #                                  = 655,497.16
    assert flows.by_date == {"2026-02-06": pytest.approx(655_497.16)}
    # the abs() bug produced this instead: 655,497.16 + 2 * (7434 + 305947.84)
    assert flows.by_date["2026-02-06"] != pytest.approx(1_282_260.84)


REBOOK_PAIR = """<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement>
      <CashTransactions/>
      <Transfers>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="ETHA 15C"
                  reportDate="2026-02-09" positionAmountInBase="-49600" cashTransfer="0" code="Ca"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="ETHA 15C"
                  reportDate="2026-02-09" positionAmountInBase="49600" cashTransfer="0"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="SOFI 45C"
                  reportDate="2026-02-09" positionAmountInBase="-14034" cashTransfer="0" code="Ca"/>
        <Transfer type="ACATS" direction="IN" assetCategory="OPT" symbol="SOFI 45C"
                  reportDate="2026-02-09" positionAmountInBase="14034" cashTransfer="0"/>
      </Transfers>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_cancel_and_rebook_pair_nets_to_zero():
    """IBKR cancels a mis-booked transfer leg and re-books it the same day.

    Real rows from 2026-02-09. The cancel carries the negative amount, so the
    pair must net to zero. Under abs() it summed to +127,268.00 and invented an
    external flow on a day nothing moved.
    """
    flows = parse_flows(REBOOK_PAIR)
    # (-49600 + 49600) + (-14034 + 14034) = 0
    assert flows.by_date["2026-02-09"] == pytest.approx(0.0)


def test_transferring_out_a_short_position_is_a_positive_flow():
    """Shedding a liability increases account value, so OUT of a negative
    position amount is a POSITIVE external flow. The direction sign multiplies
    the reported sign; it does not replace it."""
    xml = ACATS_XML.format(direction="OUT").replace(
        'positionAmountInBase="50000.00"', 'positionAmountInBase="-50000.00"'
    )
    flows = parse_flows(xml)
    # -1 (OUT) * -50,000 (a short position) = +50,000
    assert flows.by_date == {"2026-04-02": pytest.approx(50_000.00)}


def test_acats_out_is_a_negative_external_flow():
    flows = parse_flows(ACATS_XML.format(direction="OUT"))
    assert flows.by_date == {"2026-04-02": pytest.approx(-50_000.00)}

    chain = chain_of({"2026-04-01": 300_000, "2026-04-02": 240_000}, dict(flows.by_date))
    assert chain.returns[0] == pytest.approx(-10_000 / 300_000)


def test_acats_is_not_classified_by_type_string():
    """v1 asserted `is_external_flow_type("acats") is True` via a substring match.

    The allowlist classifier refuses to guess: transfers are recognized
    structurally, from the <Transfer> element, not from a CashTransaction type.
    """
    with pytest.raises(UnknownFlowType):
        classify_flow_type("acats")


# ---------------------------------------------------------------------------
# 6. Split — NAV is split-adjusted, so no phantom return
# ---------------------------------------------------------------------------

def test_split_adjusted_nav_has_no_phantom_return():
    nav = {"2026-06-08": 100_000, "2026-06-09": 100_000, "2026-06-10": 101_000}
    chain = chain_of(nav)
    assert chain.returns == pytest.approx((0.0, 0.01))
    assert chain.cum_return == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# 7. Dividend is return, not flow
# ---------------------------------------------------------------------------

DIVIDEND_XML = """<FlexQueryResponse>
  <FlexStatements>
    <FlexStatement>
      <CashTransactions>
        <CashTransaction type="Dividends" dateTime="2026-05-12" amount="240.00"/>
      </CashTransactions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_dividend_is_internal_not_an_external_flow():
    assert classify_flow_type("Dividends") is FlowClass.INTERNAL
    assert is_external_flow_type("Dividends") is False

    flows = parse_flows(DIVIDEND_XML)
    assert flows.by_date == {}

    nav = {"2026-05-11": 100_000, "2026-05-12": 100_240}
    assert chain_of(nav, dict(flows.by_date)).returns[0] == pytest.approx(0.0024)
    # If it were mis-classified as external the dividend would vanish from return.
    assert chain_of(nav, {"2026-05-12": 240}).returns[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 8. Inception is the first NAV date
# ---------------------------------------------------------------------------

def test_inception_is_the_first_nav_date():
    nav = {"2026-05-01": 50_000, "2026-05-04": 52_000, "2026-05-05": 51_000}
    payload = payload_of(nav)

    assert payload["period_start"] == "2026-05-01"
    assert payload["period_end"] == "2026-05-05"
    expected = (52_000 / 50_000) * (51_000 / 52_000) - 1
    assert payload["twr"]["cum_return"] == pytest.approx(expected)


def test_no_hardcoded_inception_date():
    nav = {"2026-01-02": 100_000, "2026-01-05": 101_000}
    payload = payload_of(nav)
    assert "2025-12-31" not in [p["date"] for p in payload["series"]]
    assert "2025-12-31" not in [s["date"] for s in payload["subperiods"]]


def test_calendar_is_nav_dates_only():
    """2026-01-01 is a market holiday and has no NAV row, so it has no subperiod."""
    nav = {"2025-12-31": 100_000, "2026-01-02": 101_000, "2026-01-05": 102_000}
    payload = payload_of(nav)

    assert "2026-01-01" not in [s["date"] for s in payload["subperiods"]]
    assert payload["period_start"] == "2025-12-31"
    assert payload["counts"]["n_returns"] == 2


# ---------------------------------------------------------------------------
# 9-11. Gates — re-pinned against the v2 gate table
# ---------------------------------------------------------------------------

def _weekday_nav(count: int, *, start_year: int = 2026) -> dict:
    from datetime import date, timedelta

    nav, day, value = {}, date(start_year, 1, 2), 100_000.0
    while len(nav) < count:
        if day.weekday() < 5:
            nav[day.isoformat()] = value
            value *= 1.001
        day += timedelta(days=1)
    return nav


def test_annualized_is_gated_on_calendar_days_not_sample_size():
    """v1 published an annualized return at N>=20. GIPS forbids annualizing a
    sub-year period, and that exponent is what rendered +3,288,954%."""
    short = payload_of(_weekday_nav(40))  # ~8 weeks, N=39
    assert short["counts"]["n_returns"] >= twr_gates.MIN_N_DISPERSION
    assert short["twr"]["annualized"]["value"] is None
    assert short["twr"]["annualized"]["unavailable_reason"] == "period_lt_1y"

    long = payload_of(_weekday_nav(300))  # > 365 calendar days
    assert long["calendar_days"] >= twr_gates.MIN_CALENDAR_DAYS_ANNUALIZED
    assert long["twr"]["annualized"]["value"] is not None


def test_var_is_gated_at_min_n_dispersion():
    """v1 gated VaR at N>=60. k = floor(0.05N) >= 1 only needs N>=20; below 100
    the honest signal is low_confidence, not a dash."""
    below = payload_of(_weekday_nav(twr_gates.MIN_N_DISPERSION))  # N = 19
    assert below["counts"]["n_returns"] == twr_gates.MIN_N_DISPERSION - 1
    assert below["risk"]["var_95"]["value"] is None
    assert below["risk"]["var_95"]["unavailable_reason"] == "insufficient_n"
    assert below["risk"]["var_95"]["min_n"] == twr_gates.MIN_N_DISPERSION

    at = payload_of(_weekday_nav(twr_gates.MIN_N_DISPERSION + 1))  # N = 20
    assert at["risk"]["var_95"]["value"] is not None
    assert at["risk"]["cvar_95"]["value"] is not None
    assert at["risk"]["var_95"]["low_confidence"] is True


def test_ratios_are_gated_at_min_n_ratio():
    below = payload_of(_weekday_nav(twr_gates.MIN_N_RATIO))  # N = 59
    assert below["risk"]["sharpe_ratio"]["value"] is None
    assert below["risk"]["sharpe_ratio"]["unavailable_reason"] == "insufficient_n"

    at = payload_of(_weekday_nav(twr_gates.MIN_N_RATIO + 1))  # N = 60
    assert at["risk"]["sharpe_ratio"]["value"] is not None


def test_beta_is_gated_on_n_common_not_n_returns():
    """The gate must be on the ALIGNED sample. v1 gated on the portfolio's own N,
    which let a 4-point benchmark produce beta = 23.93."""
    portfolio = {f"2026-01-{i:02d}": 0.001 for i in range(1, 61)}
    sparse = {f"2026-01-{i:02d}": 0.001 * (1 if i % 2 else -1) for i in range(1, 40)}

    pairs = align_series(portfolio, sparse)
    assert pairs.n_common < twr_gates.MIN_N_BENCHMARK
    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=len(portfolio), symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "insufficient_n"


def test_zero_variance_benchmark_is_refused():
    dates = [f"2026-02-{i:02d}" for i in range(1, 46)]
    pairs = AlignedPairs(
        dates=tuple(dates),
        portfolio=tuple(0.001 * (1 if i % 2 else -1) for i in range(len(dates))),
        benchmark=tuple(0.0 for _ in dates),
        n_common=len(dates),
    )
    block, reason = build_benchmark_block(
        pairs, 0.0, n_returns=len(dates), symbol="SPY", basis="price_return"
    )
    assert block is None
    assert reason == "benchmark_degenerate"


def test_benchmark_absent_emits_no_benchmark_derived_key():
    payload = payload_of(_weekday_nav(45))
    assert payload["benchmark"] is None
    blob = json.dumps(payload)
    for key in ("beta", "alpha", "tracking_error", "information_ratio", "r_squared"):
        assert f'"{key}"' not in blob


# ---------------------------------------------------------------------------
# 12. Multi-account internal transfer nets to zero
# ---------------------------------------------------------------------------

def test_multi_account_internal_transfer_nets_to_zero():
    per_nav = {
        "U123": {"2026-01-02": 200_000, "2026-01-05": 100_000},
        "U456": {"2026-01-02": 0, "2026-01-05": 100_000},
    }
    per_flows = {
        "U123": {"2026-01-05": -100_000},
        "U456": {"2026-01-05": 100_000},
    }
    nav, flows, gaps = consolidate_accounts(per_nav, per_flows)

    assert gaps == []
    assert {o.date: o.nav for o in nav}["2026-01-05"] == pytest.approx(200_000)
    assert flows.get("2026-01-05", 0.0) == pytest.approx(0.0)

    chain = build_subperiods(nav, flows)
    assert chain.cum_return == pytest.approx(0.0)


def test_consolidated_account_gap_drops_the_date():
    per_nav = {
        "U123": {"2026-01-02": 100_000, "2026-01-05": 101_000, "2026-01-06": 102_000},
        "U456": {"2026-01-02": 50_000, "2026-01-06": 51_000},
    }
    nav, _flows, gaps = consolidate_accounts(per_nav, {})

    assert gaps == ["2026-01-05"]
    assert [o.date for o in nav] == ["2026-01-02", "2026-01-06"]
    # never silently short by the missing account's NAV
    assert {o.date: o.nav for o in nav}["2026-01-06"] == pytest.approx(153_000)


# ---------------------------------------------------------------------------
# 13-14. Missing NAV
# ---------------------------------------------------------------------------

def test_missing_nav_is_unavailable_with_a_warning():
    payload = payload_of({})
    assert payload["status"] == "unavailable"
    assert payload["series"] == []
    assert [w["code"] for w in payload["warnings"]] == ["NAV_UNAVAILABLE"]


def test_single_nav_row_is_insufficient_data():
    payload = payload_of({"2026-01-02": 100_000})
    assert payload["status"] == "insufficient_data"
    assert payload["counts"]["n_subperiods"] == 0
    assert payload["warnings"] != []


def test_failed_flows_never_publish_a_return():
    payload = build_payload(
        observations({"2026-01-02": 100_000, "2026-01-05": 101_000}),
        FlowSet.failed("flex timeout"),
    )
    assert payload["status"] == "degraded"
    assert payload["flows_status"] == "failed"
    assert payload["twr"] is None
    assert "FLOWS_FETCH_FAILED" in [w["code"] for w in payload["warnings"]]


# ---------------------------------------------------------------------------
# 15-16. Fees and interest are internal, not investor capital
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_type",
    [
        "Other Fees",
        "Broker Interest Paid",
        "Broker Interest Received",
        "Withholding Tax",
        "Payment In Lieu Of Dividends",
        "Advisor Fees",
    ],
)
def test_fees_and_interest_are_internal(raw_type):
    assert classify_flow_type(raw_type) is FlowClass.INTERNAL
    assert is_external_flow_type(raw_type) is False


def test_borrow_fee_stays_in_the_return_series():
    nav = {"2026-01-02": 100_000, "2026-01-05": 99_900}
    assert chain_of(nav).returns[0] == pytest.approx(-0.001)
    # Counting the fee as an external flow would erase the loss.
    assert chain_of(nav, {"2026-01-05": -100}).returns[0] == pytest.approx(0.0)


def test_unrecognized_flow_type_raises_rather_than_guessing():
    for raw in ("Deposit Advance Reversal", "Sharebuilder"):
        with pytest.raises(UnknownFlowType):
            classify_flow_type(raw)


# ---------------------------------------------------------------------------
# 17. Risk-free fallback is labelled at the source
# ---------------------------------------------------------------------------

def test_risk_free_fallback_is_labelled():
    nav = _weekday_nav(70)

    fallback = payload_of(nav, risk_free_rate=0.0, risk_free_source="fallback_zero")
    assert fallback["methodology"]["risk_free_rate"] == 0.0
    assert fallback["methodology"]["risk_free_source"] == "fallback_zero"
    assert fallback["risk"]["sharpe_ratio"]["value"] is not None

    fred = payload_of(nav, risk_free_rate=0.0525, risk_free_source="fred_dgs3mo")
    assert fred["methodology"]["risk_free_source"] == "fred_dgs3mo"


# ---------------------------------------------------------------------------
# 18. subperiod_return edge cases (v1 `twr_return`)
# ---------------------------------------------------------------------------

def test_subperiod_return_edge_cases():
    assert subperiod_return(100_000, 110_000, 0) == pytest.approx(0.10)
    assert subperiod_return(100_000, 150_000, 50_000) == pytest.approx(0.0)
    # A zero base has no meaningful return; v1 answered 0.0, which is a lie.
    assert subperiod_return(0, 100_000, 0) is None
    assert subperiod_return(-5_000, 1_000, 0) is None
    assert subperiod_return(float("nan"), 1_000, 0) is None

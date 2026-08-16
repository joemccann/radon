"""Flow classification, Flex parsing, and the data-integrity gates — spec §B, §C, §E.2, §E.8.

RED SUITE. `scripts.lib.flex_flows`, `scripts.lib.twr_math` and the rewritten
`scripts.perf_twr_builder` do not exist yet; an ImportError is the expected
first failure.

No network, no Turso, no Flex: every outbound call is monkeypatched and every
disk read is pointed at `tmp_path`.

Assumed builder surface (spec §C.5). `build_payload` takes an already-resolved
NAV series and an already-resolved `FlowSet`; resolution (Flex / disk / Turso)
is the caller's job, which is what makes the integrity gates testable without
I/O::

    build_payload(
        nav: Sequence[NavObservation],
        flows: FlowSet,
        *,
        benchmark: Mapping[str, float] | None = None,
        benchmark_symbol: str = "SPY",
        risk_free_rate: float = 0.0,
        risk_free_source: str = "fallback_zero",
        nav_source: str = "flex_live",
        nav_sessions_behind: int = 0,
        account_id: str = "ALL",
        allow_inferred_flows: bool = False,
    ) -> dict
"""

from __future__ import annotations

import ast
import json
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scripts.lib.flex_flows import (
    EXTERNAL_FLOW_TYPES,
    INTERNAL_FLOW_TYPES,
    classify_flow_type,
    is_external_flow_type,
    parse_flows,
)
from scripts.lib.twr_math import (
    FlowClass,
    FlowSet,
    FlowsStatus,
    NavObservation,
    UnknownFlowType,
)

import scripts.perf_twr_builder as builder
from scripts.perf_twr_builder import build_payload, get_external_flows_for_nav, load_nav_from_disk

from fixtures import twr_scenarios as fx  # noqa: E402  (tests/ dir, inserted above)

REL = 1e-9
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def observations(nav_map):
    return tuple(NavObservation(date=d, nav=float(v)) for d, v in sorted(nav_map.items()))


def ok_flows(mapping=None):
    if mapping:
        return FlowSet(
            status=FlowsStatus.OK,
            by_date=dict(mapping),
            source="flex_cash_transactions+transfers",
        )
    return FlowSet.empty_verified()


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


# ===========================================================================
# E.2 — flow classification
# ===========================================================================


@pytest.mark.parametrize("raw", fx.LIVE_EXTERNAL_TYPES)
def test_23_24_external_types_classify_external(raw):
    assert classify_flow_type(raw) is FlowClass.EXTERNAL


@pytest.mark.parametrize("raw", fx.LIVE_INTERNAL_TYPES)
def test_25_internal_types_classify_internal(raw):
    assert classify_flow_type(raw) is FlowClass.INTERNAL


@pytest.mark.parametrize("raw", fx.SUBSTRING_TRAP_TYPES)
def test_26_substring_traps_raise_instead_of_failing_open(raw):
    # Regression pin for defect #2: the retired matcher only asked whether
    # "deposit" or "withdrawal" appeared anywhere in the string.
    assert "deposit" in raw.lower() or "withdrawal" in raw.lower()
    with pytest.raises(UnknownFlowType):
        classify_flow_type(raw)


@pytest.mark.parametrize("raw", fx.UNKNOWN_TYPES)
def test_27_unrecognized_types_raise(raw):
    with pytest.raises(UnknownFlowType):
        classify_flow_type(raw)


def test_28_is_external_flow_type_is_derived_from_the_classifier():
    for raw in sorted(EXTERNAL_FLOW_TYPES | INTERNAL_FLOW_TYPES):
        assert is_external_flow_type(raw) == (classify_flow_type(raw) is FlowClass.EXTERNAL)


def test_28b_allowlists_are_disjoint_and_normalized():
    assert EXTERNAL_FLOW_TYPES & INTERNAL_FLOW_TYPES == frozenset()
    for raw in EXTERNAL_FLOW_TYPES | INTERNAL_FLOW_TYPES:
        assert raw == " ".join(raw.split()).strip().casefold()


def test_29_and_f7_only_flex_flows_defines_a_flow_type_predicate():
    flex_source = (REPO_ROOT / "scripts" / "lib" / "flex_flows.py").read_text()
    assert flex_source.count("EXTERNAL_FLOW_TYPES = frozenset") == 1

    for rel in ("scripts/perf_twr_builder.py", "scripts/portfolio_performance.py"):
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert "EXTERNAL_FLOW_TYPES" not in assigned, f"{rel} redefines the classifier"
        assert "NON_FLOW_TYPES" not in assigned, f"{rel} redefines the classifier"

        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "is_external_flow_type" not in defined, f"{rel} redefines the predicate"
        assert not any(name.startswith("_is_external") for name in defined), rel


def test_30_flowset_constructors_enforce_the_status_invariant():
    failed = FlowSet.failed("timeout")
    assert failed.status is FlowsStatus.FAILED
    assert dict(failed.by_date) == {}

    empty = FlowSet.empty_verified()
    assert empty.status is FlowsStatus.EMPTY_VERIFIED
    assert dict(empty.by_date) == {}

    with pytest.raises((AssertionError, ValueError)):
        FlowSet(status=FlowsStatus.FAILED, by_date={"2026-01-13": 1.0}, source="x")
    with pytest.raises((AssertionError, ValueError)):
        FlowSet.failed("")


# ===========================================================================
# E.2 / B.2 — Flex XML parsing
# ===========================================================================


def test_31_a_nav_only_statement_is_failed_not_empty_verified():
    result = parse_flows(fx.flex_xml_nav_only())
    assert result.status is FlowsStatus.FAILED
    assert dict(result.by_date) == {}


def test_present_but_empty_flow_section_is_empty_verified():
    result = parse_flows(fx.flex_xml(cash_transactions=(), transfers=()))
    assert result.status is FlowsStatus.EMPTY_VERIFIED


def test_cash_transactions_are_summed_per_date_and_internals_ignored():
    result = parse_flows(
        fx.flex_xml(
            cash_transactions=[
                fx.cash_transaction(type_="Deposits/Withdrawals", amount=80_007.13),
                fx.cash_transaction(type_="Deposits/Withdrawals", amount=-7.13),
                fx.cash_transaction(type_="Other Fees", amount=-12.50),
                fx.cash_transaction(type_="Dividends", amount=240.00),
            ],
            transfers=(),
        )
    )
    assert result.status is FlowsStatus.OK
    # 80007.13 + (-7.13) = 80000.00 ; fees and dividends stay inside the return
    assert result.by_date["2026-01-13"] == pytest.approx(80_000.00, rel=REL)
    assert len(result.by_date) == 1


def test_06_acats_in_uses_position_amount_in_base_not_transfer_price():
    result = parse_flows(fx.flex_xml(cash_transactions=(), transfers=[fx.ACATS_IN]))
    amount = result.by_date["2026-02-06"]

    assert amount == pytest.approx(725_000.00, rel=REL)
    # Anti-pin: transferPrice is 725.00, a PER-SHARE price. Today's parser
    # returns it as the cash amount, which is why the 725k is invisible.
    assert amount != pytest.approx(725.00, rel=1e-6)
    # quantity * transferPrice = 1000 * 725.00 = 725000.00 is coincidentally
    # right here, but must not be the code path.
    assert result.status is FlowsStatus.OK


def test_07_acats_out_is_negative():
    result = parse_flows(fx.flex_xml(cash_transactions=(), transfers=[fx.ACATS_OUT]))
    assert result.by_date["2026-02-06"] == pytest.approx(-725_000.00, rel=REL)


def test_08_transfer_without_direction_raises():
    with pytest.raises(UnknownFlowType):
        parse_flows(fx.flex_xml(cash_transactions=(), transfers=[fx.ACATS_NO_DIRECTION]))


def test_cash_category_transfer_reads_cash_transfer():
    result = parse_flows(fx.flex_xml(cash_transactions=(), transfers=[fx.CASH_TRANSFER_IN]))
    assert result.by_date["2026-02-06"] == pytest.approx(42_000.00, rel=REL)


def test_transfer_with_no_usable_amount_raises():
    with pytest.raises(UnknownFlowType):
        parse_flows(fx.flex_xml(cash_transactions=(), transfers=[fx.TRANSFER_NO_AMOUNT]))


def test_cash_transactions_and_transfers_net_on_the_same_date():
    result = parse_flows(
        fx.flex_xml(
            cash_transactions=[
                fx.cash_transaction(
                    type_="Deposits/Withdrawals", amount=-25_000.00, report_date="2026-02-06"
                )
            ],
            transfers=[fx.ACATS_IN],
        )
    )
    # 725000.00 + (-25000.00) = 700000.00
    assert result.by_date["2026-02-06"] == pytest.approx(700_000.00, rel=REL)


# ===========================================================================
# B.6 / C.2 Gate 1 — a failed flow fetch is not "no deposits"
# ===========================================================================


def test_77_flex_flow_fetch_failure_is_never_silently_empty(monkeypatch):
    monkeypatch.setenv("IB_FLEX_TOKEN", "tok")
    monkeypatch.setenv("IB_FLEX_FLOWS_QUERY_ID", "1422766")

    def _boom(*_a, **_kw):
        raise TimeoutError("Flex GetStatement timeout")

    monkeypatch.setattr(builder, "fetch_flex_xml", _boom)

    result = get_external_flows_for_nav()
    assert result.status is FlowsStatus.FAILED
    assert dict(result.by_date) == {}


def test_77b_failed_flows_degrade_the_payload_and_suppress_twr():
    payload = build_payload(observations(fx.golden_nav()), FlowSet.failed("timeout"))

    assert payload["status"] == "degraded"
    assert payload["flows_status"] == "failed"
    assert payload["twr"] is None
    assert payload["series"] == []
    # The live payload violated exactly this: status "ok" with warnings [].
    assert payload["warnings"] != []
    assert "FLOWS_FETCH_FAILED" in codes(payload)
    assert warning_with(payload, "FLOWS_FETCH_FAILED")["severity"] == "error"


def test_f3_no_input_yields_ok_with_failed_flows():
    """Property pin over the status resolver: ok and failed are mutually exclusive."""
    rng = random.Random(20260815)
    for _ in range(50):
        n = rng.randint(2, 12)
        nav = observations(fx.nav_from_returns(fx.boundary_returns(n)))
        payload = build_payload(
            nav,
            FlowSet.failed("timeout"),
            nav_source=rng.choice(["flex_live", "disk_cache", "turso"]),
            nav_sessions_behind=rng.randint(0, 40),
        )
        assert payload["status"] != "ok"
        assert payload["flows_status"] == "failed"


def test_empty_verified_flows_publish_normally_and_are_labelled():
    payload = build_payload(observations(fx.golden_nav_no_flows()), FlowSet.empty_verified())

    assert payload["status"] == "ok"
    assert payload["flows_status"] == "empty_verified"
    assert payload["twr"]["cum_return"] is not None


def test_unknown_flow_type_degrades_the_payload():
    payload = build_payload(
        observations(fx.golden_nav()), FlowSet.failed("unknown flow type: Sharebuilder")
    )
    assert payload["status"] == "degraded"
    assert payload["twr"] is None


# ===========================================================================
# C.2 Gate 2 — NAV freshness
# ===========================================================================


def _write_nav_cache(tmp_path, newest: date, count: int = 5) -> Path:
    rows = [
        {"date": (newest - timedelta(days=count - 1 - i)).isoformat(), "total": 100_000.0 + i}
        for i in range(count)
    ]
    path = tmp_path / "nav_history_ib.json"
    path.write_text(json.dumps(rows))
    return path


def test_76_a_disk_cache_older_than_thirty_days_is_refused(tmp_path, monkeypatch):
    # The live defect: a 2026-03-20 series served on 2026-08-15 (148 days).
    stale_newest = date.today() - timedelta(days=148)
    monkeypatch.setattr(builder, "_NAV_CACHE_PATH", _write_nav_cache(tmp_path, stale_newest))
    monkeypatch.setattr(builder, "_IB_NAV_CACHE_DIR", tmp_path / "absent")

    assert load_nav_from_disk() is None


def test_76b_a_fresh_disk_cache_is_still_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(
        builder, "_NAV_CACHE_PATH", _write_nav_cache(tmp_path, date.today() - timedelta(days=3))
    )
    monkeypatch.setattr(builder, "_IB_NAV_CACHE_DIR", tmp_path / "absent")

    loaded = load_nav_from_disk()
    assert loaded is not None
    assert len(loaded) == 5


def test_76c_the_thirty_day_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "_IB_NAV_CACHE_DIR", tmp_path / "absent")

    monkeypatch.setattr(
        builder, "_NAV_CACHE_PATH", _write_nav_cache(tmp_path, date.today() - timedelta(days=30))
    )
    assert load_nav_from_disk() is not None

    monkeypatch.setattr(
        builder, "_NAV_CACHE_PATH", _write_nav_cache(tmp_path, date.today() - timedelta(days=31))
    )
    assert load_nav_from_disk() is None


@pytest.mark.parametrize(
    "nav_source,sessions_behind,expected_status,expected_codes",
    [
        ("flex_live", 0, "ok", []),
        ("flex_live", 2, "ok", []),
        ("flex_live", 3, "stale", ["NAV_STALE"]),
        ("disk_cache", 1, "stale", ["NAV_SOURCE_DISK"]),
        ("disk_cache", 9, "degraded", ["NAV_SOURCE_DISK", "NAV_STALE"]),
        ("turso", 1, "stale", ["NAV_SOURCE_TURSO"]),
        ("turso", 9, "degraded", ["NAV_SOURCE_TURSO", "NAV_STALE"]),
    ],
)
def test_f4_nav_freshness_matrix(nav_source, sessions_behind, expected_status, expected_codes):
    payload = build_payload(
        observations(fx.golden_nav()),
        ok_flows(fx.GOLDEN_FLOWS),
        nav_source=nav_source,
        nav_sessions_behind=sessions_behind,
    )
    assert payload["status"] == expected_status
    assert payload["nav_source"] == nav_source
    assert payload["nav_sessions_behind"] == sessions_behind
    assert payload["nav_as_of"] == "2026-03-27"
    for code in expected_codes:
        assert code in codes(payload)


def test_f4_the_live_combination_is_unreproducible():
    """nav_source disk_cache + 105 sessions behind + status ok + warnings []."""
    payload = build_payload(
        observations(fx.golden_nav()),
        ok_flows(fx.GOLDEN_FLOWS),
        nav_source="disk_cache",
        nav_sessions_behind=105,
    )
    assert payload["status"] == "degraded"
    assert payload["warnings"] != []


# ===========================================================================
# C.1 — status precedence
# ===========================================================================


def test_14_no_nav_series_is_unavailable():
    payload = build_payload((), ok_flows())
    assert payload["status"] == "unavailable"
    assert "NAV_UNAVAILABLE" in codes(payload)
    assert payload["twr"] is None
    assert payload["series"] == []


def test_13_a_single_observation_is_insufficient_data():
    payload = build_payload(observations(fx.single_observation()), ok_flows())
    assert payload["status"] == "insufficient_data"
    assert payload["counts"]["n_nav_observations"] == 1
    assert payload["counts"]["n_subperiods"] == 0
    assert payload["warnings"] != []


def test_12_all_subperiods_skipped_is_insufficient_data():
    payload = build_payload(observations(fx.negative_nav()), ok_flows())
    assert payload["status"] == "insufficient_data"
    assert payload["counts"]["n_returns"] == 0
    assert payload["counts"]["n_skipped"] == 1


def test_17_duplicate_nav_dates_surface_as_a_degraded_payload():
    dupes = (
        NavObservation(date="2026-01-13", nav=185_755.43),
        NavObservation(date="2026-01-13", nav=106_680.59),
        NavObservation(date="2026-01-14", nav=186_000.00),
    )
    payload = build_payload(dupes, ok_flows())
    assert payload["status"] == "degraded"
    assert "NAV_DUPLICATE_DATE" in codes(payload)


def test_c1_degraded_outranks_stale():
    payload = build_payload(
        observations(fx.feb06_nav()),
        ok_flows(),  # no flow for the 294% jump -> suspect -> degraded
        nav_source="disk_cache",
        nav_sessions_behind=1,  # would be `stale` on its own
    )
    assert payload["status"] == "degraded"


# ===========================================================================
# B.4 — suspect quarantine and the inferred-flow diagnostic
# ===========================================================================


def test_19_suspect_subperiod_degrades_and_emits_an_inferred_candidate():
    payload = build_payload(observations(fx.feb06_nav()), ok_flows())

    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    assert warning_with(payload, "SUBPERIOD_SUSPECT")["severity"] == "error"
    assert payload["counts"]["n_suspect"] == 1

    sp = subperiod_on(payload, fx.FEB06_DATE)
    assert sp["skip_reason"] == "suspect_no_flow"
    assert sp["r"] is None

    inferred = warning_with(payload, "INFERRED_FLOW_CANDIDATE")
    # 972215.53 - 246713.50 = 725502.03
    assert inferred["context"]["amount"] == pytest.approx(725_502.03, rel=REL)
    assert inferred["context"]["date"] == fx.FEB06_DATE
    # An inference is never applied without the explicit flag.
    assert payload["methodology"]["inferred_flows"] == []


def test_inferred_flows_require_the_explicit_flag_and_force_degraded():
    payload = build_payload(observations(fx.feb06_nav()), ok_flows(), allow_inferred_flows=True)
    assert payload["status"] == "degraded"
    assert payload["methodology"]["inferred_flows"] != []


def test_20_a_flow_dominant_but_explained_session_stays_ok():
    payload = build_payload(
        observations(fx.feb06_nav()), ok_flows({fx.FEB06_DATE: fx.FEB06_FLOW})
    )
    sp = subperiod_on(payload, fx.FEB06_DATE)

    assert payload["status"] == "ok"
    assert "flow_dominant" in sp["flags"]
    # residual    = 972215.53 - 725000.00 - 246713.50 =    502.03
    # denominator = 246713.50 + 725000.00             = 971713.50
    #   502.03 / 971713.50 = 0.0005166440519762543
    assert sp["r"] == pytest.approx(0.0005166440519762543, rel=REL)
    # Superseded EOD figure: 502.03 / 246713.50 = 0.0020348704063621486
    assert sp["r"] != pytest.approx(0.0020348704063621486, rel=1e-6)
    assert "SUBPERIOD_SUSPECT" not in codes(payload)


def test_16_a_holiday_gap_is_an_info_warning_only():
    payload = build_payload(observations(fx.HOLIDAY_GAP_NAV), ok_flows())
    assert "NAV_GAP" in codes(payload)
    assert warning_with(payload, "NAV_GAP")["severity"] == "info"
    assert payload["status"] == "ok"


def test_21_a_shifted_flow_date_is_reported():
    payload = build_payload(observations(fx.SATURDAY_FLOW_NAV), ok_flows(fx.SATURDAY_FLOW))
    shifted = warning_with(payload, "FLOW_DATE_SHIFTED")

    assert shifted["severity"] == "info"
    assert shifted["context"]["flow_date"] == "2026-01-10"
    assert shifted["context"]["applied_date"] == "2026-01-12"
    assert subperiod_on(payload, "2026-01-12")["c"] == pytest.approx(50_000.0, rel=REL)


def test_a_flow_after_period_end_is_dropped_with_a_warning():
    nav = fx.WEEKEND_GAP_NAV
    payload = build_payload(observations(nav), ok_flows({"2026-01-20": 5_000.0}))
    assert "FLOW_AFTER_PERIOD_END" in codes(payload)
    assert payload["equity"]["net_external_flows"] == pytest.approx(0.0, abs=1e-9)


# ===========================================================================
# C.3 — plausibility gates
# ===========================================================================


def test_c3_implausible_annualized_is_suppressed_and_degrades():
    """A 400-day window with a contaminated cum return clears the calendar gate."""
    returns = [0.05] * 400  # 1.05 ** 400 - 1 is astronomically large
    payload = build_payload(observations(fx.nav_from_returns(returns)), ok_flows())

    assert payload["status"] == "degraded"
    assert "IMPLAUSIBLE_ANNUALIZED" in codes(payload)
    assert warning_with(payload, "IMPLAUSIBLE_ANNUALIZED")["severity"] == "error"
    assert payload["twr"]["annualized"]["value"] is None
    assert payload["twr"]["annualized"]["unavailable_reason"] == "implausible"


def test_58_implausible_alpha_suppresses_the_benchmark_only():
    # Portfolio alternates +0.08 / +0.09 per session; the benchmark alternates
    # +0.0001 / +0.0002 in the SAME phase, so the pair is perfectly correlated:
    #   dev_r = +/- 0.005 ; dev_b = +/- 0.00005 -> beta = 0.005 / 0.00005 = 100
    #   mean_r = 0.085 ; mean_b = 0.00015
    #   alpha  = (0.085 - 100 * 0.00015) * 252 = 0.070 * 252 = 17.64
    # 1764%/yr is >> IMPLAUSIBLE_ALPHA (10.0) — the same shape as the live
    # ALPHA +2190.09% rendered off an unusable SPY series. The threshold moved
    # 1.0 -> 10.0 because 1.0 was a skill ceiling, not a corruption detector:
    # the operator's own book earned a real +124.56% annualized alpha and was
    # being told it was a data defect. 1764% and 2190% still clear 10.0.
    port_returns = [0.08 if i % 2 == 0 else 0.09 for i in range(60)]
    bench_returns = [0.0001 if i % 2 == 0 else 0.0002 for i in range(60)]

    nav = fx.nav_from_returns(port_returns)
    dates = sorted(nav)
    close = 500.0
    bench_closes = {dates[0]: close}
    for i, d in enumerate(dates[1:]):
        close *= 1.0 + bench_returns[i]
        bench_closes[d] = close

    payload = build_payload(observations(nav), ok_flows(), benchmark=bench_closes)

    # The benchmark block is SUPPRESSED, not published with a nonsense alpha.
    assert payload["benchmark"] is None
    assert "IMPLAUSIBLE_ALPHA" in codes(payload)
    # 17.64 is reported in the warning context so the operator can see what was
    # rejected, without it reaching a card.
    assert warning_with(payload, "IMPLAUSIBLE_ALPHA")["context"]["alpha"] == pytest.approx(
        17.64, rel=1e-3
    )

    # Severity "info", matching BENCHMARK_UNAVAILABLE: the same outcome, so the
    # same disclosure level. An "error" here floored the payload to degraded and
    # took the benchmark-INDEPENDENT metrics down with it.
    assert warning_with(payload, "IMPLAUSIBLE_ALPHA")["severity"] == "info"
    assert payload["status"] == "ok"

    # The whole point: a bad regression against SPY must not invalidate a return
    # computed from NAV and flows alone.
    assert payload["twr"] is not None
    assert payload["twr"]["cum_return"] is not None
    assert payload["risk"]["max_drawdown"]["value"] is not None


# ===========================================================================
# C.2 Gate 3 / F5 — benchmark coherence
# ===========================================================================


def test_83_no_benchmark_means_no_benchmark_derived_key_anywhere():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS), benchmark=None)
    blob = json.dumps(payload)

    assert payload["benchmark"] is None
    for key in (
        "beta",
        "alpha_annualized",
        "tracking_error",
        "information_ratio",
        "r_squared",
        "benchmark_return",
        "benchmark_close",
    ):
        assert f'"{key}"' not in blob, f"{key} leaked into a benchmark-less payload"


def test_57_structural_impossibility_over_randomized_payloads():
    rng = random.Random(4886)
    forbidden = (
        "beta",
        "alpha_annualized",
        "tracking_error",
        "information_ratio",
        "correlation",
        "r_squared",
        "benchmark_return",
        "benchmark_close",
    )
    for _ in range(200):
        n = rng.randint(2, 80)
        nav = observations(fx.nav_from_returns(fx.boundary_returns(n)))
        dates = [o.date for o in nav]
        mode = rng.choice(["none", "short", "flat", "full"])
        if mode == "none":
            bench = None
        elif mode == "short":
            bench = {d: 500.0 + i for i, d in enumerate(dates[: max(1, n // 3)])}
        elif mode == "flat":
            bench = dict.fromkeys(dates, 500.0)
        else:
            bench = {d: 500.0 * (1.0 + 0.001 * ((i % 5) - 2)) ** i for i, d in enumerate(dates)}

        payload = build_payload(nav, ok_flows(), benchmark=bench)
        blob = json.dumps(payload)
        if payload["benchmark"] is None:
            for key in forbidden:
                assert f'"{key}"' not in blob
        else:
            assert payload["benchmark"]["benchmark_return"] is not None
            aligned = payload["benchmark"]["n_common"]
            populated = [s for s in payload["series"] if s.get("benchmark_close") is not None]
            assert len(populated) >= aligned


# ===========================================================================
# D — MWR reporting honesty
# ===========================================================================


def test_f6_golden_mwr_period_return_is_a_number():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    # 7217.35574011656 / 143750 = 0.05020769210515868  (see test_golden_modified_dietz)
    assert payload["mwr"]["period_return"]["value"] == pytest.approx(
        0.05020769210515868, rel=1e-12
    )
    assert payload["mwr"]["annualized"]["value"] is None
    assert payload["mwr"]["annualized"]["unavailable_reason"] == "period_lt_1y"
    assert payload["mwr"]["multiple_sign_changes"] is False


def test_68_mwr_n_gate_19_then_20():
    below = build_payload(observations(fx.nav_from_returns(fx.boundary_returns(19))), ok_flows())
    assert below["mwr"]["period_return"]["value"] is None
    assert below["mwr"]["period_return"]["unavailable_reason"] == "insufficient_n"
    assert below["mwr"]["period_return"]["min_n"] == 20
    assert below["mwr"]["period_return"]["n"] == 19

    at = build_payload(observations(fx.nav_from_returns(fx.boundary_returns(20))), ok_flows())
    assert at["mwr"]["period_return"]["value"] is not None
    assert at["mwr"]["period_return"]["unavailable_reason"] is None


def test_64_and_82_failed_flows_never_blame_the_sample_size():
    """The live card read 'MWR IRR — needs 20 sessions (N=57)' for a metric
    that was simply never computed. A null MWR must name its real cause."""
    payload = build_payload(
        observations(fx.nav_from_returns(fx.boundary_returns(57))), FlowSet.failed("timeout")
    )
    for node in (payload["mwr"]["period_return"], payload["mwr"]["annualized"]):
        assert node["value"] is None
        assert node["unavailable_reason"] == "no_flow_data"
        assert node["unavailable_reason"] != "insufficient_n"
        assert node["n"] >= 20  # the sample is fine; the flows are not


# ===========================================================================
# C.5 — payload contract v2
# ===========================================================================

V2_TOP_LEVEL_KEYS = {
    "schema_version",
    "status",
    "generated_at",
    "account_id",
    "methodology",
    "nav_source",
    "nav_as_of",
    "nav_sessions_behind",
    "flows_status",
    "flows_source",
    "period_start",
    "period_end",
    "calendar_days",
    "counts",
    "twr",
    "mwr",
    "risk",
    "drawdown_detail",
    "distribution",
    "benchmark",
    "equity",
    "series",
    "subperiods",
    "warnings",
}

ISO_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# §C.5: "Both the `ok` and the degraded branches emit the same keys; only
# values change." Comparing only `set(payload)` left every nested block free to
# collapse — which is how a degraded payload shipped `equity: {}` (tests-D3).
V2_NESTED_BLOCKS = (
    "methodology",
    "counts",
    "mwr",
    "risk",
    "drawdown_detail",
    "distribution",
    "equity",
)


def nested_key_sets(payload):
    return {block: set(payload[block]) for block in V2_NESTED_BLOCKS}


@pytest.mark.parametrize(
    "nav,flows,kwargs",
    [
        (fx.golden_nav(), "ok", {}),
        (fx.golden_nav(), "failed", {}),
        (fx.single_observation(), "ok", {}),
        ({}, "ok", {}),
        (fx.feb06_nav(), "ok", {"nav_source": "disk_cache", "nav_sessions_behind": 40}),
    ],
)
def test_every_branch_emits_the_same_v2_keys(nav, flows, kwargs):
    flow_set = FlowSet.failed("timeout") if flows == "failed" else ok_flows(fx.GOLDEN_FLOWS)
    payload = build_payload(observations(nav), flow_set, **kwargs)
    reference = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))

    assert set(payload) == V2_TOP_LEVEL_KEYS
    assert nested_key_sets(payload) == nested_key_sets(reference)
    assert payload["schema_version"] == 2
    assert isinstance(payload["warnings"], list)
    assert ISO_INSTANT.match(payload["generated_at"]), payload["generated_at"]
    json.dumps(payload)  # must be serializable as-is


def test_87_generated_at_is_a_full_instant_not_a_date():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    # A date-only taken_at makes every row permanently look stale to the UI's
    # freshness check, and loses the lexicographic race against ISO rows.
    assert len(payload["generated_at"]) > 10
    assert payload["generated_at"].endswith("Z")


def test_methodology_block_declares_the_conventions():
    payload = build_payload(
        observations(fx.golden_nav()),
        ok_flows(fx.GOLDEN_FLOWS),
        risk_free_rate=0.02,
        risk_free_source="fred_dgs3mo",
    )
    m = payload["methodology"]
    assert m["curve_type"] == "twr_daily_bod"
    assert m["return_basis"] == "time_weighted"
    # `curve_type` describes the NAV marks (one per session, end of day);
    # `flow_convention` describes the denominator, which is B + C.
    assert m["flow_convention"] == "bod"
    assert m["day_count"] == "act/365"
    assert m["vol_scaling_days"] == 252
    assert m["sortino_target"] == 0.0
    assert m["risk_free_rate"] == pytest.approx(0.02, rel=REL)
    assert m["risk_free_source"] == "fred_dgs3mo"


def test_fallback_risk_free_source_is_reachable():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    assert payload["methodology"]["risk_free_source"] == "fallback_zero"
    assert payload["methodology"]["risk_free_rate"] == 0.0


def test_equity_pnl_subtracts_external_flows():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    eq = payload["equity"]

    assert eq["starting"] == pytest.approx(100_000.00, rel=1e-12)
    assert eq["ending"] == pytest.approx(182_217.35574011656, rel=1e-12)
    # +100000 - 25000 = +75000
    assert eq["net_external_flows"] == pytest.approx(75_000.00, rel=1e-12)
    # 182217.35574011656 - 100000 - 75000 = 7217.35574011656
    # The retired `pnl = ending - starting` would have published 82217.36.
    assert eq["investment_pnl"] == pytest.approx(7_217.35574011656, rel=1e-12)
    assert eq["investment_pnl"] != pytest.approx(82_217.35574011656, rel=1e-6)


def test_series_carries_a_twr_index_and_a_drawdown_from_that_index():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    series = payload["series"]

    assert len(series) == 61
    assert series[0]["twr_index"] == pytest.approx(100.0, rel=1e-12)
    assert series[0]["daily_return"] is None  # the seed observation has no return
    assert series[0]["cum_return"] == 0.0
    assert series[0]["drawdown"] == 0.0
    # 100 * (1 + 0.05092267338835921), the golden chain derived in
    # test_twr_math.py::test_golden_chain_counts_and_cumulative_return
    assert series[-1]["twr_index"] == pytest.approx(105.09226733883592, rel=1e-9)
    assert series[-1]["nav"] == pytest.approx(182_217.35574011656, rel=1e-12)
    assert all(point["drawdown"] <= 0 for point in series)


def test_counts_block_is_internally_consistent():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    c = payload["counts"]
    assert c["n_nav_observations"] == 61
    assert c["n_subperiods"] == 60
    assert c["n_subperiods"] == c["n_nav_observations"] - 1
    assert c["n_returns"] == 60
    assert c["n_skipped"] == 0
    assert c["n_suspect"] == 0
    assert payload["calendar_days"] == 84
    assert payload["status"] == "ok"
    # audit tests-D1: this used to pin `warnings == []` on a payload whose
    # 2026-02-13 subperiod is flow-dominant (|C|/B = 100000/102577.57 = 0.97),
    # actively holding the missing §C.3 FLOW_DOMINANT warning in place.
    assert codes(payload) == ["FLOW_DOMINANT"]


def test_every_risk_and_distribution_entry_is_a_gated_value():
    payload = build_payload(observations(fx.golden_nav()), ok_flows(fx.GOLDEN_FLOWS))
    for block in ("risk", "distribution"):
        for name, node in payload[block].items():
            assert set(node) >= {"value", "n", "min_n", "unavailable_reason"}, name
            assert (node["value"] is None) == (node["unavailable_reason"] is not None), name


# ===========================================================================
# E.8 — production regression replays
# ===========================================================================


def test_live_fixture_anchors_line_up_with_the_production_dates():
    dates = fx.live_dates()
    assert len(dates) == fx.LIVE_N_OBSERVATIONS == 58
    for idx, expected in fx.LIVE_ANCHOR_DATES.items():
        assert dates[idx] == expected
    nav = fx.interpolated_live_nav()
    assert len(nav) == 58
    for idx, expected in fx.LIVE_NAV_ANCHORS.items():
        assert nav[dates[idx]] == pytest.approx(expected, rel=1e-12)


def test_74_live_series_without_flows_is_degraded_not_a_951_percent_return():
    payload = build_payload(observations(fx.interpolated_live_nav()), ok_flows())

    assert payload["status"] == "degraded"
    assert "SUBPERIOD_SUSPECT" in codes(payload)
    # Three real deposit days, none of them recorded as a flow here:
    #   185755.43 / 106680.59 - 1 = 0.7412298713383569
    #   232497.53 / 189502.12 - 1 = 0.2268861688724116
    #   972215.53 / 246713.50 - 1 = 2.9406660633689196
    # audit tests-D4: the 01-26 session sits under the 0.50 threshold, so it was
    # chained whole and the old `abs(published) < 0.60` passed at 0.5321 with
    # 22.7 points of deposit still inside it.
    # audit round 3 / R4: this runs on the INTERPOLATED series, whose median |r|
    # is 0.00243973 — 13x flatter than the account's real 0.03252648 — so a
    # dispersion bar clears 01-26 here for a reason the production series does
    # not supply. The real-series claim lives in
    # test_perf_twr_residuals.py::test_r4_*; do not read n_suspect == 3 here as
    # a statement about production.
    assert payload["counts"]["n_suspect"] == 3
    for suspect_date in ("2026-01-13", "2026-01-26", fx.FEB06_DATE):
        sp = subperiod_on(payload, suspect_date)
        assert sp["skip_reason"] == "suspect_no_flow", suspect_date
        assert sp["r"] is None, suspect_date

    published = None if payload["twr"] is None else payload["twr"]["cum_return"]
    if published is not None:
        assert published != pytest.approx(fx.LIVE_CONTAMINATED_CUM_RETURN, rel=0.01)
        # Dropping the 01-26 factor from the audit's 1.532119750498659 chain
        # leaves 1.532119750498659 / 1.2268861688724116 - 1 = 0.2487872056678062.
        assert abs(published) < 0.30


def test_75_f1_live_series_with_flows_produces_a_plausible_return():
    payload = build_payload(observations(fx.interpolated_live_nav()), ok_flows(fx.LIVE_FLOWS))

    jan13 = subperiod_on(payload, "2026-01-13")
    feb06 = subperiod_on(payload, fx.FEB06_DATE)

    # residual    = 185755.43 - 80007.13 - 106680.59 =   -932.29
    # denominator = 106680.59 + 80007.13             = 186687.72
    #   -932.29 / 186687.72 = -0.004993847479630734
    assert jan13["r"] == pytest.approx(-0.004993847479630734, rel=REL)
    # residual    = 972215.53 - 725000.00 - 246713.50 =    502.03
    # denominator = 246713.50 + 725000.00             = 971713.50
    #   502.03 / 971713.50 = 0.0005166440519762543
    assert feb06["r"] == pytest.approx(0.0005166440519762543, rel=REL)
    # residual    = 232497.53 - 42000.00 - 189502.12 =    995.41
    # denominator = 189502.12 + 42000.00             = 231502.12
    #   995.41 / 231502.12 = 0.004299787837796058
    jan26 = subperiod_on(payload, "2026-01-26")
    assert jan26["r"] == pytest.approx(0.004299787837796058, rel=REL)

    # With C dropped, these three sessions read as
    #   185755.43/106680.59 = 1.7412327
    #   232497.53/189502.12 = 1.2268891
    #   972215.53/246713.50 = 3.9406661
    # whose product is 8.4184 (+741.84%) — the bulk of the live +951.28%.
    # Correctly flow-adjusted they multiply to roughly 0.9998
    #   (1 - 0.004993847479630734) * (1 + 0.004299787837796058)
    #                             * (1 + 0.0005166440519762543)
    corrected = (1 + jan13["r"]) * (1 + jan26["r"]) * (1 + feb06["r"])
    assert corrected != pytest.approx(8.4184, rel=0.01)
    assert corrected - 1 != pytest.approx(7.4184, rel=0.01)
    assert abs(corrected - 1) < 0.02

    assert payload["counts"]["n_suspect"] == 0
    assert abs(payload["twr"]["cum_return"]) < 0.60
    assert payload["twr"]["cum_return"] != pytest.approx(
        fx.LIVE_CONTAMINATED_CUM_RETURN, rel=0.01
    )


def test_f2_live_window_is_too_short_to_annualize():
    payload = build_payload(observations(fx.interpolated_live_nav()), ok_flows(fx.LIVE_FLOWS))
    # 2025-12-31 -> 2026-03-20 is 79 calendar days
    assert payload["calendar_days"] == 79
    assert payload["twr"]["annualized"]["value"] is None
    assert payload["twr"]["annualized"]["unavailable_reason"] == "period_lt_1y"


def test_f2_any_published_annualization_is_bounded_or_degraded():
    rng = random.Random(951_28)
    for _ in range(50):
        returns = [rng.uniform(-0.03, 0.05) for _ in range(400)]
        payload = build_payload(observations(fx.nav_from_returns(returns)), ok_flows())
        annualized = payload["twr"]["annualized"]["value"] if payload["twr"] else None
        if annualized is not None:
            assert abs(annualized) <= 10.0 or payload["status"] == "degraded"

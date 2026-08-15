"""TWR portfolio performance — Plan §4.5 (20 cases).

Covers: zero flows, deposit/withdrawal, ACATS ±50k, split, dividend,
inception, N gates (3/40/60), multi-account internal transfer,
hard-coded date guard, Flex missing → insufficient_data, holiday,
borrow fee not counted, Rf fallback, golden no-flow TR=0.123456.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

# Builder is under scripts/
from perf_twr_builder import (
    CURVE_TYPE,
    RETURN_BASIS,
    TRADING_DAYS,
    build_payload,
    build_twr_subperiods,
    compute_twr_metrics,
    consolidate_accounts,
    is_external_flow_type,
    twr_return,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "perf_nav_tw_no_flows.json"


def _nav(d):  # helper: make single-account nav dict
    return dict(d)


def _flows(d):
    return dict(d)


# ---------------------------------------------------------------------------
# 1. Zero flows — base TWR correctness
# ---------------------------------------------------------------------------

def test_zero_flows_twr():
    nav = _nav({"2026-01-02": 100_000, "2026-01-03": 110_000, "2026-01-06": 121_000})
    flows = {}
    subs = build_twr_subperiods(nav, flows)
    # r1 = 0.10, r2 = 0.10, cum = 1.10*1.10-1 = 0.21
    assert subs[1]["r"] == pytest.approx(0.10)
    assert subs[2]["r"] == pytest.approx(0.10)
    assert subs[2]["cum_r"] == pytest.approx(0.21)
    payload = build_payload(nav, flows, risk_free_rate_override=0.04)
    assert payload["status"] == "ok"
    assert payload["summary"]["total_return"] == pytest.approx(0.21)
    assert payload["methodology"]["curve_type"] == "twr_modified_dietz_daily"
    assert payload["contracts_missing_history"] == []
    assert payload["trades_source"] == "ib_nav"
    assert payload["summary"]["starting_equity"] == pytest.approx(100_000)
    assert payload["summary"]["ending_equity"] == pytest.approx(121_000)
    assert payload["summary"]["pnl"] == pytest.approx(21_000)


def test_golden_no_flows_total_return_0_123456():
    data = json.loads(FIXTURE_PATH.read_text())
    nav = data["nav_snapshots"]
    flows = data["external_flows"]
    payload = build_payload(nav, flows, risk_free_rate_override=0.04)
    assert payload["summary"]["total_return"] == pytest.approx(0.123456, abs=1e-8)
    assert payload["metrics"]["total_return"] == pytest.approx(0.123456, abs=1e-8)


# ---------------------------------------------------------------------------
# 2-3. Deposit / withdrawal excluded
# ---------------------------------------------------------------------------

def test_deposit_excluded_from_return():
    # NAV jumps 50k on deposit day, but TWR removes it
    nav = _nav({"2026-03-14": 200_000, "2026-03-15": 260_000, "2026-03-17": 265_000})
    flows = _flows({"2026-03-15": 50_000})  # deposit
    subs = build_twr_subperiods(nav, flows)
    # r on 03-15 = (260k -200k -50k)/200k = 0.05
    assert subs[1]["r"] == pytest.approx(0.05)
    # r on 03-17 = (265k -260k)/260k
    assert subs[2]["r"] == pytest.approx(5_000 / 260_000)
    payload = build_payload(nav, flows, risk_free_rate_override=0.04)
    # cum should not be inflated by deposit
    expected = (1.05)*(1+5_000/260_000)-1
    assert payload["summary"]["total_return"] == pytest.approx(expected)


def test_withdrawal_excluded_from_return():
    nav = _nav({"2026-06-01": 500_000, "2026-06-02": 380_000, "2026-06-03": 390_000})
    flows = _flows({"2026-06-02": -100_000})  # withdrawal
    subs = build_twr_subperiods(nav, flows)
    # r on withdrawal day = (380k -500k -(-100k))/500k = -20k/500k = -0.04
    assert subs[1]["r"] == pytest.approx(-0.04)
    payload = build_payload(nav, flows, risk_free_rate_override=0.04)
    expected = (1-0.04)*(1+10_000/380_000)-1
    assert payload["summary"]["total_return"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 4-5. ACATS ±50k
# ---------------------------------------------------------------------------

def test_acats_plus_50k_treated_as_external_flow():
    nav = _nav({"2026-04-01": 300_000, "2026-04-02": 360_000})
    flows = _flows({"2026-04-02": 50_000})  # ACATS transfer-in
    assert is_external_flow_type("acats") is True
    subs = build_twr_subperiods(nav, flows)
    assert subs[1]["r"] == pytest.approx((360_000 - 300_000 - 50_000)/300_000)
    assert subs[1]["r"] == pytest.approx(10_000/300_000)


def test_acats_minus_50k_transfer_out():
    nav = _nav({"2026-04-01": 300_000, "2026-04-02": 240_000})
    flows = _flows({"2026-04-02": -50_000})
    subs = build_twr_subperiods(nav, flows)
    assert subs[1]["r"] == pytest.approx((240_000 - 300_000 - (-50_000))/300_000)
    assert subs[1]["r"] == pytest.approx(-10_000/300_000)


# ---------------------------------------------------------------------------
# 6. Split — NAV is split-adjusted, no phantom return
# ---------------------------------------------------------------------------

def test_split_nvda_10to1_no_phantom_return():
    # NVDA 10:1 on 2026-06-07 — IB bars are split-adjusted, so NAV series
    # already reflects adjusted price.  A holding of 100 NVDA at $100 ($10k)
    # becomes 1000 at $10 ($10k).  NAV doesn't jump.
    # Simulate: NAV flat 100k before and after split day, no flow.
    nav = _nav({"2026-06-06": 100_000, "2026-06-09": 100_000, "2026-06-10": 101_000})
    flows = {}
    subs = build_twr_subperiods(nav, flows)
    assert subs[1]["r"] == pytest.approx(0.0)  # split day -> no jump
    assert subs[2]["r"] == pytest.approx(0.01)
    payload = build_payload(nav, flows, risk_free_rate_override=0.04)
    assert payload["summary"]["total_return"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# 7. Dividend is return, not flow
# ---------------------------------------------------------------------------

def test_dividend_is_return_not_flow():
    assert is_external_flow_type("dividend") is False
    assert is_external_flow_type("Dividend") is False
    # NAV includes dividend cash: 100k -> 100k + $240 div = 100240
    nav = _nav({"2026-05-09": 100_000, "2026-05-12": 100_240})
    # No external flow — dividend stays in return
    subs = build_twr_subperiods(nav, {})
    assert subs[1]["r"] == pytest.approx(0.0024)
    # If someone mistakenly passed dividend as flow, it would zero it out
    subs_wrong = build_twr_subperiods(nav, {"2026-05-12": 240})
    assert subs_wrong[1]["r"] == pytest.approx(0.0)  # shows why div must NOT be flow
    # Correct payload excludes dividend from flows
    payload = build_payload(nav, {}, risk_free_rate_override=0.04)
    assert payload["summary"]["total_return"] == pytest.approx(0.0024)


# ---------------------------------------------------------------------------
# 8. Inception 2026-05-01 (not Jan 1)
# ---------------------------------------------------------------------------

def test_inception_is_first_nav_not_jan01():
    nav = _nav({"2026-05-01": 50_000, "2026-05-02": 52_000, "2026-05-05": 51_000})
    payload = build_payload(nav, {}, risk_free_rate_override=0.04)
    assert payload["period_start"] == "2026-05-01"
    assert payload["period_end"] == "2026-05-05"
    assert payload["summary"]["period_start"] == "2026-05-01"
    # total_return chained from 05-01, not fabricated Jan-Apr
    r1 = (52_000-50_000)/50_000
    r2 = (51_000-52_000)/52_000
    expected = (1+r1)*(1+r2)-1
    assert payload["summary"]["total_return"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 9-11. N gates
# ---------------------------------------------------------------------------

def test_three_day_n_gate_annualized_is_null():
    # 4 NAV dates => 3 returns => N=3 <20 => annualized null
    nav = {f"2026-01-0{i}": 100_000 + i*1_000 for i in range(2, 6)}  # 02,03,04,05
    # ensure valid calendar weekdays (use simple sequence, builder doesn't validate weekdays)
    nav = {"2026-01-02": 100_000, "2026-01-03": 101_000, "2026-01-06": 102_000, "2026-01-07": 103_000}
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04)
    assert payload["metrics"]["n"] == 3
    assert payload["metrics"]["annualized_return"] is None
    assert payload["summary"]["annualized_return"] is None


def test_annualized_present_when_n_ge_20():
    # 21 NAV dates => 20 returns => N=20 => annualized present
    base = 100_000
    nav = {}
    for i in range(21):
        # Use 2026-01-02 .. 2026-01-22 etc but just generate sequential fake dates
        day = f"2026-02-{i+1:02d}"
        nav[day] = base * (1.001 ** i)
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04)
    assert payload["metrics"]["n"] == 20
    assert payload["metrics"]["annualized_return"] is not None
    assert payload["summary"]["annualized_return"] is not None


def test_beta_null_when_n_less_than_40():
    nav = {f"2026-03-{i+1:02d}": 100_000 + i*100 for i in range(30)}  # 30 dates => 29 returns <40
    nav = {f"2026-03-{i+1:02d}".replace("2026-03-","2026-03-"): v for i,v in enumerate([100_000 + i*500 for i in range(30)])}
    # Build a proper seq with 30 entries: need N=29 <40
    nav = {}
    for i in range(30):
        nav[f"2026-04-{i+1:02d}"] = 100_000 + i*1000
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04, benchmark_series={k: 5000+i*10 for i,k in enumerate(sorted(nav.keys()))})
    assert payload["metrics"]["n"] < 40
    assert payload["metrics"]["beta"] is None
    assert payload["metrics"]["alpha"] is None


def test_beta_present_when_n_ge_40():
    n_days = 41  # 41 dates => 40 returns => meets gate
    nav = {f"2026-05-{i+1:02d}": 100_000 + i*1000 for i in range(n_days)}
    bench = {k: 5000 + i*50 for i, k in enumerate(sorted(nav.keys()))}
    # Create correlated series so |corr|>0.2
    # Make nav returns track bench roughly
    # Instead of synthetic linear, let builder compute; linear both gives corr ~1
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04, benchmark_series=bench)
    assert payload["metrics"]["n"] >= 40
    # May be None if corr gate fails, but with linear it should be present
    # If bench variance 0 then null; with our linear bench, corr should be ~1
    assert payload["metrics"]["beta"] is not None


def test_var_null_when_n_less_than_60():
    nav = {f"2026-06-{i+1:02d}": 100_000 + (i%3)*500 for i in range(50)}  # 50 dates =>49 <60
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04)
    assert payload["metrics"]["n"] < 60
    assert payload["metrics"]["var_95"] is None
    assert payload["metrics"]["cvar_95"] is None


def test_var_present_when_n_ge_60():
    nav = {f"2026-07-{i+1:02d}": 100_000 + (i%5)*200 for i in range(61)}  # 61 =>60
    payload = build_payload(_nav(nav), {}, risk_free_rate_override=0.04)
    assert payload["metrics"]["n"] >= 60
    assert payload["metrics"]["var_95"] is not None
    assert payload["metrics"]["cvar_95"] is not None


# ---------------------------------------------------------------------------
# 12. Multi-account internal transfer nets to zero in consolidated
# ---------------------------------------------------------------------------

def test_multi_account_internal_transfer_net_zero():
    # U123 withdraws 100k, U456 deposits 100k same day — consolidated C=0
    per_nav = {
        "U123": {"2026-01-02": 200_000, "2026-01-03": 100_000},
        "U456": {"2026-01-02": 0, "2026-01-03": 100_000},
    }
    per_flows = {
        "U123": {"2026-01-03": -100_000},
        "U456": {"2026-01-03": 100_000},
    }
    nav_cons, flow_cons = consolidate_accounts(per_nav, per_flows)
    # Consolidated NAV stays 200k, flow nets 0 => return 0
    assert nav_cons["2026-01-03"] == pytest.approx(200_000)
    assert flow_cons.get("2026-01-03", 0) == pytest.approx(0.0)
    # Also via build_payload multi-account path
    payload = build_payload(per_nav, per_flows, risk_free_rate_override=0.04)
    assert payload["status"] == "ok"
    # With single-consolidated dict, 200k ->200k => 0 return
    assert payload["summary"]["total_return"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 13. Hard-coded 2025-12-31 not present
# ---------------------------------------------------------------------------

def test_hardcoded_2025_12_31_not_present():
    nav = _nav({"2026-01-02": 100_000, "2026-01-03": 101_000})
    payload = build_payload(nav, {}, risk_free_rate_override=0.04)
    dates = [s["date"] for s in payload["series"]]
    assert "2025-12-31" not in dates
    subs_dates = [s["report_date"] for s in payload["twr_subperiods"]]
    assert "2025-12-31" not in subs_dates


# ---------------------------------------------------------------------------
# 14. Flex missing → insufficient_data
# ---------------------------------------------------------------------------

def test_flex_missing_insufficient_data():
    payload = build_payload({}, {}, risk_free_rate_override=0.04)
    assert payload["status"] == "insufficient_data"
    payload2 = build_payload(None, None, risk_free_rate_override=0.04)
    assert payload2["status"] == "insufficient_data"
    assert payload2["contracts_missing_history"] == []
    assert payload2["series"] == []
    # Single NAV row also insufficient
    payload3 = build_payload({"2026-01-02": 100_000}, {}, risk_free_rate_override=0.04)
    assert payload3["status"] == "insufficient_data"
    assert any("insufficient" in w.lower() or "no flex" in w.lower() for w in payload3["warnings"])


# ---------------------------------------------------------------------------
# 15. Holiday calendar — 2026-01-01 not in calendar
# ---------------------------------------------------------------------------

def test_holiday_2026_01_01_not_in_calendar():
    # 2026-01-01 is New Year holiday — no NAV.  Calendar is NAV dates only.
    nav = _nav({"2025-12-31": 100_000, "2026-01-02": 101_000, "2026-01-05": 102_000})
    # If someone incorrectly used SPY calendar, 01-01 would be included.
    # Our builder uses twr_subperiods dates only, so 01-01 absent.
    payload = build_payload(nav, {}, risk_free_rate_override=0.04)
    dates = [s["report_date"] for s in payload["twr_subperiods"]]
    assert "2026-01-01" not in dates
    assert payload["period_start"] == "2025-12-31"
    # Returns are 01-02 and 01-05 only
    assert payload["metrics"]["n"] == 2


# ---------------------------------------------------------------------------
# 16. Borrow fee not counted as flow
# ---------------------------------------------------------------------------

def test_borrow_fee_not_counted_as_flow():
    assert is_external_flow_type("borrow_fee") is False
    assert is_external_flow_type("Borrow Fee") is False
    nav = _nav({"2026-01-02": 100_000, "2026-01-03": 99_900})
    # Borrow fee $100 appears as separate CashTransactions type=Borrow Fee
    # but should NOT be in external_flows.  NAV already includes it as return drift.
    subs_without = build_twr_subperiods(nav, {})
    assert subs_without[1]["r"] == pytest.approx(-0.001)
    subs_with = build_twr_subperiods(nav, {"2026-01-03": -100})
    # If fee were counted, return would be 0 (wrong)
    assert subs_with[1]["r"] == pytest.approx(0.0)
    # Correct behavior: don't pass fee as flow
    payload = build_payload(nav, {}, risk_free_rate_override=0.04)
    assert payload["summary"]["total_return"] == pytest.approx(-0.001)


# ---------------------------------------------------------------------------
# 17. Rf fallback
# ---------------------------------------------------------------------------

def test_rf_fallback_uses_zero_with_warning_when_fred_unavailable():
    nav = _nav({"2026-01-02": 100_000, "2026-01-03": 101_000, "2026-01-06": 102_000})
    # Patch fred client to simulate missing key / network failure
    with patch("perf_twr_builder.get_risk_free_rate", return_value=(0.0, "Rf unavailable — FRED_API_KEY not set or fetch failed; Sharpe = excess over 0")):
        payload = build_payload(nav, {})
        assert payload["methodology"]["risk_free_rate"] == 0.0
        assert any("Rf unavailable" in w for w in payload["warnings"])
        # Sharpe should still be computed as excess over 0 (i.e., not NaN)
        assert payload["metrics"]["sharpe_ratio"] is not None


def test_twr_return_helper_edge_cases():
    assert twr_return(0, 100_000, 0) == 0.0
    assert twr_return(100_000, 110_000, 0) == pytest.approx(0.10)
    assert twr_return(100_000, 150_000, 50_000) == pytest.approx(0.0)

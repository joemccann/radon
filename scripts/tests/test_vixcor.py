"""VIXCOR indicator — 20-session VIX x COR3M rolling correlation tests.

Spec: docs/indicators/vixcor.md.

Ground truth is read from two checked-in fixtures, never from the network
and never from Turso:

  - fixtures/vix_history_sample.csv — the full Cboe VIX_History.csv
    (1990-01-02 .. 2026-08-14, 9,251 data rows) in its genuine
    ``DATE,OPEN,HIGH,LOW,CLOSE`` / ``MM/DD/YYYY`` shape, with two rows
    appended on purpose at the end of the file: one malformed DATE and one
    empty CLOSE. Both must be skipped by the parser.
  - fixtures/cor3m_sample.json — the full ``cor_history`` (date, cor3m)
    dump, 5,186 rows, including the 15 scattered pre-2021 NULL cor3m dates.

Fixture-derived expectations below were derived by inspecting the fixtures
directly (2026-08-15) with a scratch script, NOT by calling the functions
under test and NOT from memory. Hand-computable expectations carry their
arithmetic in a comment.

Two calibration anchors are deliberate, fixture-pinned exceptions to the
window-relative-date rule: the 2026-08-14 correlation value and the five
2024+ breakdown episodes. They are anchored to the checked-in bytes, not to
the live CDN, so they cannot rot.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
VIX_CSV = (FIXTURES / "vix_history_sample.csv").read_text()
COR3M_ROWS: list[dict[str, Any]] = json.loads((FIXTURES / "cor3m_sample.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0049_vixcor.sql"


def _mod():
    """Import the module under test lazily so the red phase fails per-test."""
    import fetch_vixcor  # type: ignore[import-not-found]

    return fetch_vixcor


# The cor3m rows as the ingest job consumes them: nulls dropped, ascending.
COR3M_PARENT_ROWS = [
    {"date": row["date"], "value": row["cor3m"]}
    for row in COR3M_ROWS
    if row["cor3m"] is not None
]


def _cor3m_through(cutoff: str) -> list[dict[str, Any]]:
    """Parent rows truncated at ``cutoff`` inclusive — the parent-lag lever."""
    return [row for row in COR3M_PARENT_ROWS if row["date"] <= cutoff]


def _vix_csv_through(cutoff: str) -> str:
    """The fixture CSV truncated at ``cutoff`` inclusive, header preserved."""
    lines = VIX_CSV.rstrip("\n").split("\n")
    kept = [lines[0]]
    for line in lines[1:]:
        stamp = line.split(",", 1)[0].strip()
        try:
            iso = datetime.strptime(stamp, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        if iso <= cutoff:
            kept.append(line)
    return "\n".join(kept) + "\n"


# ── fixture-derived pins (scratch-derived 2026-08-15) ─────────────

PIN_VIX_ROW_COUNT = 9251
PIN_VIX_FIRST = {"date": "1990-01-02", "value": 17.24}
PIN_VIX_LAST = {"date": "2026-08-14", "value": 14.25}

PIN_JOINED_COUNT = 5171          # cor3m non-null dates that also exist in VIX
PIN_CORR_COUNT = 5152            # == 5171 - (WINDOW - 1)
PIN_FIRST_EMITTABLE = "2006-01-31"
PIN_FIRST_EMITTABLE_CORR = 0.949499096477509
PIN_JOINED_FIRST = {"date": "2006-01-03", "vix_close": 11.14, "cor3m_close": 31.34}

# The operator's chart prints this as 0.01 on 14-Aug-2026 with VIX 14.25.
PIN_CALIBRATION_DATE = "2026-08-14"
PIN_CALIBRATION_CORR = 0.01496925372475627
PIN_CALIBRATION_VIX = 14.25
PIN_CALIBRATION_COR3M = 11.03
PIN_PRIOR_CORR = 0.029761334930446283      # 2026-08-13
# change_1d = corr20(2026-08-14) - corr20(2026-08-13)
#           = 0.01496925372475627 - 0.029761334930446283 = -0.014792081205690012
PIN_CALIBRATION_CHANGE_1D = -0.014792081205690012
PIN_CALIBRATION_PERCENTILE = 0.018633540372670808
PIN_CALIBRATION_VIX_COV_20D = 0.10641289363663008   # sample stddev / mean

# Window sensitivity at the calibration date (B.4): only N=20 inclusive works.
PIN_N19_INCLUSIVE = 0.06745062880341113
PIN_N21_INCLUSIVE = -0.03629951635838527
PIN_N20_EXCLUDING_TODAY = 0.029761334930446283

# Return-convention negative controls over the last 21 joined sessions (B.1).
PIN_PCT_CHANGE_CORR = 0.9204038910141121
PIN_LOG_RETURN_CORR = 0.9219399129964957

# Forward-fill negative control, the worst window since 2024 (B.5).
PIN_FFILL_CONTROL_DATE = "2024-07-11"
PIN_FFILL_CONTROL_INNER_JOIN = 0.5042916591128015
PIN_FFILL_CONTROL_FORWARD_FILLED = 0.7072847947497293

PIN_TOTAL_EPISODES = 31

# B.11 — the operator's five circled events, full precision from the fixtures.
PIN_EPISODES_2024_PLUS = [
    {
        "trigger": "2024-02-22", "corr_at_trigger": 0.2476875968621676,
        "vix_at_trigger": 14.54, "start": "2024-02-16", "end": "2024-03-26",
        "sessions": 27, "trough": -0.07375849579329652,
        "trough_date": "2024-03-20", "open": False,
    },
    {
        "trigger": "2024-06-03", "corr_at_trigger": 0.21858974669112646,
        "vix_at_trigger": 13.11, "start": "2024-06-03", "end": "2024-06-14",
        "sessions": 10, "trough": 0.11841251565327786,
        "trough_date": "2024-06-05", "open": False,
    },
    {
        "trigger": "2025-10-01", "corr_at_trigger": 0.072058107586012,
        "vix_at_trigger": 16.29, "start": "2025-09-30", "end": "2025-10-09",
        "sessions": 8, "trough": 0.019239413405105085,
        "trough_date": "2025-10-02", "open": False,
    },
    {
        "trigger": "2026-05-22", "corr_at_trigger": 0.23461447662579915,
        "vix_at_trigger": 16.7, "start": "2026-05-21", "end": "2026-05-26",
        "sessions": 3, "trough": 0.23461447662579915,
        "trough_date": "2026-05-22", "open": False,
    },
    {
        "trigger": "2026-08-11", "corr_at_trigger": 0.2333891500142998,
        "vix_at_trigger": 15.28, "start": "2026-08-11", "end": "2026-08-14",
        "sessions": 4, "trough": 0.01496925372475627,
        "trough_date": "2026-08-14", "open": True,
    },
]

# Per-episode forward VIX drawup for the 2025-10-01 trigger.
PIN_FORWARD_2025_10_01 = {
    5: 0.05831798649478204,
    10: 0.32965009208103146,
    21: 0.5537139349294045,
    42: 0.6218538980969923,
    63: 0.6218538980969923,
}
# The 2026-05-22 trigger has fewer than 63 forward joined sessions.
PIN_FORWARD_2026_05_22_H63_IS_NONE = True

# B.9 full-history distribution over the 5,152 non-null readings.
PIN_STATS = {
    "min": -0.5324175769932946,
    "max": 0.9931593701592107,
    "mean": 0.7620753472861348,
    "median": 0.8445778983526377,
    "p01": -0.1135352135252202,
    "p05": 0.25860627026564653,
    "p10": 0.45704431225069253,
    "p25": 0.6795349466351676,
    "p75": 0.9250152033656959,
    "p90": 0.9581875756728307,
    "p95": 0.9723001728108077,
    "p99": 0.9857230387031951,
    "share_below_zero": 0.017663043478260868,
    "share_below_trigger": 0.04891304347826087,
}
# Population sd 0.23409487306555923 / sample sd 0.2341175952081088 — the
# spec publishes 0.2341 for both, so this pin is deliberately loose enough
# to accept either convention. The repo convention is population (pstdev).
PIN_STATS_STDDEV = 0.23409487306555923
# The mechanism numbers the UI copy quotes: 6.9% vs 11.8%. Both are the
# SAMPLE coefficient of variation, which is what reproduces the spec.
PIN_VIX_COV_BREAKDOWN = 0.06907717856123359
PIN_VIX_COV_COUPLED = 0.11797279611537437

# B.7 forward statistics: the event aggregate and the null it is shown against.
PIN_FORWARD_EVENT = {
    5: {"n": 30, "mean_drawup": 0.03920647298479089, "median_drawup": 0.037724533607575106,
        "p_higher": 0.4, "p_drawup_20": 0.06666666666666667},
    10: {"n": 30, "mean_drawup": 0.07995864263012523, "median_drawup": 0.07646834029890826,
         "p_higher": 0.4666666666666667, "p_drawup_20": 0.16666666666666666},
    21: {"n": 30, "mean_drawup": 0.20888022516902216, "median_drawup": 0.14484303418466693,
         "p_higher": 0.5666666666666667, "p_drawup_20": 0.36666666666666664},
    42: {"n": 30, "mean_drawup": 0.3301047625491723, "median_drawup": 0.31786580800443087,
         "p_higher": 0.6, "p_drawup_20": 0.6333333333333333},
    63: {"n": 29, "mean_drawup": 0.439223571045596, "median_drawup": 0.3311092577147623,
         "p_higher": 0.3793103448275862, "p_drawup_20": 0.6896551724137931},
}
PIN_FORWARD_BASE = {
    5: {"n": 5147, "mean_drawup": 0.0895626000701214, "median_drawup": 0.05456774984671986,
        "p_higher": 0.46221099669710514, "p_drawup_20": 0.15407033223236838},
    10: {"n": 5142, "mean_drawup": 0.1582332659843353, "median_drawup": 0.1009258730883359,
         "p_higher": 0.4618825359782186, "p_drawup_20": 0.28160248930377285},
    21: {"n": 5131, "mean_drawup": 0.2736448802236806, "median_drawup": 0.17860465116279078,
         "p_higher": 0.4580003897875658, "p_drawup_20": 0.4591697524848957},
    42: {"n": 5110, "mean_drawup": 0.44010489348883564, "median_drawup": 0.2930825885883874,
         "p_higher": 0.4461839530332681, "p_drawup_20": 0.6379647749510763},
    63: {"n": 5089, "mean_drawup": 0.572664526517183, "median_drawup": 0.39388646288209617,
         "p_higher": 0.4431125957948516, "p_drawup_20": 0.7254863430929456},
}

# Dates the join must drop. All five are Cboe index holidays present in the
# VIX file and absent from cor3m; 2020-11-17 is the latest of the 15 NULLs.
PIN_VIX_ONLY_2026 = ["2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03"]
PIN_COR3M_NULL_DATES = [
    "2006-05-18", "2006-11-20", "2007-04-02", "2007-06-13", "2007-07-02",
    "2008-04-01", "2008-09-29", "2008-10-10", "2010-07-02", "2012-05-01",
    "2016-10-03", "2018-10-01", "2018-10-29", "2019-04-02", "2020-11-17",
]
# The 20 joined sessions ending 2026-07-10 straddle two Cboe index holidays:
# they span 30 calendar days and 22 VIX sessions, but exactly 20 observations.
PIN_GAP_WINDOW_END = "2026-07-10"
PIN_GAP_WINDOW_START = "2026-06-11"


# ── fixture integrity ─────────────────────────────────────────────


class TestFixtureIntegrity:
    """Cheap structural pins so a refreshed fixture cannot rot the tests silently."""

    def test_vix_csv_header_and_bad_rows_present(self):
        lines = VIX_CSV.rstrip("\n").split("\n")
        assert lines[0] == "DATE,OPEN,HIGH,LOW,CLOSE"
        assert lines[-2] == "not-a-date,14.60,14.70,14.20,14.30"
        assert lines[-1] == "08/17/2026,14.30,14.40,14.10,"
        assert len(lines) == 1 + PIN_VIX_ROW_COUNT + 2

    def test_cor3m_fixture_shape(self):
        assert len(COR3M_ROWS) == 5186
        assert COR3M_ROWS[0] == {"date": "2006-01-03", "cor3m": 31.34}
        assert COR3M_ROWS[-1] == {"date": "2026-08-14", "cor3m": 11.03}
        nulls = [row["date"] for row in COR3M_ROWS if row["cor3m"] is None]
        assert nulls == PIN_COR3M_NULL_DATES
        assert len(COR3M_PARENT_ROWS) == 5171


# ── VIX ingestion parsing ─────────────────────────────────────────


class TestParseIndexCsv:
    def test_fixture_row_count_and_bounds(self):
        rows = _mod().parse_index_csv(VIX_CSV)
        assert len(rows) == PIN_VIX_ROW_COUNT
        assert rows[0] == PIN_VIX_FIRST
        assert rows[-1] == PIN_VIX_LAST

    def test_rows_are_ascending_iso_dates(self):
        dates = [row["date"] for row in _mod().parse_index_csv(VIX_CSV)]
        assert dates == sorted(dates)
        assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in dates)

    def test_malformed_and_empty_close_rows_are_skipped(self):
        text = (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "08/14/2026,14.640000,14.720000,14.180000,14.250000\n"
            "not-a-date,1,1,1,1\n"
            "08/17/2026,1,1,1,\n"
        )
        assert _mod().parse_index_csv(text) == [{"date": "2026-08-14", "value": 14.25}]

    def test_unsorted_input_comes_back_ascending(self):
        text = (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "08/14/2026,1,1,1,14.25\n"
            "08/12/2026,1,1,1,14.55\n"
            "08/13/2026,1,1,1,14.63\n"
        )
        assert _mod().parse_index_csv(text) == [
            {"date": "2026-08-12", "value": 14.55},
            {"date": "2026-08-13", "value": 14.63},
            {"date": "2026-08-14", "value": 14.25},
        ]

    def test_header_only_csv_is_empty(self):
        assert _mod().parse_index_csv("DATE,OPEN,HIGH,LOW,CLOSE\n") == []


# ── correlation math ──────────────────────────────────────────────


class TestCorrWindow:
    def test_worked_example_one(self):
        # x = [10, 12, 14, 16, 18]  ->  mean 14, dx = [-4, -2, 0, 2, 4]
        # y = [20, 21, 23, 24, 27]  ->  mean 23, dy = [-3, -2, 0, 1, 4]
        # sum(dx*dy) = 12 + 4 + 0 + 2 + 16 = 34
        # sum(dx^2)  = 16 + 4 + 0 + 4 + 16 = 40
        # sum(dy^2)  =  9 + 4 + 0 + 1 + 16 = 30
        # den = sqrt(40 * 30) = sqrt(1200) = 34.641016151377546
        # r = 34 / 34.641016151377546 = 0.9814954576223638
        result = _mod().corr_window([10, 12, 14, 16, 18], [20, 21, 23, 24, 27])
        assert abs(result - 0.9814954576223638) < 1e-12

    def test_worked_example_two_perfect_negative(self):
        # x = [1, 2, 3] -> dx = [-1, 0, 1]; y = [6, 4, 2] -> dy = [2, 0, -2]
        # sum(dx*dy) = -2 + 0 - 2 = -4 ; sum(dx^2) = 2 ; sum(dy^2) = 8
        # den = sqrt(2 * 8) = 4 ; r = -4 / 4 = -1.0 exactly
        assert _mod().corr_window([1, 2, 3], [6, 4, 2]) == -1.0

    def test_worked_example_three_degenerate_leg_is_none_not_zero(self):
        # y is constant -> sum(dy^2) = 0 -> den = 0. A degenerate window is
        # unmeasurable; 0.0 is a legitimate breakdown reading and returning it
        # here would fabricate an episode.
        result = _mod().corr_window([1, 2, 3], [5, 5, 5])
        assert result is None
        assert not isinstance(result, float)

    def test_both_legs_flat_is_none(self):
        assert _mod().corr_window([7, 7, 7, 7], [3, 3, 3, 3]) is None

    def test_single_observation_is_none(self):
        # One point has zero deviation on both legs -> den = 0.
        assert _mod().corr_window([14.25], [11.03]) is None

    def test_perfect_positive(self):
        # x == y over 20 points: num == sum(dx^2) == den, so r = 1.0 exactly.
        xs = [float(v) for v in range(1, 21)]
        assert abs(_mod().corr_window(xs, list(xs)) - 1.0) < 1e-12

    def test_order_invariance(self):
        m = _mod()
        xs = [10, 12, 14, 16, 18]
        ys = [20, 21, 23, 24, 27]
        assert m.corr_window(xs, ys) == m.corr_window(ys, xs)

    def test_scale_and_shift_invariance(self):
        # Pearson is invariant under y -> 2y + 7, so example 1's r is unchanged.
        m = _mod()
        xs = [10, 12, 14, 16, 18]
        ys = [20, 21, 23, 24, 27]
        shifted = [2 * v + 7 for v in ys]
        assert abs(m.corr_window(xs, shifted) - 0.9814954576223638) < 1e-12

    def test_levels_convention_not_returns(self):
        """Negative control: the shipped convention correlates LEVELS.

        Differencing the same 21 joined sessions gives ~+0.92 under both
        return conventions and can never reproduce the operator's chart.
        """
        m = _mod()
        joined = _joined_fixture()
        xs = [row["vix_close"] for row in joined][-21:]
        ys = [row["cor3m_close"] for row in joined][-21:]
        pct_x = [(xs[i + 1] - xs[i]) / xs[i] for i in range(20)]
        pct_y = [(ys[i + 1] - ys[i]) / ys[i] for i in range(20)]
        log_x = [math.log(xs[i + 1] / xs[i]) for i in range(20)]
        log_y = [math.log(ys[i + 1] / ys[i]) for i in range(20)]

        assert m.corr_window(pct_x, pct_y) == pytest.approx(PIN_PCT_CHANGE_CORR, abs=1e-12)
        assert m.corr_window(log_x, log_y) == pytest.approx(PIN_LOG_RETURN_CORR, abs=1e-12)
        # ... and the levels window on the same tail is two orders smaller.
        assert m.corr_window(xs[1:], ys[1:]) == pytest.approx(
            PIN_CALIBRATION_CORR, abs=1e-12
        )


class TestNamedConstants:
    def test_module_constants_match_the_spec(self):
        m = _mod()
        assert m.WINDOW == 20
        assert m.MIN_OBSERVATIONS == 20
        assert m.BREAKDOWN_TRIGGER == 0.25
        assert m.BREAKDOWN_EXIT == 0.30
        assert m.EPISODE_MERGE_SESSIONS == 10
        assert m.EPISODE_DEBOUNCE_SESSIONS == 42
        assert m.FORWARD_HORIZONS == (5, 10, 21, 42, 63)
        assert m.DRAWUP_MATERIAL_THRESHOLD == 0.20
        assert m.LOOSENING_FLOOR == 0.50
        assert m.PARENT_LAG_GRACE_SESSIONS == 2
        assert m.PARENT_READ_PAGE_ROWS == 2000


# ── the join ──────────────────────────────────────────────────────


def _joined_fixture() -> list[dict[str, Any]]:
    return _mod().join_series(_mod().parse_index_csv(VIX_CSV), COR3M_PARENT_ROWS)


def _series_fixture() -> list[dict[str, Any]]:
    return _mod().compute_corr_series(_joined_fixture())


def _rows(pairs) -> list[dict[str, Any]]:
    return [{"date": d, "value": v} for d, v in pairs]


class TestJoinSeries:
    def test_inner_join_is_the_cor3m_calendar(self):
        joined = _joined_fixture()
        assert len(joined) == PIN_JOINED_COUNT
        assert joined[0] == PIN_JOINED_FIRST
        assert joined[-1] == {
            "date": PIN_CALIBRATION_DATE,
            "vix_close": PIN_CALIBRATION_VIX,
            "cor3m_close": PIN_CALIBRATION_COR3M,
        }
        dates = [row["date"] for row in joined]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)

    def test_vix_only_index_holidays_are_dropped(self):
        dates = {row["date"] for row in _joined_fixture()}
        for holiday in PIN_VIX_ONLY_2026:
            assert holiday not in dates

    def test_cor3m_null_dates_are_dropped(self):
        dates = {row["date"] for row in _joined_fixture()}
        for gap in PIN_COR3M_NULL_DATES:
            assert gap not in dates

    def test_cor3m_only_date_is_dropped(self):
        joined = _mod().join_series(
            _rows([("2026-08-13", 14.63), ("2026-08-14", 14.25)]),
            _rows([("2026-08-13", 10.80), ("2026-08-14", 11.03), ("2026-08-17", 11.20)]),
        )
        assert [row["date"] for row in joined] == ["2026-08-13", "2026-08-14"]

    def test_unsorted_inputs_join_ascending(self):
        joined = _mod().join_series(
            _rows([("2026-08-14", 14.25), ("2026-08-12", 14.55), ("2026-08-13", 14.63)]),
            _rows([("2026-08-13", 10.80), ("2026-08-14", 11.03), ("2026-08-12", 10.79)]),
        )
        assert [row["date"] for row in joined] == ["2026-08-12", "2026-08-13", "2026-08-14"]
        assert joined[0] == {"date": "2026-08-12", "vix_close": 14.55, "cor3m_close": 10.79}

    def test_duplicate_dates_collapse_to_one_row_last_wins(self):
        joined = _mod().join_series(
            _rows([("2026-08-14", 14.10), ("2026-08-14", 14.25)]),
            _rows([("2026-08-14", 11.00), ("2026-08-14", 11.03)]),
        )
        assert joined == [
            {"date": "2026-08-14", "vix_close": 14.25, "cor3m_close": 11.03}
        ]

    def test_no_overlap_yields_empty_join(self):
        joined = _mod().join_series(
            _rows([("2026-08-14", 14.25)]), _rows([("2026-08-13", 10.80)])
        )
        assert joined == []

    def test_forward_fill_is_banned_and_measurably_different(self):
        """Negative control for B.5: forward filling cor3m over a Cboe index
        holiday pairs a repeated parent point with a genuinely-moved VIX and
        drags the window. At the worst 2024+ window it moves the reading by
        0.203, straight across the LOOSENING/COUPLED band edge.
        """
        m = _mod()
        vix_rows = m.parse_index_csv(VIX_CSV)
        joined = m.join_series(vix_rows, COR3M_PARENT_ROWS)
        dates = [row["date"] for row in joined]
        end = dates.index(PIN_FFILL_CONTROL_DATE)
        inner = m.corr_window(
            [r["vix_close"] for r in joined[end - 19:end + 1]],
            [r["cor3m_close"] for r in joined[end - 19:end + 1]],
        )
        assert inner == pytest.approx(PIN_FFILL_CONTROL_INNER_JOIN, abs=1e-12)

        parent_by_date = {row["date"]: row["value"] for row in COR3M_PARENT_ROWS}
        filled_x: list[float] = []
        filled_y: list[float] = []
        carried: Optional[float] = None
        filled_dates: list[str] = []
        for row in vix_rows:
            carried = parent_by_date.get(row["date"], carried)
            if carried is None:
                continue
            filled_dates.append(row["date"])
            filled_x.append(row["value"])
            filled_y.append(carried)
        fend = filled_dates.index(PIN_FFILL_CONTROL_DATE)
        filled = m.corr_window(filled_x[fend - 19:fend + 1], filled_y[fend - 19:fend + 1])
        assert filled == pytest.approx(PIN_FFILL_CONTROL_FORWARD_FILLED, abs=1e-12)
        assert abs(filled - inner) > 0.2


# ── window boundaries ─────────────────────────────────────────────


class TestComputeCorrSeries:
    def test_emits_one_row_per_joined_session(self):
        joined = _joined_fixture()
        series = _mod().compute_corr_series(joined)
        assert len(series) == len(joined) == PIN_JOINED_COUNT
        assert [row["date"] for row in series] == [row["date"] for row in joined]

    def test_leading_rows_are_null_and_row_19_is_the_first_float(self):
        series = _series_fixture()
        assert all(row["corr20"] is None for row in series[:19])
        assert series[19]["date"] == PIN_FIRST_EMITTABLE
        assert series[19]["corr20"] == pytest.approx(PIN_FIRST_EMITTABLE_CORR, abs=1e-12)
        assert sum(1 for row in series if row["corr20"] is not None) == PIN_CORR_COUNT

    def test_row_value_uses_the_trailing_20_inclusive_slice(self):
        m = _mod()
        joined = _joined_fixture()
        series = m.compute_corr_series(joined)
        for i in (19, 1000, 4000, len(series) - 1):
            expected = m.corr_window(
                [r["vix_close"] for r in joined[i - 19:i + 1]],
                [r["cor3m_close"] for r in joined[i - 19:i + 1]],
            )
            assert series[i]["corr20"] == pytest.approx(expected, abs=1e-15)

    def test_window_sensitivity_at_the_calibration_date(self):
        """B.4: only a 20-session window INCLUSIVE of today reproduces 0.0150."""
        m = _mod()
        joined = _joined_fixture()
        xs = [row["vix_close"] for row in joined]
        ys = [row["cor3m_close"] for row in joined]
        assert m.corr_window(xs[-19:], ys[-19:]) == pytest.approx(PIN_N19_INCLUSIVE, abs=1e-4)
        assert m.corr_window(xs[-20:], ys[-20:]) == pytest.approx(PIN_CALIBRATION_CORR, abs=1e-4)
        assert m.corr_window(xs[-21:], ys[-21:]) == pytest.approx(PIN_N21_INCLUSIVE, abs=1e-4)
        assert m.corr_window(xs[-21:-1], ys[-21:-1]) == pytest.approx(
            PIN_N20_EXCLUDING_TODAY, abs=1e-4
        )

    def test_window_spanning_a_holiday_still_uses_20_observations(self):
        """A window straddling two Cboe index holidays covers 30 calendar days
        and 22 VIX sessions but exactly 20 joined observations. Correlating
        plotted bars, not calendar days, is the point.
        """
        m = _mod()
        joined = _joined_fixture()
        dates = [row["date"] for row in joined]
        end = dates.index(PIN_GAP_WINDOW_END)
        window = joined[end - 19:end + 1]
        assert len(window) == 20
        assert window[0]["date"] == PIN_GAP_WINDOW_START
        assert (
            date.fromisoformat(PIN_GAP_WINDOW_END)
            - date.fromisoformat(PIN_GAP_WINDOW_START)
        ).days + 1 == 30
        vix_sessions = [
            row["date"]
            for row in m.parse_index_csv(VIX_CSV)
            if PIN_GAP_WINDOW_START <= row["date"] <= PIN_GAP_WINDOW_END
        ]
        assert len(vix_sessions) == 22
        assert "2026-06-19" in vix_sessions and "2026-07-03" in vix_sessions
        assert "2026-06-19" not in {row["date"] for row in window}
        assert "2026-07-03" not in {row["date"] for row in window}

    def test_series_shorter_than_min_observations_is_all_null_and_does_not_raise(self):
        joined = _joined_fixture()[:19]
        series = _mod().compute_corr_series(joined)
        assert len(series) == 19
        assert all(row["corr20"] is None for row in series)

    def test_single_row_series(self):
        series = _mod().compute_corr_series(
            [{"date": "2026-08-14", "vix_close": 14.25, "cor3m_close": 11.03}]
        )
        assert series == [
            {"date": "2026-08-14", "vix_close": 14.25, "cor3m_close": 11.03, "corr20": None}
        ]

    def test_empty_series(self):
        assert _mod().compute_corr_series([]) == []

    def test_flat_vix_window_emits_none_not_zero(self):
        """A degenerate window inside an otherwise-live series stays None."""
        joined = [
            {"date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
             "vix_close": 15.0,
             "cor3m_close": 10.0 + i}
            for i in range(20)
        ]
        series = _mod().compute_corr_series(joined)
        assert series[-1]["corr20"] is None


# ── episode detection ─────────────────────────────────────────────


def _synthetic(values: list[Optional[float]]) -> list[dict[str, Any]]:
    """Series rows carrying a supplied corr20 track; dates are labels only."""
    start = date(2020, 1, 1)
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "vix_close": 15.0 + i * 0.01,
            "cor3m_close": 20.0 + i * 0.01,
            "corr20": value,
        }
        for i, value in enumerate(values)
    ]


class TestDetectEpisodesBoundaries:
    def test_run_below_exit_that_never_trips_trigger_is_not_an_episode(self):
        # Dips to 0.28: below EXIT (0.30) but never below TRIGGER (0.25).
        assert _mod().detect_episodes(_synthetic([0.9] * 5 + [0.28] * 4 + [0.9] * 5)) == []

    def test_start_is_the_exit_cross_and_trigger_is_the_trigger_cross(self):
        values = [0.9] * 5 + [0.28, 0.27, 0.22, 0.21, 0.40] + [0.9] * 5
        episodes = _mod().detect_episodes(_synthetic(values))
        assert len(episodes) == 1
        assert episodes[0]["start"] == _synthetic(values)[5]["date"]     # first < 0.30
        assert episodes[0]["trigger"] == _synthetic(values)[7]["date"]   # first < 0.25
        assert episodes[0]["end"] == _synthetic(values)[8]["date"]       # last < 0.30
        assert episodes[0]["sessions"] == 4
        assert episodes[0]["open"] is False

    def test_exactly_0_25_is_not_a_trigger(self):
        # All comparisons are strict: corr20 == 0.25 does not open an episode.
        assert _mod().detect_episodes(_synthetic([0.9] * 3 + [0.25] * 4 + [0.9] * 3)) == []

    def test_exactly_0_30_closes_a_run(self):
        # 0.30 is not < 0.30, so it terminates the run rather than extending it.
        values = [0.9] * 3 + [0.24, 0.30, 0.24] + [0.9] * 3
        episodes = _mod().detect_episodes(_synthetic(values))
        assert len(episodes) == 1
        # Two adjacent one-session runs at indices 3 and 5, separated by a single
        # session, merge under EPISODE_MERGE_SESSIONS.
        assert episodes[0]["start"] == _synthetic(values)[3]["date"]
        assert episodes[0]["end"] == _synthetic(values)[5]["date"]
        assert episodes[0]["sessions"] == 3

    def test_runs_eight_sessions_apart_merge(self):
        # Run A at 5..6, eight sessions above EXIT, run B at 15..16.
        values = [0.9] * 5 + [0.20, 0.20] + [0.9] * 8 + [0.20, 0.20] + [0.9] * 5
        rows = _synthetic(values)
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 1
        assert episodes[0]["start"] == rows[5]["date"]
        assert episodes[0]["end"] == rows[16]["date"]
        assert episodes[0]["sessions"] == 12

    def test_runs_twelve_sessions_apart_do_not_merge_but_debounce_absorbs(self):
        # Run A at 5..6, twelve sessions above EXIT, run B at 19..20. The runs
        # stay distinct (12 > EPISODE_MERGE_SESSIONS) but B's trigger sits 14
        # sessions after A's, inside EPISODE_DEBOUNCE_SESSIONS (42), so B is
        # absorbed: one emitted episode whose end extends over B.
        values = [0.9] * 5 + [0.20, 0.20] + [0.9] * 12 + [0.20, 0.20] + [0.9] * 5
        rows = _synthetic(values)
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 1
        assert episodes[0]["trigger"] == rows[5]["date"]
        assert episodes[0]["end"] == rows[20]["date"]

    def test_debounce_boundary_at_42_sessions_absorbs(self):
        # Trigger A at index 5; trigger B at index 47 -> 47 - 5 == 42, "within
        # 42 sessions of the prior trigger", so it does not open a new episode.
        values = [0.9] * 5 + [0.20] + [0.9] * 41 + [0.20] + [0.9] * 5
        rows = _synthetic(values)
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 1
        assert episodes[0]["trigger"] == rows[5]["date"]
        assert episodes[0]["end"] == rows[47]["date"]

    def test_debounce_boundary_at_43_sessions_opens_a_new_episode(self):
        # Trigger A at index 5; trigger B at index 48 -> 48 - 5 == 43 > 42.
        values = [0.9] * 5 + [0.20] + [0.9] * 42 + [0.20] + [0.9] * 5
        rows = _synthetic(values)
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 2
        assert episodes[0]["trigger"] == rows[5]["date"]
        assert episodes[1]["trigger"] == rows[48]["date"]

    def test_runs_sixty_sessions_apart_are_two_episodes(self):
        values = [0.9] * 5 + [0.20, 0.20] + [0.9] * 60 + [0.20, 0.20] + [0.9] * 5
        rows = _synthetic(values)
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 2
        assert episodes[0]["trigger"] == rows[5]["date"]
        assert episodes[1]["trigger"] == rows[67]["date"]

    def test_trough_is_the_minimum_inside_start_end(self):
        values = [0.9] * 3 + [0.24, 0.10, 0.05, 0.18, 0.29] + [0.9] * 3
        rows = _synthetic(values)
        episode = _mod().detect_episodes(rows)[0]
        assert episode["trough"] == pytest.approx(0.05)
        assert episode["trough_date"] == rows[5]["date"]

    def test_open_when_the_last_session_is_still_below_exit(self):
        rows = _synthetic([0.9] * 5 + [0.24, 0.20, 0.15])
        episode = _mod().detect_episodes(rows)[0]
        assert episode["open"] is True
        assert episode["end"] == rows[-1]["date"]

    def test_not_open_when_the_last_session_recovered_above_exit(self):
        rows = _synthetic([0.9] * 5 + [0.24, 0.20, 0.15, 0.55])
        assert _mod().detect_episodes(rows)[0]["open"] is False

    def test_leading_null_corr_rows_are_ignored(self):
        rows = _synthetic([None] * 19 + [0.9, 0.24, 0.20, 0.9])
        episodes = _mod().detect_episodes(rows)
        assert len(episodes) == 1
        assert episodes[0]["trigger"] == rows[20]["date"]

    def test_all_null_series_has_no_episodes(self):
        assert _mod().detect_episodes(_synthetic([None] * 25)) == []

    def test_empty_series_has_no_episodes(self):
        assert _mod().detect_episodes([]) == []


class TestDetectEpisodesAgainstFixtures:
    def test_five_operator_circles_since_2024(self):
        episodes = [
            episode
            for episode in _mod().detect_episodes(_series_fixture())
            if episode["trigger"] >= "2024-01-01"
        ]
        assert len(episodes) == 5
        for actual, expected in zip(episodes, PIN_EPISODES_2024_PLUS):
            assert actual["trigger"] == expected["trigger"]
            assert actual["start"] == expected["start"]
            assert actual["end"] == expected["end"]
            assert actual["sessions"] == expected["sessions"]
            assert actual["trough_date"] == expected["trough_date"]
            assert actual["open"] is expected["open"]
            assert actual["corr_at_trigger"] == pytest.approx(
                expected["corr_at_trigger"], abs=1e-9
            )
            assert actual["trough"] == pytest.approx(expected["trough"], abs=1e-9)
            assert actual["vix_at_trigger"] == pytest.approx(expected["vix_at_trigger"])

    def test_full_history_episode_count(self):
        assert len(_mod().detect_episodes(_series_fixture())) == PIN_TOTAL_EPISODES

    def test_only_the_newest_episode_is_open(self):
        episodes = _mod().detect_episodes(_series_fixture())
        assert [e["trigger"] for e in episodes if e["open"]] == ["2026-08-11"]


# ── stats, current card, forward statistics ───────────────────────


class TestComputeStats:
    def test_full_history_distribution(self):
        stats = _mod().compute_stats(_series_fixture())
        for key, expected in PIN_STATS.items():
            assert stats[key] == pytest.approx(expected, abs=1e-9), key
        # Population 0.2340949 vs sample 0.2341176; the spec publishes 0.2341.
        assert stats["stddev"] == pytest.approx(PIN_STATS_STDDEV, abs=1e-4)

    def test_vix_coefficient_of_variation_split(self):
        """The mechanism the tab's copy quotes: breakdown windows are quiet."""
        stats = _mod().compute_stats(_series_fixture())
        assert stats["vix_cov_breakdown"] == pytest.approx(PIN_VIX_COV_BREAKDOWN, abs=1e-9)
        assert stats["vix_cov_coupled"] == pytest.approx(PIN_VIX_COV_COUPLED, abs=1e-9)
        assert stats["vix_cov_breakdown"] < stats["vix_cov_coupled"]


class TestBuildCurrent:
    def _current(self):
        m = _mod()
        series = _series_fixture()
        episodes = m.detect_episodes(series)
        return m.build_current(series, episodes, m.compute_stats(series))

    def test_calibration_card(self):
        current = self._current()
        assert current["date"] == PIN_CALIBRATION_DATE
        assert current["vix_close"] == PIN_CALIBRATION_VIX
        assert current["cor3m_close"] == PIN_CALIBRATION_COR3M
        assert current["corr20"] == pytest.approx(PIN_CALIBRATION_CORR, abs=1e-6)
        assert round(current["corr20"], 6) == 0.014969
        assert current["change_1d"] == pytest.approx(PIN_CALIBRATION_CHANGE_1D, abs=1e-9)
        assert current["percentile"] == pytest.approx(PIN_CALIBRATION_PERCENTILE, abs=1e-9)
        assert current["percentile"] < 0.03
        assert current["regime"] == "DECOUPLED"
        assert current["vix_cov_20d"] == pytest.approx(PIN_CALIBRATION_VIX_COV_20D, abs=1e-9)

    def test_open_episode_rides_on_the_current_card(self):
        episode = self._current()["episode"]
        assert episode["trigger"] == "2026-08-11"
        assert episode["start"] == "2026-08-11"
        assert episode["end"] == "2026-08-14"
        assert episode["sessions"] == 4
        assert episode["open"] is True
        assert episode["trough"] == pytest.approx(PIN_CALIBRATION_CORR, abs=1e-9)
        assert episode["trough_date"] == "2026-08-14"
        assert episode["vix_at_trigger"] == pytest.approx(15.28)

    def test_regime_bands_are_strict(self):
        m = _mod()
        stats = m.compute_stats(_series_fixture())

        def regime_for(value: float) -> str:
            rows = _synthetic([0.9] * 24 + [value])
            return m.build_current(rows, m.detect_episodes(rows), stats)["regime"]

        assert regime_for(0.24) == "DECOUPLED"
        assert regime_for(0.25) == "LOOSENING"     # not < 0.25
        assert regime_for(0.49) == "LOOSENING"
        assert regime_for(0.50) == "COUPLED"       # >= 0.50
        assert regime_for(0.90) == "COUPLED"

    def test_empty_series_has_no_current_card(self):
        m = _mod()
        assert m.build_current([], [], None) is None


class TestForwardStats:
    def test_event_and_base_aggregates(self):
        m = _mod()
        series = _series_fixture()
        forward = m.compute_forward_stats(series, m.detect_episodes(series))
        assert tuple(forward["horizons"]) == (5, 10, 21, 42, 63)
        for horizon, expected in PIN_FORWARD_EVENT.items():
            bucket = forward["event"][str(horizon)]
            assert bucket["n"] == expected["n"], horizon
            for key in ("mean_drawup", "median_drawup", "p_higher", "p_drawup_20"):
                assert bucket[key] == pytest.approx(expected[key], abs=1e-9), (horizon, key)
        for horizon, expected in PIN_FORWARD_BASE.items():
            bucket = forward["base"][str(horizon)]
            assert bucket["n"] == expected["n"], horizon
            for key in ("mean_drawup", "median_drawup", "p_higher", "p_drawup_20"):
                assert bucket[key] == pytest.approx(expected[key], abs=1e-9), (horizon, key)

    def test_post_breakdown_drawup_is_below_the_base_rate_at_every_horizon(self):
        """The editorial constraint of the whole tab, pinned as arithmetic."""
        m = _mod()
        series = _series_fixture()
        forward = m.compute_forward_stats(series, m.detect_episodes(series))
        for horizon in (5, 10, 21, 42, 63):
            key = str(horizon)
            assert forward["event"][key]["mean_drawup"] < forward["base"][key]["mean_drawup"]

    def test_open_episode_is_excluded_from_the_event_aggregate(self):
        # 31 episodes, exactly one open -> 30 resolved triggers at h=5.
        m = _mod()
        series = _series_fixture()
        episodes = m.detect_episodes(series)
        assert sum(1 for e in episodes if e["open"]) == 1
        forward = m.compute_forward_stats(series, episodes)
        assert forward["event"]["5"]["n"] == len(episodes) - 1 == 30



# ── migration + writer upsert ─────────────────────────────────────


class _RecordingConnection:
    """sqlite3 stand-in for the Hrana client that refuses executemany.

    Over the Hrana HTTP transport executemany is one round-trip PER ROW; the
    writer must batch instead (feedback_turso_hrana_io_bounding).
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple = ()):  # noqa: D102
        self.statements.append((sql, tuple(params)))
        return self._conn.execute(sql, params)

    def executemany(self, *_args, **_kwargs):  # noqa: D102
        raise AssertionError("executemany is one Hrana round-trip per row")

    def commit(self):  # noqa: D102
        self.commits += 1
        self._conn.commit()


@pytest.fixture
def vixcor_db(monkeypatch: pytest.MonkeyPatch):
    """In-memory sqlite carrying only the 0049 schema, wired into the writer."""
    assert MIGRATION.exists(), "scripts/db/migrations/0049_vixcor.sql must exist"
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
    )
    conn.executescript(MIGRATION.read_text())
    recording = _RecordingConnection(conn)

    from db import writer as writer_mod

    monkeypatch.setattr(writer_mod, "get_db", lambda: recording)
    try:
        yield writer_mod, recording, conn
    finally:
        conn.close()


class TestVixcorStorage:
    def test_migration_registers_version_49(self):
        assert MIGRATION.exists(), "scripts/db/migrations/0049_vixcor.sql must exist"
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 49"
        ).fetchone() == (49,)

    def test_table_columns_pk_and_index(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        info = conn.execute("PRAGMA table_info(vixcor_history)").fetchall()
        columns = {row[1]: row for row in info}
        assert set(columns) == {"date", "vix_close", "cor3m_close", "corr20", "recorded_at"}
        assert columns["date"][2] == "TEXT" and columns["date"][5] == 1     # PRIMARY KEY
        assert columns["vix_close"][2] == "REAL" and columns["vix_close"][3] == 1   # NOT NULL
        assert columns["cor3m_close"][2] == "REAL" and columns["cor3m_close"][3] == 1
        assert columns["corr20"][2] == "REAL" and columns["corr20"][3] == 0   # nullable
        assert columns["recorded_at"][2] == "TEXT" and columns["recorded_at"][3] == 1
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(vixcor_history)")}
        assert "idx_vixcor_history_date" in indexes

    def test_corr20_accepts_null_but_closes_do_not(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        conn.executescript(MIGRATION.read_text())
        conn.execute(
            "INSERT INTO vixcor_history (date, vix_close, cor3m_close, corr20, recorded_at) "
            "VALUES ('2006-01-03', 11.14, 31.34, NULL, 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO vixcor_history (date, vix_close, cor3m_close, corr20, recorded_at) "
                "VALUES ('2006-01-04', NULL, 31.0, 0.5, 'x')"
            )

    def test_upsert_writes_rows_and_is_idempotent_on_date(self, vixcor_db):
        writer_mod, _recording, conn = vixcor_db
        rows = [
            {"date": "2026-08-13", "vix_close": 14.63, "cor3m_close": 10.80, "corr20": 0.0298},
            {"date": "2026-08-14", "vix_close": 14.25, "cor3m_close": 11.03, "corr20": 0.0150},
        ]
        writer_mod.upsert_vixcor_rows(rows, recorded_at="2026-08-15T02:35:00Z")
        writer_mod.upsert_vixcor_rows(
            [{**rows[1], "corr20": 0.0151}], recorded_at="2026-08-16T02:35:00Z"
        )
        stored = conn.execute(
            "SELECT date, vix_close, cor3m_close, corr20, recorded_at "
            "FROM vixcor_history ORDER BY date"
        ).fetchall()
        assert len(stored) == 2
        assert stored[1][0] == "2026-08-14"
        assert stored[1][3] == pytest.approx(0.0151)
        assert stored[1][4] == "2026-08-16T02:35:00Z"
        assert stored[0][4] == "2026-08-15T02:35:00Z"

    def test_upsert_persists_null_corr20(self, vixcor_db):
        writer_mod, _recording, conn = vixcor_db
        writer_mod.upsert_vixcor_rows(
            [{"date": "2006-01-03", "vix_close": 11.14, "cor3m_close": 31.34, "corr20": None}],
            recorded_at="2026-08-15T02:35:00Z",
        )
        assert conn.execute("SELECT corr20 FROM vixcor_history").fetchone() == (None,)

    def test_upsert_batches_at_400_rows_and_never_executemany(self, vixcor_db):
        writer_mod, recording, conn = vixcor_db
        rows = [
            {
                "date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(),
                "vix_close": 15.0 + i * 0.01,
                "cor3m_close": 20.0 + i * 0.01,
                "corr20": 0.5,
            }
            for i in range(900)
        ]
        writer_mod.upsert_vixcor_rows(rows, recorded_at="2026-08-15T02:35:00Z")
        # 900 rows / 400 per chunk = ceil(2.25) = 3 statements, not 900.
        assert len(recording.statements) == 3
        assert [sql.count("(?, ?, ?, ?, ?)") for sql, _ in recording.statements] == [400, 400, 100]
        assert all("ON CONFLICT(date) DO UPDATE" in sql for sql, _ in recording.statements)
        assert conn.execute("SELECT COUNT(*) FROM vixcor_history").fetchone()[0] == 900

    def test_empty_rows_write_nothing(self, vixcor_db):
        writer_mod, recording, _conn = vixcor_db
        writer_mod.upsert_vixcor_rows([], recorded_at="2026-08-15T02:35:00Z")
        assert recording.statements == []


# ── job orchestration + degradation ───────────────────────────────


class _StubClient:
    """CboeClient stand-in: fetch_history(symbol, if_modified_since=...)."""

    def __init__(self, text: Optional[str], last_modified: Optional[str]):
        self._text = text
        self._last_modified = last_modified
        self.calls: list[tuple[str, Optional[str]]] = []

    def fetch_history(self, symbol: str, if_modified_since: Optional[str] = None):
        self.calls.append((symbol, if_modified_since))
        if if_modified_since is not None and if_modified_since == self._last_modified:
            return None, self._last_modified
        return self._text, self._last_modified


class _FakeWriter:
    def __init__(self):
        self.replica_guards = 0
        self.price_history: list[tuple[str, list[dict[str, Any]]]] = []
        self.vixcor_rows: list[tuple[list[dict[str, Any]], Optional[str]]] = []
        self.snapshots: list[tuple[str, str, dict[str, Any]]] = []
        self.health: list[tuple[str, str, Optional[dict[str, Any]]]] = []

    def ensure_no_replica_for_writers(self):
        self.replica_guards += 1

    def upsert_price_history_rows(self, symbol, rows):
        self.price_history.append((symbol, list(rows)))

    def upsert_vixcor_rows(self, rows, recorded_at=None):
        self.vixcor_rows.append((list(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, status, finished_at=None, **kwargs):
        self.health.append((service, status, kwargs.get("error")))


VIX_STAMP = "Sat, 15 Aug 2026 23:01:11 GMT"
# radon-vixcor.timer fires 02:35 UTC = 22:35 ET the prior evening, so this is
# the Friday-evening run whose last completed session is 2026-08-14.
NOW_SAT = datetime(2026, 8, 15, 2, 35, tzinfo=timezone.utc)
# The Monday-evening run: last completed session 2026-08-17.
NOW_TUE = datetime(2026, 8, 18, 2, 35, tzinfo=timezone.utc)


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """fetch_vixcor with its JSON cache relocated and its writer faked."""
    mod = _mod()
    monkeypatch.setattr(mod, "VIXCOR_JSON", tmp_path / "vixcor.json")
    fake = _FakeWriter()
    monkeypatch.setattr(mod, "writer", fake)
    monkeypatch.setattr(mod, "load_cor3m_rows", lambda: COR3M_PARENT_ROWS)
    monkeypatch.setattr(mod, "latest_stored_vix_date", lambda: "2026-08-13")
    return mod, fake, tmp_path


class TestRunHappyPath:
    def test_changed_source_computes_writes_rows_snapshot_and_heartbeat(self, job):
        mod, fake, _tmp = job
        client = _StubClient(VIX_CSV, VIX_STAMP)
        payload = mod.run(client=client, now=NOW_SAT)

        assert client.calls == [("VIX", None)]
        assert payload["status"] == "ok"
        assert payload["as_of"] == PIN_CALIBRATION_DATE
        assert payload["parent_as_of"] == PIN_CALIBRATION_DATE
        assert payload["vix_as_of"] == PIN_CALIBRATION_DATE
        assert payload["expected_session"] == PIN_CALIBRATION_DATE
        assert payload["lag_sessions"] == 0
        assert payload["window"] == 20
        assert payload["count"] == PIN_JOINED_COUNT
        assert payload["corr_count"] == PIN_CORR_COUNT
        assert payload["source_last_modified"] == {"vix": VIX_STAMP}
        assert payload["current"]["corr20"] == pytest.approx(PIN_CALIBRATION_CORR, abs=1e-6)
        assert payload["scan_time"].endswith("Z")

        assert fake.replica_guards >= 1
        assert [row[0] for row in fake.snapshots] == ["vixcor"]
        assert fake.health == [("vixcor", "ok", None)]
        assert len(fake.vixcor_rows) == 1
        assert len(fake.vixcor_rows[0][0]) == PIN_JOINED_COUNT
        assert fake.vixcor_rows[0][1] == payload["scan_time"]

    def test_series_rows_carry_the_episode_membership_flag(self, job):
        mod, _fake, _tmp = job
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        by_date = {row["date"]: row for row in payload["series"]}
        assert by_date["2026-08-14"]["episode"] is True
        assert by_date["2026-08-11"]["episode"] is True
        assert by_date["2026-08-10"]["episode"] is False
        assert by_date["2006-01-03"]["corr20"] is None
        assert by_date["2006-01-03"]["episode"] is False

    def test_bounded_vix_tail_write(self, job):
        """A normal day writes the tail only, not 9,251 rows."""
        mod, fake, _tmp = job
        mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        symbol, rows = fake.price_history[0]
        assert symbol == "VIX"
        # latest_stored_vix_date() == "2026-08-13" -> 2026-08-13 and 2026-08-14.
        assert [row["date"] for row in rows] == ["2026-08-13", "2026-08-14"]
        assert all(row["source"] == "cboe" for row in rows)
        assert rows[-1]["close"] == PIN_CALIBRATION_VIX

    def test_backfill_writes_the_full_vix_history(self, job):
        mod, fake, _tmp = job
        mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT, backfill=True)
        symbol, rows = fake.price_history[0]
        assert symbol == "VIX"
        assert len(rows) == PIN_VIX_ROW_COUNT
        assert rows[0]["date"] == PIN_VIX_FIRST["date"]

    def test_missing_stored_max_date_falls_back_to_the_full_write(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "latest_stored_vix_date", lambda: None)
        mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        assert len(fake.price_history[0][1]) == PIN_VIX_ROW_COUNT

    def test_unchanged_source_reuses_cached_payload_without_row_writes(self, job):
        mod, fake, tmp_path = job
        cached = {
            "scan_time": "2026-08-14T02:35:00Z",
            "source_last_modified": {"vix": VIX_STAMP},
            "status": "ok",
            "as_of": PIN_CALIBRATION_DATE,
            "count": PIN_JOINED_COUNT,
            "corr_count": PIN_CORR_COUNT,
            "series": [],
            "episodes": [],
            "current": {"date": PIN_CALIBRATION_DATE},
            "stats": {},
            "forward_stats": {},
        }
        mod.VIXCOR_JSON.write_text(json.dumps(cached))

        client = _StubClient(VIX_CSV, VIX_STAMP)
        payload = mod.run(client=client, now=NOW_SAT)

        assert client.calls == [("VIX", VIX_STAMP)]
        assert payload["count"] == PIN_JOINED_COUNT
        assert payload["scan_time"] != cached["scan_time"]
        assert fake.vixcor_rows == []
        assert fake.price_history == []
        assert [row[0] for row in fake.snapshots] == ["vixcor"]
        assert fake.health == [("vixcor", "ok", None)]

    def test_json_cache_is_written_atomically(self, job):
        mod, _fake, tmp_path = job
        mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        assert mod.VIXCOR_JSON.exists()
        assert not (tmp_path / "vixcor.json.tmp").exists()
        assert json.loads(mod.VIXCOR_JSON.read_text())["as_of"] == PIN_CALIBRATION_DATE

    def test_zero_rows_raises_and_writes_nothing(self, job):
        mod, fake, _tmp = job
        empty = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.24,17.24,17.24,17.24\n"
        with pytest.raises(ValueError, match="zero rows"):
            mod.run(client=_StubClient(empty, VIX_STAMP), now=NOW_SAT)
        assert fake.snapshots == []
        assert fake.health == []
        assert fake.vixcor_rows == []
        assert not mod.VIXCOR_JSON.exists()

    def test_market_status_defaults_closed_when_the_calendar_raises(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, _fake, _tmp = job
        import utils.market_calendar as calendar

        def boom(*_args, **_kwargs):
            raise RuntimeError("holiday file unreadable")

        monkeypatch.setattr(calendar, "market_state", boom)
        monkeypatch.setattr(mod, "market_state", boom, raising=False)
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        assert payload["market_status"] == "closed"

    def test_payload_episodes_carry_forward_drawups(self, job):
        mod, _fake, _tmp = job
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        episodes = {episode["trigger"]: episode for episode in payload["episodes"]}
        assert len(payload["episodes"]) == PIN_TOTAL_EPISODES
        for horizon, expected in PIN_FORWARD_2025_10_01.items():
            assert episodes["2025-10-01"]["forward"][str(horizon)] == pytest.approx(
                expected, abs=1e-9
            )
        # Fewer than 63 forward joined sessions -> None, never a sentinel.
        assert episodes["2026-05-22"]["forward"]["63"] is None
        assert all(value is None for value in episodes["2026-08-11"]["forward"].values())

    def test_progress_goes_to_stderr_and_stdout_stays_clean(self, job, capsys):
        mod, _fake, _tmp = job
        mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "[vixcor]" in captured.err

    def test_main_json_prints_only_the_payload_to_stdout(
        self, job, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        mod, _fake, _tmp = job
        payload = {"scan_time": "2026-08-15T02:35:00Z", "status": "ok", "series": []}
        monkeypatch.setattr(mod, "run", lambda **_kwargs: payload)
        monkeypatch.setattr(sys, "argv", ["fetch_vixcor.py", "--json"])
        mod.main()
        captured = capsys.readouterr()
        assert json.loads(captured.out) == payload


class TestParentLagDegradation:
    """A derived child must not treat parent publication lag as corruption.

    cor_history legitimately runs a session behind. Failing the unit here
    pages the operator on every run (feedback_derived_indicator_parent_embargo).
    """

    def test_one_session_behind_holds_with_an_ok_heartbeat(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-13"))
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)

        assert payload["status"] == "holding"
        assert payload["lag_sessions"] == 1
        assert payload["parent_as_of"] == "2026-08-13"
        assert payload["vix_as_of"] == "2026-08-14"
        assert payload["as_of"] == "2026-08-13"     # max JOINED date, never filled forward
        assert payload["expected_session"] == "2026-08-14"
        # 5,171 joined rows minus the one session the parent has not published.
        assert payload["count"] == PIN_JOINED_COUNT - 1

        assert [row[0] for row in fake.snapshots] == ["vixcor"]
        assert fake.health == [("vixcor", "ok", None)], "a routine parent lag must not page"
        assert len(fake.vixcor_rows[0][0]) == PIN_JOINED_COUNT - 1

    def test_two_sessions_behind_is_still_the_grace_band(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-12"))
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)
        assert payload["lag_sessions"] == 2   # PARENT_LAG_GRACE_SESSIONS
        assert payload["status"] == "holding"
        assert fake.health == [("vixcor", "ok", None)]

    def test_beyond_grace_writes_an_error_heartbeat_without_raising(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-11"))
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_SAT)

        assert payload["lag_sessions"] == 3
        assert payload["status"] == "stale_parent"
        assert payload["as_of"] == "2026-08-11"
        assert [row[0] for row in fake.snapshots] == ["vixcor"], "the tab keeps serving"
        service, status, error = fake.health[0]
        assert (service, status) == ("vixcor", "error")
        assert error and error.get("message")
        assert error.get("next_attempt_at")
        # Raising would fail the unit and page a SECOND time for one condition.
        assert len(fake.vixcor_rows[0][0]) == PIN_JOINED_COUNT - 3

    def test_a_shared_missing_session_is_not_parent_lag(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        """Cboe published late: both legs stop at 2026-08-13. lag == 0."""
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-13"))
        client = _StubClient(_vix_csv_through("2026-08-13"), VIX_STAMP)
        payload = mod.run(client=client, now=NOW_SAT)

        assert payload["lag_sessions"] == 0
        assert payload["status"] == "ok"
        assert payload["as_of"] == "2026-08-13"
        assert payload["expected_session"] == "2026-08-14"
        assert fake.health == [("vixcor", "ok", None)]

    def test_lag_is_counted_in_sessions_not_calendar_days(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        """Parent at Thursday 2026-08-13, expected session Monday 2026-08-17.

        Four calendar days apart, but only one VIX session (2026-08-14) sits
        in between, so this is a one-session hold, not a three-session page.
        """
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-13"))
        payload = mod.run(client=_StubClient(VIX_CSV, VIX_STAMP), now=NOW_TUE)

        assert payload["expected_session"] == "2026-08-17"
        assert payload["lag_sessions"] == 1
        assert payload["status"] == "holding"
        assert fake.health == [("vixcor", "ok", None)]

    def test_count_vix_sessions_between_uses_the_vix_calendar(self):
        m = _mod()
        vix_dates = [row["date"] for row in m.parse_index_csv(VIX_CSV)]
        # (2026-08-13, 2026-08-17] contains one VIX session: 2026-08-14.
        assert m.count_vix_sessions_between(vix_dates, "2026-08-13", "2026-08-17") == 1
        # (2026-08-11, 2026-08-14] contains 08-12, 08-13, 08-14.
        assert m.count_vix_sessions_between(vix_dates, "2026-08-11", "2026-08-14") == 3
        # Equal endpoints are zero, and the bound is strict on the low side.
        assert m.count_vix_sessions_between(vix_dates, "2026-08-14", "2026-08-14") == 0
        # 2026-07-03 is a Cboe index holiday for cor3m but a real VIX session.
        assert m.count_vix_sessions_between(vix_dates, "2026-07-02", "2026-07-06") == 2


class TestParentLoading:
    def test_turso_read_is_keyset_paginated_and_never_unbounded(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Hrana I/O bounding: ~5,190 rows must page on the date cursor."""
        mod = _mod()
        import db.client as client_mod

        pages: list[tuple[str, tuple]] = []
        rows = [(row["date"], row["value"]) for row in COR3M_PARENT_ROWS]

        class _Cursor:
            def __init__(self, result):
                self._result = result

            def fetchall(self):
                return self._result

        class _Db:
            def execute(self, sql, params=()):
                pages.append((sql, tuple(params)))
                cursor, limit = params
                page = [row for row in rows if row[0] > cursor][:limit]
                return _Cursor(page)

        monkeypatch.setattr(client_mod, "get_db", lambda: _Db())
        loaded = mod.load_cor3m_rows()

        assert len(loaded) == len(COR3M_PARENT_ROWS)
        assert loaded[0] == {"date": "2006-01-03", "value": 31.34}
        assert loaded[-1] == {"date": "2026-08-14", "value": 11.03}
        assert len(pages) >= 3, "5,171 rows at 2,000 per page is at least 3 round trips"
        assert all("LIMIT ?" in sql for sql, _ in pages)
        assert all(params[1] == mod.PARENT_READ_PAGE_ROWS for _, params in pages)

    def test_unreachable_turso_falls_back_to_disk_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        mod = _mod()
        import db.client as client_mod

        def boom():
            raise RuntimeError("turso unreachable")

        monkeypatch.setattr(client_mod, "get_db", boom)
        cor_json = tmp_path / "cor.json"
        cor_json.write_text(
            json.dumps(
                {
                    "series": [
                        {"date": "2026-08-14", "cor3m": 11.03},
                        {"date": "2026-08-12", "cor3m": 10.79},
                        {"date": "2026-08-13", "cor3m": None},
                    ]
                }
            )
        )
        monkeypatch.setattr(mod, "COR_JSON", cor_json)
        assert mod.load_cor3m_rows() == [
            {"date": "2026-08-12", "value": 10.79},
            {"date": "2026-08-14", "value": 11.03},
        ]


# ── the rebuttal may not over-claim either ────────────────────────

SPEC = Path(__file__).parents[2] / "docs" / "indicators" / "vixcor.md"


def _flat(text: str) -> str:
    """Collapse wrapping so a reflowed docstring cannot break a copy pin."""
    return " ".join(text.split())


def _spec_section_zero() -> str:
    """Section 0 of the spec, where the validation verdict is stated."""
    body = SPEC.read_text()
    start = body.index("## 0.")
    return _flat(body[start:body.index("## A.", start)])


# The claim that is true of the MEAN only, at each of its six shipped sites.
OVER_CLAIM_PATTERNS = (
    "below the all-session base rate at every horizon",
    "below the unconditional base rate at every horizon",
    "below the unconditional rate at every horizon",
)
QUALIFIED_MEAN = "below the all-session mean at every horizon"
QUALIFIED_MEDIAN = "on the median the two are indistinguishable at 42 sessions"

# Hand-computed from PIN_FORWARD_EVENT / PIN_FORWARD_BASE at h=42:
#   mean    0.3301047625491723  vs 0.44010489348883564  -> event 11.00pp BELOW
#   median  0.31786580800443087 vs 0.2930825885883874   -> event  2.48pp ABOVE
#           0.31786580800443087 - 0.2930825885883874 = 0.02478321941604347
#   P(+20%) 0.6333333333333333  vs 0.6379647749510763   -> 0.46pp apart
# Mann-Whitney at h=42 gives p = 0.683: "no difference" is the defensible
# statement at that horizon, and 42 sessions is exactly the span of the
# operator's four blue arrows.
PIN_H42_MEDIAN_DELTA = 0.02478321941604347
PIN_H42_P20_DELTA = 0.6333333333333333 - 0.6379647749510763


class TestRebuttalIsScopedToTheMean:
    """Overstating the refutation is the same sin as overstating the claim."""

    def test_forward_stats_docstring_names_the_mean_and_concedes_the_median(self):
        doc = _flat(_mod().compute_forward_stats.__doc__ or "").lower()
        assert QUALIFIED_MEAN in doc
        assert QUALIFIED_MEDIAN in doc
        for claim in OVER_CLAIM_PATTERNS:
            assert claim not in doc

    def test_spec_section_zero_qualifies_the_first_bullet(self):
        section = _spec_section_zero().lower()
        assert QUALIFIED_MEAN in section
        assert QUALIFIED_MEDIAN in section
        for claim in OVER_CLAIM_PATTERNS:
            assert claim not in section

    def test_h42_median_reverses_the_mean_ordering(self):
        """The arithmetic the qualification exists for, from the fixtures."""
        series = _series_fixture()
        episodes = _mod().detect_episodes(series)
        stats = _mod().compute_forward_stats(series, episodes)
        event = stats["event"]["42"]
        base = stats["base"]["42"]

        assert event["mean_drawup"] < base["mean_drawup"]
        assert event["median_drawup"] > base["median_drawup"]
        assert event["median_drawup"] - base["median_drawup"] == pytest.approx(
            PIN_H42_MEDIAN_DELTA, abs=1e-12
        )
        assert event["p_drawup_20"] - base["p_drawup_20"] == pytest.approx(
            PIN_H42_P20_DELTA, abs=1e-12
        )
        # The base mean is right-skewed ~1.50x, which is what manufactures the
        # mean-only gap: 0.44010489348883564 / 0.2930825885883874 = 1.5016...
        assert base["mean_drawup"] / base["median_drawup"] == pytest.approx(1.5016, abs=1e-3)

    def test_the_mean_claim_itself_still_holds_at_every_horizon(self):
        """The qualification narrows the claim; it does not abandon it."""
        series = _series_fixture()
        stats = _mod().compute_forward_stats(series, _mod().detect_episodes(series))
        for horizon in _mod().FORWARD_HORIZONS:
            key = str(horizon)
            assert stats["event"][key]["mean_drawup"] < stats["base"][key]["mean_drawup"]


class TestTroughAnchoredHigherRate:
    """M2 — the spec quotes the h=21 figure while naming the h=42 horizon."""

    @staticmethod
    def _trough_anchored_higher(horizon: int) -> tuple[int, int]:
        series = _series_fixture()
        positions = {row["date"]: index for index, row in enumerate(series)}
        higher = 0
        resolved = 0
        for episode in _mod().detect_episodes(series):
            if episode["open"]:
                continue
            index = positions[episode["trough_date"]]
            if index + horizon >= len(series):
                continue
            resolved += 1
            if series[index + horizon]["vix_close"] > series[index]["vix_close"]:
                higher += 1
        return higher, resolved

    def test_trough_anchored_p_higher_at_21_sessions_is_16_of_30(self):
        assert self._trough_anchored_higher(21) == (16, 30)   # 53.3%

    def test_trough_anchored_p_higher_at_42_sessions_is_15_of_30(self):
        assert self._trough_anchored_higher(42) == (15, 30)   # 50.0%

    def test_trigger_anchored_h42_is_the_60_percent_the_spec_compares_against(self):
        series = _series_fixture()
        positions = {row["date"]: index for index, row in enumerate(series)}
        higher = sum(
            1
            for episode in _mod().detect_episodes(series)
            if not episode["open"]
            and series[positions[episode["trigger"]] + 42]["vix_close"]
            > series[positions[episode["trigger"]]]["vix_close"]
        )
        assert (higher, 30) == (18, 30)   # 60.0%, the one pro-claim number

    def test_spec_quotes_the_h42_trough_figure_and_names_the_horizon(self):
        section = _spec_section_zero()
        assert "collapses to 53.3% / p=0.17" not in section, (
            "53.3% (16/30) is the h=21 trough-anchored value, not h=42"
        )
        assert "50.0%" in section
        assert "15 of 30" in section
        assert "h=42" in section
        if "53.3%" in section:
            assert "h=21" in section, "the h=21 figure must be labelled as h=21"


# ── the 304 fast path must not launder a degraded state ───────────

# The Friday-evening run one week after NOW_SAT: nothing new has been
# published, so the conditional GET 304s, but the last completed session has
# moved on to 2026-08-21 and the cached payload is now six sessions behind.
NOW_SAT_NEXT_WEEK = datetime(2026, 8, 22, 2, 35, tzinfo=timezone.utc)


class TestConditionalGetDoesNotLaunderDegradedState:
    """O1 / O2 — a 304 reuses the payload, so it must reuse its verdict too.

    cloud/services/radon-vixcor.timer documents weekend and holiday runs as
    304s BY DESIGN, and the watchdog only escalates after two CONSECUTIVE
    error cycles. A 304 that writes ``ok`` therefore de-escalates any parent
    breakage that spans a weekend, and nulls the stored ``last_error`` on the
    way through (scripts/db/service_health_sql.py sets
    ``last_error = excluded.last_error``).
    """

    def test_304_after_a_stale_parent_keeps_the_error_heartbeat(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-11"))
        client = _StubClient(VIX_CSV, VIX_STAMP)

        first = mod.run(client=client, now=NOW_SAT)
        second = mod.run(client=client, now=NOW_SAT)

        # Cycle 2 really is the 304 path: conditional GET, no row writes.
        assert client.calls == [("VIX", None), ("VIX", VIX_STAMP)]
        assert len(fake.vixcor_rows) == 1
        assert first["status"] == "stale_parent"
        assert second["status"] == "stale_parent"

        assert [row[1] for row in fake.health] == ["error", "error"], (
            "a 304 that reuses a stale_parent payload must reuse its heartbeat"
        )

    def test_304_preserves_the_stored_error_text(
        self, job, monkeypatch: pytest.MonkeyPatch
    ):
        mod, fake, _tmp = job
        monkeypatch.setattr(mod, "load_cor3m_rows", lambda: _cor3m_through("2026-08-11"))
        client = _StubClient(VIX_CSV, VIX_STAMP)
        mod.run(client=client, now=NOW_SAT)
        mod.run(client=client, now=NOW_SAT)

        _service, _status, error = fake.health[1]
        assert error, "last_error = excluded.last_error nulls the stored text"
        assert error.get("message")
        assert "sessions behind" in error["message"]
        assert error.get("next_attempt_at")

    def test_304_does_not_refresh_freshness_for_a_payload_that_has_gone_stale(
        self, job
    ):
        """O2 — the route gates on scan_time (VIXCOR_MAX_AGE_MS).

        A frozen Cboe CDN 304s forever: scan_time keeps advancing while
        as_of / expected_session / lag_sessions stay pinned to the day the
        source last moved, so the heartbeat stays green and nothing pages.
        """
        mod, fake, _tmp = job
        client = _StubClient(VIX_CSV, VIX_STAMP)

        first = mod.run(client=client, now=NOW_SAT)
        assert first["status"] == "ok"
        assert first["expected_session"] == "2026-08-14"
        assert first["lag_sessions"] == 0

        second = mod.run(client=client, now=NOW_SAT_NEXT_WEEK)

        # The heartbeat still beats: the writer is alive and must say so.
        assert second["scan_time"] != first["scan_time"]
        # But the data has not moved, and the payload must not pretend it has.
        assert second["as_of"] == "2026-08-14"
        assert second["expected_session"] == "2026-08-21", (
            "expected_session is frozen at the last changed-source run"
        )
        assert second["lag_sessions"] >= 3
        assert second["status"] == "stale_parent"
        assert fake.health[1][1] == "error"

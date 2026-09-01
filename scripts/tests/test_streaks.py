"""Consecutive daily gains (STREAKS) — pure computation + payload contract.

Ground truth for the Yahoo parser is a REAL captured chart payload:
  fixtures/yahoo_chart_sample.json — SPY, 12 sessions ending 2026-08-28,
  captured from query1.finance.yahoo.com on 2026-08-30.
Expected values below were derived by inspecting the fixture directly,
not computed by hand.
"""
import json
import math
from pathlib import Path


from utils.streaks import (
    build_streaks_payload,
    closes_to_rows,
    compute_streaks,
    parse_yahoo_chart,
    run_lengths,
)

FIXTURES = Path(__file__).parent / "fixtures"
YAHOO_CHART = json.loads((FIXTURES / "yahoo_chart_sample.json").read_text())

# Canonical 9-session series used across the payload tests. Streaks:
# closes  1  2  3  2  2  3  4  5  6
# streak  0  1  2  0  0  1  2  3  4
DATES = [
    "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
    "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14",
]
CLOSES = [1.0, 2.0, 3.0, 2.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def canonical_map():
    return dict(zip(DATES, CLOSES))


# ── compute_streaks ───────────────────────────────────────────────


class TestComputeStreaks:
    def test_empty(self):
        assert compute_streaks([]) == []

    def test_single_close_has_no_prior_session(self):
        assert compute_streaks([5.0]) == [0]

    def test_canonical_series(self):
        assert compute_streaks(CLOSES) == [0, 1, 2, 0, 0, 1, 2, 3, 4]

    def test_flat_close_resets_the_streak(self):
        assert compute_streaks([1.0, 1.0, 2.0]) == [0, 0, 1]

    def test_monotonic_decline_is_all_zeros(self):
        assert compute_streaks([5.0, 4.0, 3.0, 2.0]) == [0, 0, 0, 0]


# ── run_lengths ───────────────────────────────────────────────────


class TestRunLengths:
    def test_canonical_runs(self):
        assert run_lengths([0, 1, 2, 0, 0, 1, 2, 3, 4]) == [2, 4]

    def test_trailing_in_progress_run_counts(self):
        assert run_lengths([0, 1]) == [1]

    def test_no_runs(self):
        assert run_lengths([0, 0, 0]) == []


# ── closes_to_rows ────────────────────────────────────────────────


class TestClosesToRows:
    def test_sorts_ascending_and_truncates_datetime_keys(self):
        rows = closes_to_rows({
            "2026-01-03": 5.0,
            "2026-01-02T00:00:00": 4.0,
        })
        assert rows == [
            {"date": "2026-01-02", "close": 4.0},
            {"date": "2026-01-03", "close": 5.0},
        ]

    def test_drops_non_finite_and_non_positive_closes(self):
        rows = closes_to_rows({
            "2026-01-02": None,
            "2026-01-05": 0.0,
            "2026-01-06": -3.0,
            "2026-01-07": math.nan,
            "2026-01-08": 7.5,
        })
        assert rows == [{"date": "2026-01-08", "close": 7.5}]

    def test_duplicate_dates_after_truncation_last_wins(self):
        rows = closes_to_rows({
            "2026-01-02": 4.0,
            "2026-01-02T00:00:00": 4.5,
        })
        assert rows == [{"date": "2026-01-02", "close": 4.5}]


# ── build_streaks_payload ─────────────────────────────────────────


class TestBuildStreaksPayload:
    def test_full_contract_on_canonical_series(self):
        payload = build_streaks_payload(
            "spy", canonical_map(), source="uw",
            scan_time="2026-08-30T21:00:00+00:00",
        )
        assert payload["symbol"] == "SPY"
        assert payload["scan_time"] == "2026-08-30T21:00:00+00:00"
        assert payload["source"] == "uw"
        assert payload["missing"] is False
        assert payload["count"] == 9
        assert payload["first_date"] == DATES[0]
        assert payload["last_date"] == DATES[-1]
        assert payload["current"] == {
            "date": DATES[-1],
            "close": 6.0,
            "streak": 4,
            "day_change_pct": 20.0,
        }
        assert payload["stats"] == {
            "max_streak": 4,
            "max_streak_end": DATES[-1],
            "runs_total": 2,
            "runs_ge_current": 1,
            "avg_run": 3.0,
            "up_day_pct": 75.0,
        }
        assert payload["series"][0] == {"date": DATES[0], "close": 1.0, "streak": 0}
        assert [e["streak"] for e in payload["series"]] == [0, 1, 2, 0, 0, 1, 2, 3, 4]

    def test_max_streak_end_is_the_most_recent_hit(self):
        closes = dict(zip(DATES[:6], [1.0, 2.0, 3.0, 1.0, 2.0, 3.0]))
        payload = build_streaks_payload("SPY", closes, source="ib", scan_time="t")
        assert payload["stats"]["max_streak"] == 2
        assert payload["stats"]["max_streak_end"] == DATES[5]
        assert payload["stats"]["runs_total"] == 2
        # Current streak is 2 and both runs reached 2.
        assert payload["stats"]["runs_ge_current"] == 2

    def test_zero_current_streak_has_no_precedent_count(self):
        closes = dict(zip(DATES[:3], [1.0, 2.0, 1.0]))
        payload = build_streaks_payload("SPY", closes, source="ib", scan_time="t")
        assert payload["current"]["streak"] == 0
        assert payload["current"]["day_change_pct"] == -50.0
        assert payload["stats"]["runs_ge_current"] is None
        assert payload["stats"]["runs_total"] == 1

    def test_missing_shell_when_no_closes(self):
        payload = build_streaks_payload("xyz", {}, source=None, scan_time=None)
        assert payload == {
            "symbol": "XYZ",
            "scan_time": None,
            "source": None,
            "missing": True,
            "count": 0,
            "first_date": None,
            "last_date": None,
            "current": None,
            "stats": None,
            "series": [],
        }

    def test_single_close_is_missing(self):
        payload = build_streaks_payload("SPY", {"2026-01-02": 5.0}, source="uw", scan_time="t")
        assert payload["missing"] is True
        assert payload["series"] == []


# ── parse_yahoo_chart (real captured fixture) ─────────────────────


class TestParseYahooChart:
    def test_fixture_parses_all_sessions(self):
        closes = parse_yahoo_chart(YAHOO_CHART)
        assert len(closes) == 12
        assert closes["2026-08-13"] == 777.8800048828125
        assert closes["2026-08-28"] == 769.3499755859375

    def test_fixture_streaks(self):
        closes = parse_yahoo_chart(YAHOO_CHART)
        rows = closes_to_rows(closes)
        streaks = compute_streaks([r["close"] for r in rows])
        # 08-19 up; 08-21 up; 08-25/26/27 build 1,2,3; 08-28 down.
        assert streaks == [0, 0, 0, 0, 1, 0, 1, 0, 1, 2, 3, 0]

    def test_null_closes_are_skipped(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1754919000, 1755005400],
                        "indicators": {"quote": [{"close": [100.0, None]}]},
                    }
                ]
            }
        }
        closes = parse_yahoo_chart(payload)
        assert list(closes.values()) == [100.0]

    def test_malformed_payloads_yield_empty(self):
        assert parse_yahoo_chart({}) == {}
        assert parse_yahoo_chart({"chart": {"result": []}}) == {}
        assert parse_yahoo_chart({"chart": {"result": [{}]}}) == {}

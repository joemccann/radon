"""Source audit regressions: plotted evidence must match the selected measurement."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.ai_cycle.collectors import SourceError, parse_eia
from scripts.ai_cycle.snapshot import compact_snapshot


def test_reviewed_hardware_backfill_preserves_reporting_cohorts():
    from scripts.ai_cycle.collectors import parse_disclosures
    from scripts.ai_cycle.model import validate_observation

    path = Path(__file__).parents[1] / "ai_cycle/fixtures/issuer_disclosures_reviewed_20260918.json"
    rows = parse_disclosures(json.loads(path.read_text()), "c" * 64, "2026-09-18T23:59:59Z")
    assert len(rows) == 18
    assert all(validate_observation(row) for row in rows)
    nvidia = [row for row in rows if row["series_id"] == "NVDA.data_center_revenue"]
    assert len(nvidia) == 11
    assert len({row["cohort_version"] for row in nvidia}) == 2
    assert len({row["metadata"]["label"] for row in nvidia}) == 2


def test_audit_reports_constant_values_and_missing_dates_without_filling():
    from scripts.ai_cycle.audit import audit_rows
    from scripts.tests.test_ai_cycle_core import observation

    rows = [{**observation(), "period_start": day, "period_end": day, "value": 7}
            for day in ("2026-08-01", "2026-08-03")]
    result = audit_rows(rows)
    assert len(result) == 1
    assert result[0]["missing_dates"] == ["2026-08-02"]
    assert result[0]["distinct_values"] == 1
    assert result[0]["observation_count"] == 2


def test_sec_comparative_periods_before_xbrl_mandate_are_retained():
    from scripts.ai_cycle.collect import source_windows
    from scripts.ai_cycle.store import ObservationStore
    from scripts.tests.test_ai_cycle_core import observation

    assert source_windows("sec", "2006-12-31", "2008-12-31", backfill=True) == [("2006-12-31", "2008-12-31")]
    store = ObservationStore(":memory:")
    store.append_observations([{**observation(), "indicator_id": "F1", "source_id": "sec", "lineage_group": "sec",
                                "period_start": "2006-12-31", "period_end": "2006-12-31"}])
    assert len(store.read_snapshot_observations()) == 1


def test_compaction_matches_full_metric_identity_and_keeps_all_ramp_series():
    metrics = [dict(id=s, source_id="ramp", methodology_version="v2", cohort_version="panel", unit="USD")
               for s in ("spend.median", "spend.top10", "spend.top1", "adoption")]
    def points(series, method, count):
        return [dict(series_id=f"{series}:{method}:panel", source_id="ramp", unit="USD", value=i,
                     date=(datetime(2024, 1, 1) + timedelta(days=i)).isoformat()) for i in range(count)]
    history = points("obsolete", "v1", 100) + sum([points(m["id"], "v2", 4) for m in metrics], [])
    result = compact_snapshot(dict(indicators=[dict(metrics=metrics, history=history)], sources=[]))
    plotted = {p["series_id"] for p in result["indicators"][0]["history"]}
    assert all(f"{m['id']}:v2:panel" in plotted for m in metrics)
    assert result["indicators"][0]["history"][0]["series_id"] == "spend.median:v2:panel"


def test_compaction_exposes_sampling_and_preserves_extreme():
    history = [dict(series_id="x", source_id="eia", unit="MW", value=1000 if i == 501 else 10,
                    date=(datetime(2020, 1, 1) + timedelta(days=i)).isoformat()) for i in range(1000)]
    result = compact_snapshot(dict(indicators=[dict(metrics=[], history=history)], sources=[]))["indicators"][0]
    assert max(p["value"] for p in result["history"]) == 1000
    assert result["history_coverage"][0]["observation_count"] == 1000
    assert result["history_coverage"][0]["displayed_count"] < 1000


def test_partial_eia_day_is_not_a_complete_daily_measurement():
    row = dict(parent="PJM", subba="DOM", period="2026-09-01T01", value=50, **{"value-units": "megawatthours"})
    rows = parse_eia({"response": {"data": [row], "total": 1}}, "a" * 64, "2026-09-03T12:00:00Z")
    assert all(r["metadata"]["complete"] is False for r in rows)
    with pytest.raises(SourceError, match="Duplicate"):
        parse_eia({"response": {"data": [row, row], "total": 2}}, "a" * 64, "2026-09-03T12:00:00Z")


def test_live_ramp_parser_extracts_earlier_history_and_does_not_replay_seed():
    from scripts.ai_cycle.collectors import parse_ramp_html

    props = {"adoptionOverall": [{"date_month": "2023-01-01", "adoption_rate_pct": 7.46}],
             "spendPerEmployee": [{"date_month": "2023-09-01", "median_pepm": 2.5,
                                    "top_10_percent_median_pepm": 70.04, "top_1_percent_median_pepm": 874.89}]}
    html = '<script>self.__next_f.push(' + json.dumps([1, '1:' + json.dumps(props)]) + ')</script>'
    rows = parse_ramp_html(html, "b" * 64, "2026-09-18T00:00:00Z")
    assert len(rows) == 4
    assert min(r["period_start"] for r in rows) == "2023-01-01"
    assert all(r["raw_hash"] == "b" * 64 for r in rows)
    assert all(r["published_at"] is None for r in rows)
    with pytest.raises(SourceError, match="Ramp"):
        parse_ramp_html("<html>changed</html>", "b" * 64, "2026-09-18T00:00:00Z")


@pytest.mark.parametrize("change", ["empty", "wrong_type", "future", "missing_spend", "invalid_json"])
def test_live_ramp_schema_errors_fail_closed(change):
    from scripts.ai_cycle.collectors import parse_ramp_html

    props = {"adoptionOverall": [{"date_month": "2023-01-01", "adoption_rate_pct": 7.46}],
             "spendPerEmployee": [{"date_month": "2023-09-01", "median_pepm": 2.5,
                                    "top_10_percent_median_pepm": 70.04, "top_1_percent_median_pepm": 874.89}]}
    if change == "empty":
        props["adoptionOverall"] = []
    elif change == "wrong_type":
        props["adoptionOverall"] = {}
    elif change == "future":
        props["adoptionOverall"][0]["date_month"] = "2026-09-01"
    elif change == "missing_spend":
        props["spendPerEmployee"][0]["median_pepm"] = None
    content = '"adoptionOverall":[' if change == "invalid_json" else json.dumps(props)
    html = '<script>self.__next_f.push(' + json.dumps([1, content]) + ')</script>'
    with pytest.raises(SourceError, match="Ramp"):
        parse_ramp_html(html, "b" * 64, "2026-09-18T00:00:00Z")

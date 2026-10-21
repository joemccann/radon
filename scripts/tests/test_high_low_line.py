"""Source identity and financial-math contract, before implementation."""
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import fetch_high_low_line as scan

FIXTURE = json.loads((Path(__file__).parent / "fixtures/high_low_line_source_sample.json").read_text())
NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)


def parsed(values, offset=0):
    day = datetime(2003, 1, 2) + timedelta(days=offset)
    rows = []
    for value in values:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day.date().isoformat(), "value": value})
        day += timedelta(days=1)
    return {"rows": rows, "non_observations": []}


def sample():
    return scan.build_output(parsed([20, 15, 22, 9, 30] * 12), parsed([5, 18, 4, 14, 6] * 12), now=NOW)


def test_synthetic_source_schema_preserves_fractional_values_and_literal_zero():
    assert FIXTURE["_fixture"]["synthetic"] is True
    high = scan.parse_stockcharts_history(json.dumps(FIXTURE["highs"]).encode(), "!NEWHINYC", now=NOW)
    low = scan.parse_stockcharts_history(json.dumps(FIXTURE["lows"]).encode(), "!NEWLONYC", now=NOW)
    assert high["rows"][0] == {"date": "2003-01-02", "value": 31.0}
    assert low["rows"][0] == {"date": "2003-01-02", "value": 11.0}
    assert any(row["value"] == 0.0001 for row in high["rows"])
    assert any(row["value"] == 0 for row in low["rows"])
    assert high["non_observations"] == low["non_observations"] == ["2012-10-29", "2012-10-30"]
    assert high["rows"][-1] == {"date": "2026-10-20", "value": 155.0}


def test_wrong_universe_is_rejected():
    data = deepcopy(FIXTURE["highs"])
    data["history"]["symbol"] = "$NYHGH"
    with pytest.raises(ValueError):
        scan.parse_stockcharts_history(json.dumps(data).encode(), "!NEWHINYC", now=NOW)


@pytest.mark.parametrize("bad", [-1, True, "NaN", None])
def test_bad_breadth_values_fail_closed(bad):
    data = deepcopy(FIXTURE["highs"])
    data["history"]["intervals"][0]["close"] = bad
    with pytest.raises(ValueError):
        scan.parse_stockcharts_history(json.dumps(data).encode(), "!NEWHINYC", now=NOW)


def test_wilder_seed_and_recurrence_independent_reference():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    rsi = scan.wilder_rsi(closes)
    assert rsi[:14] == [None] * 14
    assert rsi[14] == pytest.approx(70.4641350211, abs=1e-8)
    assert rsi[15] == pytest.approx(66.2496185536, abs=1e-8)


def test_flat_range_is_unavailable_instead_of_a_false_zero():
    assert scan.stoch_rsi([100.0] * 50) == [None] * 50
    assert scan.stoch_rsi(list(range(50))) == [None] * 50


def test_stoch_warmup_bounds_and_baseline_translation():
    values = [100.0]
    for change in ([1, 2, -1, -3, 2, -2, 4, -1] * 10):
        values.append(values[-1] + change)
    result = scan.stoch_rsi(values)
    assert result[:27] == [None] * 27
    assert result[27] is not None
    assert all(value is None or 0 <= value <= 1 for value in result)
    assert scan.stoch_rsi([x + 50000 for x in values]) == pytest.approx(result)


def test_sar_reversals_and_two_previous_bounds():
    rows = [{"high": value, "low": value, "close": value} for value in [10, 11, 12, 11, 8, 9, 10]]
    result = scan.parabolic_sar(rows)
    assert [pair[0] for pair in result] == [None, 10, 10, 11, 12, 11, 8]
    assert [pair[1] for pair in result] == [None, "rising", "rising", "rising", "falling", "falling", "rising"]


def test_cumulative_decimal_values_and_weekly_sum_not_last_daily_net():
    result = scan.build_output(parsed([19, 0.0001, 30]), parsed([7, 2, 0.0001]), now=NOW)
    assert [r["cumulative"] for r in result["series"]] == [12.0, 10.0001, 40.0]
    assert result["weekly_series"][0]["net"] == 10.0001
    assert result["baseline"] == {"date": "2003-01-02", "value": 0, "convention": "before_first_observation"}


def test_unmatched_high_low_date_is_not_silently_zero_filled():
    with pytest.raises(ValueError):
        scan.build_output(parsed([10, 20]), parsed([1]), now=NOW)


def test_output_contract_has_two_intervals_and_explicit_source_settings():
    data = sample()
    assert data["count"] == len(data["series"]) == 60
    assert data["weekly_count"] == len(data["weekly_series"])
    assert data["current"]["date"] == data["data_date"]
    assert data["source"]["symbols"] == {"highs": "!NEWHINYC", "lows": "!NEWLONYC"}
    assert data["settings"]["sar_start"] == data["settings"]["sar_max"] == 1
    assert data["settings"]["default_interval"] in {"daily", "weekly"}
    assert data["source_last_modified"] is None
    json.dumps(data, allow_nan=False)


def test_prior_session_loss_is_rejected_before_any_database_write(monkeypatch, tmp_path):
    data = sample()
    previous = deepcopy(data)
    previous["series"].append({**previous["series"][-1], "date": "2003-07-01"})
    calls = []
    fake = SimpleNamespace(**{name: lambda *args, _name=name, **kwargs: calls.append(_name) for name in (
        "ensure_no_replica_for_writers", "upsert_high_low_line_rows", "upsert_scan_snapshot", "record_service_health")})
    monkeypatch.setattr(scan, "writer", fake)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "high_low_line.json")
    with pytest.raises(ValueError):
        scan.persist_result(data, previous=previous)
    assert calls == []


def test_unchanged_content_still_updates_snapshot_and_heartbeat(monkeypatch, tmp_path):
    data = sample()
    calls = []
    fake = SimpleNamespace(**{name: lambda *args, _name=name, **kwargs: calls.append(_name) for name in (
        "ensure_no_replica_for_writers", "upsert_high_low_line_rows", "upsert_scan_snapshot", "record_service_health")})
    monkeypatch.setattr(scan, "writer", fake)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "high_low_line.json")
    scan.persist_result(data, previous=deepcopy(data))
    assert "upsert_high_low_line_rows" not in calls
    assert calls.count("upsert_scan_snapshot") == calls.count("record_service_health") == 1


def test_all_synthetic_history_observations_match_declared_recipe():
    """The public fixture is generated data, never an extracted numeric history."""
    import gzip
    fixture = json.loads(gzip.decompress((Path(__file__).parent / "fixtures/high_low_line_synthetic_history.json.gz").read_bytes()))
    assert fixture["_fixture"]["synthetic"] is True
    dates = []
    day = datetime(2003, 1, 2)
    while day.date().isoformat() <= "2026-10-20":
        if day.weekday() < 5 and day.date().isoformat() not in {"2012-10-29", "2012-10-30"}:
            dates.append(day.date().isoformat())
        day += timedelta(days=1)
    assert len(dates) == 6207  # Synthetic weekdays, not a certified exchange calendar.
    for side in ("highs", "lows"):
        assert [r["date"] for r in fixture[side]["rows"]] == dates
        for i, row in enumerate(fixture[side]["rows"]):
            if side == "highs":
                expected, zero, fraction = 31 + (37 * i + 17 * (i // 11)) % 173, 97, 89
            else:
                expected, zero, fraction = 11 + (53 * i + 13 * (i // 7)) % 167, 101, 83
            if i > 0 and i % zero == 0:
                expected = 0.0
            elif i > 0 and i % fraction == 0:
                expected = .0001
            assert row["value"] == expected
        by_date = {r["date"]: r["value"] for r in fixture[side]["rows"]}
        for interval in FIXTURE[side]["history"]["intervals"]:
            key = interval["end"]["time"][:10]
            expected = None if key in {"2012-10-29", "2012-10-30"} else by_date[key]
            assert [interval[field] for field in ("open", "high", "low", "close")] == [expected] * 4
        assert FIXTURE[side]["quote"]["close"] == fixture[side]["rows"][-1]["value"]

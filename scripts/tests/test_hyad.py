"""HYAD — High Yield Bond Cumulative Advance-Decline Line. Red tests against
docs/indicators/hyad.md.

Fixture facts are derived by INSPECTING the checked-in fixture directly:
  - fixtures/hyad_sample.json — real FINRA MarketActivityAggregates transport
    envelope. `returnBody.data` is a JSON STRING (double json.loads quirk).
    Decoded it holds 210 rows = 10 trading days 2026-08-10..2026-08-21 x 3
    bondTypes (CORP, CORP_144A, AGENCY) x 7 dataTypes. HY is column `fieldC`
    on CORP and CORP_144A; AGENCY reuses the columns for different agencies
    and is ignored. Every expectation below is recomputed from the fixture's
    own fields inside the test, never from mental arithmetic. Known anchors:
    2026-08-21 merged adv 1227 / dec 1504; mini-cum over the window -2535.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_hyad import (
    build_output,
    build_series,
    merge_bond_types,
    parse_breadth_rows,
    persist_result,
    validate_day,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0056_hyad.sql"

ENVELOPE = json.loads((FIXTURES / "hyad_sample.json").read_text())

HY_BOND_TYPES = ("CORP", "CORP_144A")
COUNT_KEYS = {
    "Advances": "advances",
    "Declines": "declines",
    "Unchanged": "unchanged",
    "Total Issues Traded": "total",
}

SCAN_TIME = datetime.now(timezone.utc).isoformat()
# One import-time anchor for every relative date in this module: mixing
# import-time and call-time clocks flakes when a CI run crosses UTC midnight.
_TODAY = date.today()


def _raw_rows() -> list[dict]:
    """Independent double-decode of the fixture for expectation building."""
    data = ENVELOPE["returnBody"]["data"]
    assert isinstance(data, str)  # the transport quirk the parser must handle
    return json.loads(data)


def _expected_merged() -> list[dict]:
    """CORP + CORP_144A HY (fieldC) counts summed per date, ascending."""
    by_date: dict[str, dict[str, int]] = defaultdict(dict)
    for row in _raw_rows():
        if row["bondType"] not in HY_BOND_TYPES:
            continue
        key = COUNT_KEYS.get(row["dataTypeDescription"])
        if key is None:
            continue
        day = by_date[row["originalTradeReportedDate"]]
        day[key] = day.get(key, 0) + int(row["fieldC"])
    return [{"date": d, **by_date[d]} for d in sorted(by_date)]


def _expected_cums() -> list[int]:
    cum = 0
    cums = []
    for row in _expected_merged():
        cum += row["advances"] - row["declines"]
        cums.append(cum)
    return cums


FIXTURE_DATES = [row["date"] for row in _expected_merged()]
LAST_DATE = FIXTURE_DATES[-1]


class TestFixtureAnchors:
    """Sanity on the fixture itself before it is used to derive expectations."""

    def test_window_and_known_anchor_values(self):
        assert FIXTURE_DATES[0] == "2026-08-10"
        assert LAST_DATE == "2026-08-21"
        assert len(FIXTURE_DATES) == 10
        last = _expected_merged()[-1]
        assert last["advances"] == 1227
        assert last["declines"] == 1504
        assert _expected_cums()[-1] == -2535


class TestParseBreadthRows:
    def test_double_decodes_the_transport_envelope(self):
        # returnBody.data is a JSON string; the parser owns the second loads.
        rows = parse_breadth_rows(ENVELOPE)
        assert len(rows) == 20  # 10 dates x 2 HY bondTypes
        assert {row["date"] for row in rows} == set(FIXTURE_DATES)

    def test_filters_agency_rows(self):
        rows = parse_breadth_rows(ENVELOPE)
        assert {row["bond_type"] for row in rows} == set(HY_BOND_TYPES)

    def test_extracts_the_hy_field_c_counts_per_date_and_bond_type(self):
        expected: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
        for raw in _raw_rows():
            key = COUNT_KEYS.get(raw["dataTypeDescription"])
            if key is None or raw["bondType"] not in HY_BOND_TYPES:
                continue
            expected[(raw["originalTradeReportedDate"], raw["bondType"])][key] = int(raw["fieldC"])
        for row in parse_breadth_rows(ENVELOPE):
            counts = expected[(row["date"], row["bond_type"])]
            assert row["advances"] == counts["advances"]
            assert row["declines"] == counts["declines"]
            assert row["unchanged"] == counts["unchanged"]
            assert row["total"] == counts["total"]


class TestMergeBondTypes:
    def test_sums_corp_and_corp_144a_per_date(self):
        merged = merge_bond_types(parse_breadth_rows(ENVELOPE))
        assert [row["date"] for row in merged] == FIXTURE_DATES
        by_date = {row["date"]: row for row in merged}
        for expected in _expected_merged():
            row = by_date[expected["date"]]
            for key in ("advances", "declines", "unchanged", "total"):
                assert row[key] == expected[key]

    def test_last_date_matches_the_research_anchors(self):
        merged = merge_bond_types(parse_breadth_rows(ENVELOPE))
        assert merged[-1]["date"] == LAST_DATE
        assert merged[-1]["advances"] == 1227
        assert merged[-1]["declines"] == 1504


class TestValidateDay:
    def _row(self, **overrides) -> dict:
        row = dict(_expected_merged()[-1])
        row.update(overrides)
        return row

    def test_fixture_rows_pass(self):
        for row in _expected_merged():
            validate_day(row)

    def test_negative_count_raises(self):
        with pytest.raises(ValueError):
            validate_day(self._row(advances=-1))
        with pytest.raises(ValueError):
            validate_day(self._row(declines=-1))
        with pytest.raises(ValueError):
            validate_day(self._row(unchanged=-1))

    def test_counts_summing_exactly_to_total_pass(self):
        # The spec pins <= — equality is valid, only strictly-greater raises.
        row = self._row()
        row["total"] = row["advances"] + row["declines"] + row["unchanged"]
        validate_day(row)

    def test_counts_exceeding_total_raise(self):
        row = self._row()
        row["total"] = row["advances"] + row["declines"] + row["unchanged"] - 1
        with pytest.raises(ValueError):
            validate_day(row)


def _synthetic_rows(count: int) -> list[dict]:
    """`count` merged rows ending yesterday, each with net = +1 (adv=dec+1)."""
    rows = []
    for i in range(count):
        day = (_TODAY - timedelta(days=count - i)).isoformat()
        rows.append({"date": day, "advances": 901, "declines": 900, "unchanged": 50, "total": 2000})
    return rows


class TestBuildSeries:
    def test_fixture_net_and_cum(self):
        series = build_series(merge_bond_types(parse_breadth_rows(ENVELOPE)), {})
        assert [point["date"] for point in series] == FIXTURE_DATES
        merged = _expected_merged()
        for point, row, cum in zip(series, merged, _expected_cums()):
            assert point["net"] == row["advances"] - row["declines"]
            assert point["cum"] == cum
        assert series[-1]["cum"] == -2535

    def test_ma_windows_are_null_until_enough_points(self):
        series = build_series(_synthetic_rows(60), {})
        cums = [point["cum"] for point in series]
        assert cums == list(range(1, 61))  # net +1 per day from the fixture-free ramp
        assert series[19]["ma21"] is None
        assert series[20]["ma21"] == pytest.approx(sum(cums[0:21]) / 21)
        assert series[48]["ma50"] is None
        assert series[49]["ma50"] == pytest.approx(sum(cums[0:50]) / 50)
        assert series[-1]["ma21"] == pytest.approx(sum(cums[-21:]) / 21)
        assert series[-1]["ma50"] == pytest.approx(sum(cums[-50:]) / 50)

    def test_ten_fixture_points_have_no_ma(self):
        series = build_series(merge_bond_types(parse_breadth_rows(ENVELOPE)), {})
        assert all(point["ma21"] is None for point in series)
        assert all(point["ma50"] is None for point in series)

    def test_joins_spx_close_and_leaves_null_on_calendar_mismatch(self):
        rows = _synthetic_rows(3)
        spx_by_date = {rows[0]["date"]: 6411.37, rows[2]["date"]: 6449.8}
        series = build_series(rows, spx_by_date)
        assert series[0]["spx_close"] == 6411.37
        assert series[1]["spx_close"] is None
        assert series[2]["spx_close"] == 6449.8

    def test_empty_window_guard_raises(self):
        # Zero rows is a retryable soft failure, never a payload.
        with pytest.raises(RuntimeError):
            build_series([], {})


DATA_DATE = (_TODAY - timedelta(days=1)).isoformat()


def _build_payload(count: int = 60) -> dict:
    rows = _synthetic_rows(count)
    return build_output(rows=rows, spx_by_date={}, scan_time=SCAN_TIME)


class TestBuildOutput:
    def test_payload_contract_keys(self):
        payload = _build_payload()
        assert {"scan_time", "data_date", "current", "series"} <= set(payload)
        assert payload["scan_time"] == SCAN_TIME
        assert payload["data_date"] == DATA_DATE

    def test_current_row_carries_counts_and_derived_values(self):
        payload = _build_payload()
        current = payload["current"]
        assert current["date"] == DATA_DATE
        assert current["advances"] == 901
        assert current["declines"] == 900
        assert current["unchanged"] == 50
        assert current["total"] == 2000
        assert current["net"] == 1
        assert current["cum"] == 60
        assert current["ma21"] == payload["series"][-1]["ma21"]
        assert current["ma50"] == payload["series"][-1]["ma50"]

    def test_series_is_ascending_with_point_contract_keys(self):
        series = _build_payload()["series"]
        assert [point["date"] for point in series] == sorted(point["date"] for point in series)
        assert {"date", "net", "cum", "ma21", "ma50", "spx_close"} <= set(series[0])

    def test_fixture_payload_ends_on_the_anchor_cum(self):
        rows = merge_bond_types(parse_breadth_rows(ENVELOPE))
        payload = build_output(rows=rows, spx_by_date={}, scan_time=SCAN_TIME)
        assert payload["data_date"] == LAST_DATE
        assert payload["current"]["cum"] == -2535
        assert payload["current"]["ma21"] is None
        assert payload["current"]["ma50"] is None


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_hyad as fh

    calls: list[tuple] = []
    monkeypatch.setattr(fh, "HYAD_JSON", tmp_path / "hyad.json")
    monkeypatch.setattr(fh.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(
        fh.writer,
        "upsert_hyad_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        fh.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        fh.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


class TestPersistResult:
    def test_changed_day_writes_in_order(self, persist_calls):
        import fetch_hyad as fh

        persist_result(_build_payload(), _synthetic_rows(2))
        assert persist_calls == [
            ("guard",),
            ("rows", 2),
            ("snapshot", "hy-ad"),
            ("health", "hy-ad", "ok"),
        ]
        fallback = json.loads(fh.HYAD_JSON.read_text())
        assert fallback["data_date"] == DATA_DATE

    def test_unchanged_day_heartbeat_skips_row_upsert_only(self, persist_calls):
        # Weekend / already-stored runs: no hyad_history upsert, but the
        # snapshot AND the service-health heartbeat are still written.
        persist_result(_build_payload(), [])
        kinds = [call[0] for call in persist_calls]
        assert "rows" not in kinds
        assert persist_calls[0] == ("guard",)
        assert ("snapshot", "hy-ad") in persist_calls
        assert ("health", "hy-ad", "ok") in persist_calls


# TEST_AUDIT T-133: `run()` was executed by no test (see test_divyield.py).
class TestRun:
    def test_only_new_or_revised_days_are_written(self, persist_calls, monkeypatch):
        import fetch_hyad as fh

        window = _synthetic_rows(3)
        stored = [dict(window[0]), dict(window[1])]
        monkeypatch.setattr(fh, "fetch_window", lambda start, end: window)
        monkeypatch.setattr(fh, "_turso_history", lambda: stored)
        monkeypatch.setattr(fh, "_spx_by_date", lambda: {})

        payload = fh.run()

        assert ("rows", 1) in persist_calls
        assert ("health", "hy-ad", "ok") in persist_calls
        assert payload["data_date"] == window[-1]["date"]

    def test_a_revised_stored_day_is_rewritten(self, persist_calls, monkeypatch):
        import fetch_hyad as fh

        window = _synthetic_rows(2)
        stored = [dict(window[0]), {**window[1], "advances": window[1]["advances"] - 5}]
        monkeypatch.setattr(fh, "fetch_window", lambda start, end: window)
        monkeypatch.setattr(fh, "_turso_history", lambda: stored)
        monkeypatch.setattr(fh, "_spx_by_date", lambda: {})

        fh.run()

        assert ("rows", 1) in persist_calls

    def test_an_empty_window_is_a_retryable_failure_not_a_heartbeat(self, persist_calls, monkeypatch):
        import fetch_hyad as fh

        monkeypatch.setattr(fh, "fetch_window", lambda start, end: [])
        with pytest.raises(RuntimeError):
            fh.run()
        assert persist_calls == []


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
        )
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_56(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [56]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [56]

    def test_schema_columns_and_date_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(hyad_history)"))
        assert [r[1] for r in info] == [
            "date", "advances", "declines", "unchanged", "total", "recorded_at",
        ]
        pk_by_column = {r[1]: r[5] for r in info}
        assert pk_by_column["date"] == 1

    def test_date_desc_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(hyad_history)")}
        assert "idx_hyad_history_date_desc" in names

    # TEST_AUDIT T-132: exercises the REAL writer, not a dead SQL constant.
    def test_upsert_is_idempotent_per_date(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        writer.upsert_hyad_rows(
            [{"date": DATA_DATE, "advances": 1200, "declines": 1500, "unchanged": 70, "total": 3100}],
            recorded_at="r1",
        )
        writer.upsert_hyad_rows(
            [{"date": DATA_DATE, "advances": 1227, "declines": 1504, "unchanged": 69, "total": 3163}],
            recorded_at="r2",
        )
        rows = list(
            db.execute(
                "SELECT date, advances, declines, unchanged, total, recorded_at FROM hyad_history"
            )
        )
        assert rows == [(DATA_DATE, 1227, 1504, 69, 3163, "r2")]

    def test_writer_arity(self):
        from db import writer

        parameters = list(inspect.signature(writer.upsert_hyad_rows).parameters)
        assert parameters == ["rows", "recorded_at"]

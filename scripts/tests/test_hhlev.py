"""HHLEV — US Household Leverage (percent of net worth). Red tests against
docs/indicators/hhlev.md.

Fixture facts are derived by INSPECTING the checked-in fixtures directly:
  - fixtures/hhlev_sample.csv — full 323-line fredgraph response, header
    `observation_date,TLBSHNO,TNWBSHNO`, 322 data rows of which 304 quarters
    carry both fields; the 18 empty-sentinel rows (both fields empty) span
    1946-01-01..1951-07-01 (annual-only early Z.1 data). Anchors recomputed
    from the fixture's own fields: 1945-10-01 → 3.79, 1960-01-01 → 10.26,
    2009-01-01 → series max 24.26, 2026-01-01 (latest) → 11.78.
  - fixtures/hhlev_api_observations_sample.json — keyed-FRED-API fallback
    shape, one observations block per series id, string values. Its
    2026-01-01 values match the CSV fixture exactly (21560050 / 182979889),
    so the two parse paths are cross-checked against each other. The keyed
    API's missing sentinel is the string "." (fredgraph CSVs use an empty
    field); the sentinel-skip test injects "." into a deep copy.

Every expectation below is recomputed from the fixture's own fields inside
the test, never from mental arithmetic.
"""

from __future__ import annotations

import copy
import csv
import inspect
import io
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_hhlev import (
    MIN_QUARTERS,
    build_output,
    build_series,
    compute_leverage,
    ensure_plausible_series,
    parse_api_observations,
    parse_fredgraph_csv,
    persist_result,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0057_hhlev.sql"

CSV_TEXT = (FIXTURES / "hhlev_sample.csv").read_text()
API_FIXTURE = json.loads((FIXTURES / "hhlev_api_observations_sample.json").read_text())

SCAN_TIME = datetime.now(timezone.utc).isoformat()
# Passthrough string; window-relative so the test never rots (the real value
# is FRED's series last_updated, or the latest observation date on the CSV path).
SOURCE_LAST_MODIFIED = (date.today() - timedelta(days=30)).isoformat()


def _independent_csv_rows() -> list[dict]:
    """Independent decode of the CSV fixture for expectation building."""
    rows = []
    for raw in csv.DictReader(io.StringIO(CSV_TEXT)):
        liabilities, net_worth = raw["TLBSHNO"], raw["TNWBSHNO"]
        if liabilities in ("", ".") or net_worth in ("", "."):
            continue
        rows.append(
            {
                "date": raw["observation_date"],
                "liabilities_musd": float(liabilities),
                "net_worth_musd": float(net_worth),
            }
        )
    return rows


def _leverage(row: dict) -> float:
    return 100.0 * row["liabilities_musd"] / row["net_worth_musd"]


CSV_ROWS = _independent_csv_rows()
CSV_BY_DATE = {row["date"]: row for row in CSV_ROWS}
FIXTURE_DATES = [row["date"] for row in CSV_ROWS]
LATEST_DATE = FIXTURE_DATES[-1]

# leverage_pct may be carried at full precision or rounded to 2 decimals
# (the spec's payload example shows 11.78); both satisfy this tolerance.
LEV_ABS = 5e-3


class TestFixtureAnchors:
    """Sanity on the fixture itself before it is used to derive expectations."""

    def test_populated_quarter_count_and_window(self):
        assert len(CSV_ROWS) == 304
        assert FIXTURE_DATES[0] == "1945-10-01"
        assert LATEST_DATE == "2026-01-01"
        assert FIXTURE_DATES == sorted(FIXTURE_DATES)

    def test_known_anchor_ratios(self):
        assert round(_leverage(CSV_BY_DATE["1945-10-01"]), 2) == 3.79
        assert round(_leverage(CSV_BY_DATE["1960-01-01"]), 2) == 10.26
        assert round(_leverage(CSV_BY_DATE["2009-01-01"]), 2) == 24.26
        assert round(_leverage(CSV_BY_DATE[LATEST_DATE]), 2) == 11.78

    def test_2009q1_is_the_series_max(self):
        peak = max(CSV_ROWS, key=_leverage)
        assert peak["date"] == "2009-01-01"

    def test_latest_quarter_component_levels(self):
        latest = CSV_BY_DATE[LATEST_DATE]
        assert latest["liabilities_musd"] == 21560050
        assert latest["net_worth_musd"] == 182979889


class TestParseFredgraphCsv:
    def test_parses_304_populated_quarters(self):
        rows = parse_fredgraph_csv(CSV_TEXT)
        assert len(rows) == 304
        assert [row["date"] for row in rows] == FIXTURE_DATES

    def test_row_values_match_an_independent_decode(self):
        for row, expected in zip(parse_fredgraph_csv(CSV_TEXT), CSV_ROWS):
            assert row["date"] == expected["date"]
            assert row["liabilities_musd"] == pytest.approx(expected["liabilities_musd"])
            assert row["net_worth_musd"] == pytest.approx(expected["net_worth_musd"])

    def test_skips_the_empty_field_sentinel_rows(self):
        # 1946Q1..1951Q3 rows are `date,,` in fredgraph CSVs — never parsed.
        dates = {row["date"] for row in parse_fredgraph_csv(CSV_TEXT)}
        assert "1946-01-01" not in dates
        assert "1951-07-01" not in dates
        assert "1946-10-01" in dates  # populated island inside the sparse era

    def test_requires_both_columns(self):
        text = (
            "observation_date,TLBSHNO,TNWBSHNO\n"
            "2000-01-01,100,\n"
            "2000-04-01,,200\n"
            "2000-07-01,.,200\n"
            "2000-10-01,100,200\n"
        )
        rows = parse_fredgraph_csv(text)
        assert [row["date"] for row in rows] == ["2000-10-01"]


class TestComputeLeverage:
    def test_first_quarter_from_fixture_fields(self):
        first = CSV_BY_DATE["1945-10-01"]
        expected = 100.0 * first["liabilities_musd"] / first["net_worth_musd"]
        result = compute_leverage(first["liabilities_musd"], first["net_worth_musd"])
        assert result == pytest.approx(expected, abs=LEV_ABS)
        assert round(result, 2) == 3.79

    def test_anchor_quarters(self):
        for anchor_date, rounded in (
            ("1960-01-01", 10.26),
            ("2009-01-01", 24.26),
            (LATEST_DATE, 11.78),
        ):
            row = CSV_BY_DATE[anchor_date]
            result = compute_leverage(row["liabilities_musd"], row["net_worth_musd"])
            assert result == pytest.approx(_leverage(row), abs=LEV_ABS)
            assert round(result, 2) == rounded


class TestParseApiObservations:
    def test_joins_both_series_on_date(self):
        rows = parse_api_observations(API_FIXTURE)
        expected_dates = [
            obs["date"] for obs in API_FIXTURE["TLBSHNO"]["observations"]
        ]
        assert [row["date"] for row in rows] == expected_dates
        by_date = {
            series: {obs["date"]: float(obs["value"]) for obs in API_FIXTURE[series]["observations"]}
            for series in ("TLBSHNO", "TNWBSHNO")
        }
        for row in rows:
            assert row["liabilities_musd"] == pytest.approx(by_date["TLBSHNO"][row["date"]])
            assert row["net_worth_musd"] == pytest.approx(by_date["TNWBSHNO"][row["date"]])

    def test_latest_row_matches_the_csv_fixture(self):
        # The two transports must agree: same Z.1 vintage, same latest quarter.
        latest = parse_api_observations(API_FIXTURE)[-1]
        csv_latest = CSV_BY_DATE[LATEST_DATE]
        assert latest["date"] == LATEST_DATE
        assert latest["liabilities_musd"] == pytest.approx(csv_latest["liabilities_musd"])
        assert latest["net_worth_musd"] == pytest.approx(csv_latest["net_worth_musd"])

    def test_skips_the_dot_sentinel(self):
        tampered = copy.deepcopy(API_FIXTURE)
        tampered["TLBSHNO"]["observations"][1]["value"] = "."
        dropped_date = tampered["TLBSHNO"]["observations"][1]["date"]
        rows = parse_api_observations(tampered)
        assert len(rows) == len(API_FIXTURE["TLBSHNO"]["observations"]) - 1
        assert dropped_date not in {row["date"] for row in rows}

    def test_requires_both_series_for_a_date(self):
        tampered = copy.deepcopy(API_FIXTURE)
        dropped = tampered["TNWBSHNO"]["observations"].pop(0)
        rows = parse_api_observations(tampered)
        assert dropped["date"] not in {row["date"] for row in rows}


def _synthetic_rows(count: int, latest_ratio: float = 10.0) -> list[dict]:
    """`count` quarterly rows ending near today, ratio 10 except the latest."""
    rows = []
    today = date.today()
    for i in range(count):
        day = (today - timedelta(days=(count - i) * 91)).isoformat()
        ratio = latest_ratio if i == count - 1 else 10.0
        rows.append(
            {"date": day, "liabilities_musd": ratio * 1000.0, "net_worth_musd": 100000.0}
        )
    return rows


class TestEnsurePlausibleSeries:
    def test_min_quarters_constant(self):
        assert MIN_QUARTERS == 250

    def test_fixture_rows_pass(self):
        ensure_plausible_series(parse_fredgraph_csv(CSV_TEXT))

    def test_fewer_than_250_rows_raises(self):
        # Retryable soft failure: never persist a truncated download.
        with pytest.raises(RuntimeError):
            ensure_plausible_series(_synthetic_rows(249))
        ensure_plausible_series(_synthetic_rows(250))

    def test_latest_ratio_outside_2_to_40_raises(self):
        with pytest.raises(RuntimeError):
            ensure_plausible_series(_synthetic_rows(250, latest_ratio=41.0))
        with pytest.raises(RuntimeError):
            ensure_plausible_series(_synthetic_rows(250, latest_ratio=1.99))
        # Band edges are plausible; only strictly-outside raises.
        ensure_plausible_series(_synthetic_rows(250, latest_ratio=2.0))
        ensure_plausible_series(_synthetic_rows(250, latest_ratio=40.0))

    def test_guard_is_scoped_to_the_latest_quarter(self):
        rows = _synthetic_rows(250)
        rows[0]["liabilities_musd"] = 45000.0  # historic 45% — Z.1 revisions own history
        ensure_plausible_series(rows)


class TestBuildSeries:
    def test_ascending_leverage_series_from_the_fixture(self):
        series = build_series(parse_fredgraph_csv(CSV_TEXT))
        assert [point["date"] for point in series] == FIXTURE_DATES
        for point, row in zip(series, CSV_ROWS):
            assert point["leverage_pct"] == pytest.approx(_leverage(row), abs=LEV_ABS)

    def test_series_points_carry_leverage_only(self):
        # Components live in `current` and Turso, never in the series payload.
        series = build_series(parse_fredgraph_csv(CSV_TEXT))
        assert set(series[0]) == {"date", "leverage_pct"}

    def test_anchor_points(self):
        series = build_series(parse_fredgraph_csv(CSV_TEXT))
        by_date = {point["date"]: point["leverage_pct"] for point in series}
        assert round(by_date["1960-01-01"], 2) == 10.26
        assert round(by_date[LATEST_DATE], 2) == 11.78
        peak_date = max(by_date, key=lambda d: by_date[d])
        assert peak_date == "2009-01-01"
        assert round(by_date[peak_date], 2) == 24.26


def _build_payload() -> dict:
    return build_output(
        rows=parse_fredgraph_csv(CSV_TEXT),
        scan_time=SCAN_TIME,
        source_last_modified=SOURCE_LAST_MODIFIED,
    )


class TestBuildOutput:
    def test_payload_contract_keys(self):
        payload = _build_payload()
        assert {"scan_time", "source_last_modified", "data_date", "current", "series"} <= set(payload)
        assert payload["scan_time"] == SCAN_TIME
        assert payload["source_last_modified"] == SOURCE_LAST_MODIFIED
        assert payload["data_date"] == LATEST_DATE

    def test_current_carries_components_and_the_latest_ratio(self):
        current = _build_payload()["current"]
        latest = CSV_BY_DATE[LATEST_DATE]
        assert current["date"] == LATEST_DATE
        assert current["liabilities_musd"] == pytest.approx(latest["liabilities_musd"])
        assert current["net_worth_musd"] == pytest.approx(latest["net_worth_musd"])
        assert current["leverage_pct"] == pytest.approx(_leverage(latest), abs=LEV_ABS)
        assert round(current["leverage_pct"], 2) == 11.78

    def test_series_is_ascending_with_all_304_quarters(self):
        series = _build_payload()["series"]
        assert len(series) == 304
        assert [point["date"] for point in series] == sorted(point["date"] for point in series)
        assert series[-1]["date"] == LATEST_DATE


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_hhlev as fh

    calls: list[tuple] = []
    monkeypatch.setattr(fh, "HHLEV_JSON", tmp_path / "hhlev.json")
    monkeypatch.setattr(fh.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(
        fh.writer,
        "upsert_hhlev_rows",
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
    def test_writes_in_order_with_the_full_history(self, persist_calls):
        import fetch_hhlev as fh

        rows = parse_fredgraph_csv(CSV_TEXT)
        persist_result(_build_payload(), rows)
        assert persist_calls == [
            ("guard",),
            ("rows", 304),
            ("snapshot", "hhlev"),
            ("health", "hhlev", "ok"),
        ]
        fallback = json.loads(fh.HHLEV_JSON.read_text())
        assert fallback["data_date"] == LATEST_DATE

    def test_every_run_reupserts_the_entire_series(self, persist_calls):
        # Z.1 revises full history each release: no unchanged-day fast path,
        # no diffing — a repeat run re-upserts all 304 rows again.
        rows = parse_fredgraph_csv(CSV_TEXT)
        persist_result(_build_payload(), rows)
        persist_result(_build_payload(), rows)
        assert [call for call in persist_calls if call[0] == "rows"] == [
            ("rows", 304),
            ("rows", 304),
        ]


DATA_DATE = (date.today() - timedelta(days=1)).isoformat()


# TEST_AUDIT T-133: `run()` was executed by no test (see test_divyield.py).
class TestRun:
    def test_every_run_writes_the_full_revised_series(self, persist_calls, monkeypatch):
        import fetch_hhlev as fh

        rows = parse_fredgraph_csv(CSV_TEXT)
        monkeypatch.setattr(fh, "fetch_rows", lambda: (rows, "2026-06-12T12:00:00Z"))

        payload = fh.run()

        assert ("rows", len(rows)) in persist_calls
        assert ("health", "hhlev", "ok") in persist_calls
        assert payload["data_date"] == LATEST_DATE
        assert "2026-06-12T12:00:00Z" in json.dumps(payload)


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

    def test_migration_registers_version_57(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [57]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [57]

    def test_schema_columns_and_date_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(hhlev_history)"))
        assert [r[1] for r in info] == [
            "date", "leverage_pct", "liabilities_musd", "net_worth_musd", "recorded_at",
        ]
        pk_by_column = {r[1]: r[5] for r in info}
        assert pk_by_column["date"] == 1

    def test_date_desc_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(hhlev_history)")}
        assert "idx_hhlev_history_date_desc" in names

    # TEST_AUDIT T-132: exercises the REAL writer, not a dead SQL constant.
    def test_upsert_is_idempotent_per_date(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        writer.upsert_hhlev_rows(
            [{"date": DATA_DATE, "leverage_pct": 11.5, "liabilities_musd": 21000000.0, "net_worth_musd": 182000000.0}],
            recorded_at="r1",
        )
        writer.upsert_hhlev_rows(
            [{"date": DATA_DATE, "leverage_pct": 11.78, "liabilities_musd": 21560050.0, "net_worth_musd": 182979889.0}],
            recorded_at="r2",
        )
        rows = list(
            db.execute(
                "SELECT date, leverage_pct, liabilities_musd, net_worth_musd, recorded_at"
                " FROM hhlev_history"
            )
        )
        assert rows == [(DATA_DATE, 11.78, 21560050.0, 182979889.0, "r2")]

    def test_writer_arity(self):
        from db import writer

        parameters = list(inspect.signature(writer.upsert_hhlev_rows).parameters)
        assert parameters == ["rows", "recorded_at"]

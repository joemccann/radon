"""DIVYIELD — percent of S&P 500 stocks with trailing dividend yield above the
10-Year Treasury yield. Red tests against docs/indicators/divyield.md.

Fixture facts are derived by INSPECTING the checked-in fixtures directly:
  - fixtures/divyield_constituents_sample.csv — header + 30 of 503 real rows
    from the github datasets constituents CSV (Symbol column, MMM..AWK).
  - fixtures/divyield_quote_sample.json — real XOM Yahoo v8 chart response;
    events.dividends holds FOUR events of 1.03 each (TTM sum 4.12) and
    meta.regularMarketPrice is 165.11, so the trailing yield is
    100 * 4.12 / 165.11 ≈ 2.4953%. The test recomputes the expectation from
    the fixture's own fields, never from mental arithmetic.
"""

from __future__ import annotations

import copy
import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_divyield import (
    FETCH_TIMEOUT_S,
    MIN_CONSTITUENTS,
    build_output,
    compute_pct_above,
    compute_trailing_yield,
    ensure_plausible_payload,
    is_unchanged_day,
    parse_constituents_csv,
    persist_result,
    sweep_yields,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0055_divyield.sql"

CONSTITUENTS_CSV = (FIXTURES / "divyield_constituents_sample.csv").read_text()
QUOTE = json.loads((FIXTURES / "divyield_quote_sample.json").read_text())

# Window-relative dates only — hardcoded dates rot in fixtures and tests.
# One import-time anchor for every relative date in this module: mixing
# import-time and call-time clocks flakes when a CI run crosses UTC midnight.
_TODAY = date.today()
DATA_DATE = (_TODAY - timedelta(days=1)).isoformat()
Y10_DATE = DATA_DATE
Y10 = 4.74
SCAN_TIME = datetime.now(timezone.utc).isoformat()


def _quote_result(chart: dict) -> dict:
    return chart["chart"]["result"][0]


def _yields_by_ticker(total: int = 503, above: int = 19, y10: float = Y10) -> dict[str, float]:
    """Synthetic per-ticker trailing yields: `above` strictly above y10."""
    yields: dict[str, float] = {}
    for i in range(above):
        yields[f"AB{i:03d}"] = y10 + 1.0 + i * 0.01
    for i in range(total - above):
        yields[f"BL{i:03d}"] = 1.0
    return yields


def _current_row() -> dict:
    return {
        "date": DATA_DATE,
        "pct_above": 100.0 * 19 / 503,
        "count_above": 19,
        "total": 503,
        "y10": Y10,
        "approximate": 0,
    }


class TestConstants:
    def test_min_plausible_constituent_count(self):
        assert MIN_CONSTITUENTS == 400


class TestParseConstituentsCsv:
    def test_parses_30_tickers_from_the_fixture(self):
        tickers = parse_constituents_csv(CONSTITUENTS_CSV)
        assert len(tickers) == 30
        assert tickers[0] == "MMM"
        assert "GOOGL" in tickers
        assert "AWK" in tickers

    def test_all_tickers_are_canonical_dash_form(self):
        for ticker in parse_constituents_csv(CONSTITUENTS_CSV):
            assert "." not in ticker
            assert ticker == ticker.upper()

    def test_dotted_class_shares_normalize_to_dash(self):
        csv_text = "Symbol,Security\nBRK.B,Berkshire Hathaway\nBF.B,Brown-Forman\n"
        assert parse_constituents_csv(csv_text) == ["BRK-B", "BF-B"]


class TestComputeTrailingYield:
    def test_fixture_xom_trailing_yield(self):
        result = _quote_result(QUOTE)
        ttm = sum(d["amount"] for d in result["events"]["dividends"].values())
        price = result["meta"]["regularMarketPrice"]
        # Sanity on the fixture itself: four 1.03 events, price 165.11.
        assert len(result["events"]["dividends"]) == 4
        assert ttm == pytest.approx(4.12)
        assert price == 165.11
        expected = 100.0 * ttm / price
        assert expected == pytest.approx(2.4953, abs=5e-4)
        assert compute_trailing_yield(QUOTE) == pytest.approx(expected)

    def test_no_dividend_events_is_zero_yield(self):
        chart = copy.deepcopy(QUOTE)
        del _quote_result(chart)["events"]
        assert compute_trailing_yield(chart) == 0.0

    def test_missing_price_is_invalid(self):
        chart = copy.deepcopy(QUOTE)
        del _quote_result(chart)["meta"]["regularMarketPrice"]
        assert compute_trailing_yield(chart) is None


class TestComputePctAbove:
    def test_synthetic_set(self):
        result = compute_pct_above([5.5, 4.0, 3.0, 6.1], 4.74)
        assert result["count_above"] == 2
        assert result["total"] == 4
        assert result["pct_above"] == pytest.approx(50.0)

    def test_boundary_yield_equal_to_y10_is_not_above(self):
        # Strict inequality is pinned by the spec: yield == y10 does NOT count.
        result = compute_pct_above([4.74, 4.74, 5.0], 4.74)
        assert result["count_above"] == 1
        assert result["total"] == 3
        boundary_only = compute_pct_above([4.74], 4.74)
        assert boundary_only["count_above"] == 0
        assert boundary_only["pct_above"] == pytest.approx(0.0)

    def test_19_of_503_matches_the_payload_example(self):
        result = compute_pct_above(list(_yields_by_ticker().values()), Y10)
        assert result["count_above"] == 19
        assert result["total"] == 503
        assert result["pct_above"] == pytest.approx(100.0 * 19 / 503, abs=0.005)


class TestDegenerateGuard:
    def test_healthy_sweep_passes(self):
        ensure_plausible_payload(503, 503)

    def test_fewer_than_400_constituents_raises(self):
        with pytest.raises(RuntimeError):
            ensure_plausible_payload(399, 399)

    def test_too_many_quote_errors_raises(self):
        # Valid yields below 80% of constituents (402/503 = 79.9%) is a soft
        # failure; 403/503 = 80.1% is acceptable.
        with pytest.raises(RuntimeError):
            ensure_plausible_payload(503, 402)
        ensure_plausible_payload(503, 403)


class TestUnchangedDay:
    def test_same_date_count_and_total_is_unchanged(self):
        assert is_unchanged_day(_current_row(), _current_row()) is True

    def test_equality_is_on_date_count_total_only(self):
        # A same-day y10 revision with identical counts is still the fast path.
        latest = {**_current_row(), "y10": 4.70}
        assert is_unchanged_day(_current_row(), latest) is True

    def test_changed_count_or_date_or_missing_latest_is_changed(self):
        assert is_unchanged_day(_current_row(), {**_current_row(), "count_above": 20}) is False
        prior_date = (_TODAY - timedelta(days=2)).isoformat()
        assert is_unchanged_day(_current_row(), {**_current_row(), "date": prior_date}) is False
        assert is_unchanged_day(_current_row(), None) is False


def _series() -> list[dict]:
    monthly = {
        "date": "1990-01-31", "pct_above": 12.1, "count_above": 58,
        "total": 480, "y10": 8.21, "approximate": 1,
    }
    daily = {**_current_row(), "approximate": 0}
    return [monthly, daily]


def _build_payload() -> dict:
    return build_output(
        yields_by_ticker=_yields_by_ticker(),
        y10=Y10,
        y10_date=Y10_DATE,
        data_date=DATA_DATE,
        scan_time=SCAN_TIME,
        source={"constituents": "github-datasets", "constituents_count": 503, "quote_errors": 0},
        series=_series(),
        backfill_cutover=DATA_DATE,
    )


class TestBuildOutput:
    def test_payload_contract_keys(self):
        payload = _build_payload()
        assert {"scan_time", "source", "data_date", "y10_date", "current",
                "series", "backfill_cutover"} <= set(payload)
        assert payload["scan_time"] == SCAN_TIME
        assert payload["data_date"] == DATA_DATE
        assert payload["y10_date"] == Y10_DATE
        assert payload["backfill_cutover"] == DATA_DATE
        assert payload["source"]["constituents_count"] == 503

    def test_current_row_aggregates_the_sweep(self):
        current = _build_payload()["current"]
        assert current["date"] == DATA_DATE
        assert current["count_above"] == 19
        assert current["total"] == 503
        assert current["y10"] == Y10
        assert current["pct_above"] == pytest.approx(100.0 * 19 / 503, abs=0.005)

    def test_leaders_are_top_ten_by_trailing_yield(self):
        leaders = _build_payload()["current"]["leaders"]
        assert len(leaders) == 10
        assert leaders[0]["ticker"] == "AB018"  # highest synthetic yield
        yields = [leader["yield_pct"] for leader in leaders]
        assert yields == sorted(yields, reverse=True)

    def test_series_stays_ascending_with_approximate_flags(self):
        series = _build_payload()["series"]
        assert [row["date"] for row in series] == sorted(row["date"] for row in series)
        assert series[0]["approximate"] == 1
        assert series[-1]["approximate"] == 0


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_divyield as fd

    calls: list[tuple] = []
    monkeypatch.setattr(fd, "DIVYIELD_JSON", tmp_path / "divyield.json")
    monkeypatch.setattr(fd.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(
        fd.writer,
        "upsert_divyield_rows",
        lambda rows, recorded_at: calls.append(("rows", len(rows))),
    )
    monkeypatch.setattr(
        fd.writer,
        "upsert_scan_snapshot",
        lambda service, scan_time, payload: calls.append(("snapshot", service)),
    )
    monkeypatch.setattr(
        fd.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


class TestPersistResult:
    def test_changed_day_writes_in_order(self, persist_calls, tmp_path):
        import fetch_divyield as fd

        persist_result(_build_payload(), [_current_row()])
        assert persist_calls == [
            ("guard",),
            ("rows", 1),
            ("snapshot", "div-yield"),
            ("health", "div-yield", "ok"),
        ]
        fallback = json.loads(fd.DIVYIELD_JSON.read_text())
        assert fallback["data_date"] == DATA_DATE

    def test_unchanged_day_fast_path_skips_row_upsert_only(self, persist_calls):
        # Weekend / unchanged-source runs: no divyield_history upsert, but the
        # snapshot AND the service-health heartbeat are still written.
        persist_result(_build_payload(), [])
        kinds = [call[0] for call in persist_calls]
        assert "rows" not in kinds
        assert persist_calls[0] == ("guard",)
        assert ("snapshot", "div-yield") in persist_calls
        assert ("health", "div-yield", "ok") in persist_calls


# TEST_AUDIT T-133: `run()` was executed by no test. A regression that stops
# writing history rows while still heartbeating `ok` (e.g. `changed`
# computed against a stale history) is invisible: the snapshot and the
# heartbeat keep the 26h/120h watchdog windows green while `*_history`
# never grows. Drive the orchestration with the fetchers stubbed.
class TestRun:
    def test_a_new_date_writes_exactly_one_history_row(self, persist_calls, monkeypatch):
        import fetch_divyield as fd

        tickers = [f"T{i:03d}" for i in range(fd.MIN_CONSTITUENTS)]
        monkeypatch.setattr(fd, "fetch_constituents", lambda: (tickers, "github"))
        monkeypatch.setattr(
            fd, "sweep_yields", lambda _tickers: ({t: 4.0 + (i % 7) / 10 for i, t in enumerate(tickers)}, 0, DATA_DATE)
        )
        monkeypatch.setattr(fd, "_latest_y10", lambda: (DATA_DATE, 4.70))
        older = {**_current_row(), "date": (_TODAY - timedelta(days=2)).isoformat()}
        monkeypatch.setattr(fd, "load_history", lambda: [older])

        payload = fd.run()

        assert ("rows", 1) in persist_calls
        assert persist_calls.index(("rows", 1)) < persist_calls.index(("snapshot", "div-yield"))
        assert ("health", "div-yield", "ok") in persist_calls
        assert payload["data_date"] == DATA_DATE
        assert [row["date"] for row in payload["series"]][-2:] == [older["date"], DATA_DATE]

    def test_an_unchanged_day_writes_no_row_but_still_heartbeats(self, persist_calls, monkeypatch):
        import fetch_divyield as fd

        tickers = [f"T{i:03d}" for i in range(fd.MIN_CONSTITUENTS)]
        yields = {t: 4.0 + (i % 7) / 10 for i, t in enumerate(tickers)}
        monkeypatch.setattr(fd, "fetch_constituents", lambda: (tickers, "github"))
        monkeypatch.setattr(fd, "sweep_yields", lambda _tickers: (yields, 0, DATA_DATE))
        monkeypatch.setattr(fd, "_latest_y10", lambda: (DATA_DATE, 4.70))
        stored = {"date": DATA_DATE, **fd.compute_pct_above(list(yields.values()), 4.70), "y10": 4.70, "approximate": 0}
        monkeypatch.setattr(fd, "load_history", lambda: [stored])

        fd.run()

        assert "rows" not in [c[0] for c in persist_calls]
        assert ("health", "div-yield", "ok") in persist_calls


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

    def test_migration_registers_version_55(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [55]

    def test_migration_is_rerunnable(self, db):
        db.executescript(MIGRATION.read_text())
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [55]

    def test_schema_columns_and_date_primary_key(self, db):
        info = list(db.execute("PRAGMA table_info(divyield_history)"))
        assert [r[1] for r in info] == [
            "date", "pct_above", "count_above", "total", "y10", "approximate", "recorded_at",
        ]
        pk_by_column = {r[1]: r[5] for r in info}
        assert pk_by_column["date"] == 1

    def test_date_desc_index_exists(self, db):
        names = {r[1] for r in db.execute("PRAGMA index_list(divyield_history)")}
        assert "idx_divyield_history_date_desc" in names

    def test_approximate_defaults_to_zero(self, db):
        db.execute(
            "INSERT INTO divyield_history (date, pct_above, count_above, total, y10, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (DATA_DATE, 3.78, 19, 503, 4.74, "r1"),
        )
        assert list(db.execute("SELECT approximate FROM divyield_history")) == [(0,)]

    # TEST_AUDIT T-132: this used to execute a dead SQL constant the writer
    # never used; the REAL `upsert_divyield_rows` body (inline SQL + a
    # hand-built params tuple) was executed by no test, so a column swap
    # in `params.extend(...)` shipped to Turso green.
    def test_upsert_is_idempotent_per_date(self, db, monkeypatch):
        from db import writer

        monkeypatch.setattr(writer, "get_db", lambda: db)
        row = {"date": DATA_DATE, "pct_above": 3.58, "count_above": 18, "total": 502, "y10": 4.70}
        writer.upsert_divyield_rows([row], recorded_at="r1")
        writer.upsert_divyield_rows(
            [{**row, "pct_above": 3.78, "count_above": 19, "total": 503, "y10": 4.74}],
            recorded_at="r2",
        )
        rows = list(
            db.execute(
                "SELECT date, pct_above, count_above, total, y10, approximate, recorded_at"
                " FROM divyield_history"
            )
        )
        assert rows == [(DATA_DATE, 3.78, 19, 503, 4.74, 0, "r2")]

    def test_writer_arity(self):
        from db import writer

        parameters = list(inspect.signature(writer.upsert_divyield_rows).parameters)
        assert parameters == ["rows", "recorded_at"]


class TestSweepBudget:
    """2026-08-24: radon-divyield Result=timeout after 900s. Yahoo v8 was
    ~20s/chart; 503 tickers / 6 workers is ~28 min. ThreadPoolExecutor.map
    waited out the whole tarpit and systemd SIGTERM'd the oneshot
    (NRestarts=0) with nothing persisted."""

    def test_tarpitted_yahoo_stops_inside_the_wall_clock_budget(self, monkeypatch):
        import time
        import fetch_divyield as fd

        monkeypatch.setattr(fd, "SWEEP_BUDGET_S", 0.15, raising=False)
        monkeypatch.setattr(fd, "FETCH_WORKERS", 2)

        def hang(_ticker: str) -> dict:
            time.sleep(0.4)
            return QUOTE

        monkeypatch.setattr(fd, "fetch_ticker_chart", hang)
        tickers = [f"T{i:02d}" for i in range(8)]
        started = time.monotonic()
        yields, errors, _data_date = sweep_yields(tickers)
        elapsed = time.monotonic() - started
        # In-flight workers may finish one 0.4s fetch after the deadline;
        # map() over 8/2 workers would take ~1.6s.
        assert elapsed < 1.0
        assert len(yields) + errors == len(tickers)

    def test_quotes_finished_before_the_deadline_are_kept(self, monkeypatch):
        import fetch_divyield as fd

        monkeypatch.setattr(fd, "SWEEP_BUDGET_S", 30.0, raising=False)
        monkeypatch.setattr(fd, "FETCH_WORKERS", 2)
        monkeypatch.setattr(fd, "fetch_ticker_chart", lambda _ticker: QUOTE)
        yields, errors, data_date = sweep_yields(["XOM", "T", "VZ"])
        assert errors == 0
        assert set(yields) == {"XOM", "T", "VZ"}
        assert data_date is not None

    def test_sweep_budget_fits_inside_unit_start_timeout(self):
        service = Path(__file__).resolve().parents[2] / "cloud" / "services" / "radon-divyield.service"
        timeout_line = next(
            line for line in service.read_text().splitlines()
            if line.startswith("TimeoutStartSec=")
        )
        unit_timeout = int(timeout_line.split("=", 1)[1])
        from fetch_divyield import SWEEP_BUDGET_S

        assert SWEEP_BUDGET_S + FETCH_TIMEOUT_S <= unit_timeout

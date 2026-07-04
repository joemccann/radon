"""Margin Debt Acceleration Indicator — parser, splice, YoY, and storage tests.

Ground-truth values are read from checked-in fixtures:
  - fixtures/margin_statistics_finra.xlsx  — real FINRA workbook (Jan 1997..May 2026)
  - fixtures/nyse_legacy_1969.html         — archived NYSE Facts & Figures year table
Expected numbers below were derived by inspecting the fixtures directly
(2026-07-03), not computed by hand.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from backfill_margin_debt_legacy import parse_legacy_page_html, parse_legacy_year_html
from fetch_margin_debt import (
    NYSE_TO_FINRA_SPLICE_RATIO,
    attach_spx_series,
    build_display_series,
    compute_yoy,
    deflate_series,
    merge_series,
    parse_margin_xlsx,
    pct_of_gdp_series,
)

FIXTURES = Path(__file__).parent / "fixtures"
FINRA_XLSX = (FIXTURES / "margin_statistics_finra.xlsx").read_bytes()
LEGACY_HTML = (FIXTURES / "nyse_legacy_1969.html").read_text()
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0027_margin_debt_history.sql"


# ── FINRA XLSX parsing ────────────────────────────────────────────


class TestParseFinraXlsx:
    def test_row_count_and_ascending_order(self):
        rows = parse_margin_xlsx(FINRA_XLSX)
        assert len(rows) == 353  # 354 sheet rows minus header
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates)
        assert dates[0] == "1997-01"
        assert dates[-1] == "2026-05"

    def test_first_row_values_with_missing_trailing_cell(self):
        # The 1997-01 sheet row has only 3 cells (no free-credit-margin cell);
        # cells must be mapped by their r= column reference, not position.
        first = parse_margin_xlsx(FINRA_XLSX)[0]
        assert first["level"] == 103337.0
        assert first["free_credit_cash"] == 68856.0
        assert first["free_credit_margin"] is None

    def test_latest_row_values(self):
        last = parse_margin_xlsx(FINRA_XLSX)[-1]
        assert last["level"] == 1415557.0
        assert last["free_credit_cash"] == 206600.0
        assert last["free_credit_margin"] == 217256.0

    def test_all_rows_have_month_keys_and_positive_levels(self):
        import re
        for row in parse_margin_xlsx(FINRA_XLSX):
            assert re.fullmatch(r"\d{4}-\d{2}", row["date"])
            assert row["level"] > 0


# ── Legacy NYSE year-table parsing ────────────────────────────────


class TestParseLegacyYearHtml:
    def test_year_and_january_values(self):
        year, rows = parse_legacy_year_html(LEGACY_HTML)
        assert year == 1969
        jan = rows[0]
        assert jan["date"] == "1969-01"
        assert jan["level"] == 5930.0  # "$5,930" in the archived table
        assert jan["free_credit_margin"] == 1130.0
        assert jan["free_credit_cash"] is None  # 1960s table has no cash column

    def test_full_year_parsed(self):
        _, rows = parse_legacy_year_html(LEGACY_HTML)
        assert [r["date"] for r in rows] == [f"1969-{m:02d}" for m in range(1, 13)]

    def test_decade_page_with_multiple_year_tables(self):
        # Archived decade pages render ten year tables in one document; the
        # page parser must attribute each table to its own year's months.
        two_years = LEGACY_HTML + LEGACY_HTML.replace(", 1969", ", 1970").replace("$5,930", "$6,100")
        rows = parse_legacy_page_html(two_years)
        by_date = {r["date"]: r for r in rows}
        assert by_date["1969-01"]["level"] == 5930.0
        assert by_date["1970-01"]["level"] == 6100.0
        assert len(rows) == 24


# ── Merge + splice ────────────────────────────────────────────────


def _row(date, level, source=None, fc_cash=None, fc_margin=None):
    return {
        "date": date,
        "level": float(level),
        "free_credit_cash": fc_cash,
        "free_credit_margin": fc_margin,
        **({"source": source} if source else {}),
    }


class TestMergeSeries:
    def test_finra_wins_on_overlap_and_sources_are_tagged(self):
        legacy = [_row("1996-12", 98000), _row("1997-01", 99460)]
        finra = [_row("1997-01", 103337), _row("1997-02", 105000)]
        merged = merge_series(legacy, finra)
        assert [r["date"] for r in merged] == ["1996-12", "1997-01", "1997-02"]
        by_date = {r["date"]: r for r in merged}
        assert by_date["1996-12"]["source"] == "nyse_legacy"
        assert by_date["1997-01"]["source"] == "finra"
        assert by_date["1997-01"]["level"] == 103337.0


# ── YoY transform ─────────────────────────────────────────────────


class TestComputeYoy:
    def test_yoy_uses_calendar_month_lookback(self):
        # 200 -> 250 over exactly 12 months: (250/200 - 1) * 100 = 25.0
        series = [_row("1995-01", 200, "finra"), _row("1996-01", 250, "finra")]
        out = compute_yoy(series)
        assert out[1]["level_yoy_pct"] == pytest.approx(25.0)

    def test_first_months_without_lookback_are_null(self):
        series = [_row("1995-01", 200, "finra"), _row("1995-06", 210, "finra")]
        out = compute_yoy(series)
        assert all(r["level_yoy_pct"] is None for r in out)

    def test_gap_at_lookback_month_is_null(self):
        # 1996-02 exists but 1995-02 does not -> no YoY even though len > 12
        series = [_row(f"1995-{m:02d}", 100 + m, "finra") for m in (1, 3, 4)] + [
            _row("1996-02", 150, "finra")
        ]
        out = compute_yoy(series)
        assert out[-1]["level_yoy_pct"] is None

    def test_seam_straddling_months_are_null(self):
        # t is finra but t-12 is nyse_legacy: member-firm scope changed, null it.
        series = [_row("1996-03", 95000, "nyse_legacy"), _row("1997-03", 110000, "finra")]
        out = compute_yoy(series)
        assert out[-1]["level_yoy_pct"] is None

    def test_same_source_after_seam_computes(self):
        series = [_row("1997-03", 110000, "finra"), _row("1998-03", 121000, "finra")]
        out = compute_yoy(series)
        assert out[-1]["level_yoy_pct"] == pytest.approx(10.0)


# ── Display series: ratio adjust + net ────────────────────────────


class TestBuildDisplaySeries:
    def test_splice_ratio_is_jan_1997_overlap(self):
        # FINRA Jan-1997 103,337 vs NYSE Jan-1997 99,460 (verified 2026-07-03)
        assert NYSE_TO_FINRA_SPLICE_RATIO == pytest.approx(103337.0 / 99460.0)

    def test_legacy_rows_are_ratio_adjusted_finra_rows_untouched(self):
        series = [_row("1996-12", 100000, "nyse_legacy"), _row("1997-01", 103337, "finra")]
        out = build_display_series(series)
        assert out[0]["level_display"] == pytest.approx(100000 * NYSE_TO_FINRA_SPLICE_RATIO)
        assert out[0]["level"] == 100000.0  # raw preserved
        assert out[1]["level_display"] == 103337.0

    def test_net_level_subtracts_available_free_credit(self):
        series = [
            _row("2026-05", 1415557, "finra", fc_cash=206600.0, fc_margin=217256.0),
            _row("1969-01", 5930, "nyse_legacy", fc_margin=1130.0),
        ]
        out = build_display_series(sorted(series, key=lambda r: r["date"]))
        by_date = {r["date"]: r for r in out}
        assert by_date["2026-05"]["net_level"] == pytest.approx(1415557 - 206600 - 217256)
        assert by_date["1969-01"]["net_level"] == pytest.approx(5930 - 1130)


# ── Normalization views ───────────────────────────────────────────


class TestNormalization:
    def test_deflate_rebases_to_reference_cpi(self):
        series = [_row("2000-01", 500, "finra")]
        # CPI 100 then vs 200 reference: real level doubles
        out = deflate_series(series, cpi_by_month={"2000-01": 100.0}, reference_cpi=200.0)
        assert out[0]["level_real"] == pytest.approx(1000.0)

    def test_deflate_missing_cpi_is_null(self):
        out = deflate_series([_row("2000-01", 500, "finra")], cpi_by_month={}, reference_cpi=200.0)
        assert out[0]["level_real"] is None

    def test_pct_of_gdp_maps_month_to_quarter(self):
        # level 5,930 $mm vs GDP 1,000 $bn = 1,000,000 $mm -> 0.593%
        series = [_row("1969-02", 5930, "nyse_legacy")]
        out = pct_of_gdp_series(series, gdp_bn_by_quarter={"1969-Q1": 1000.0})
        assert out[0]["level_pct_gdp"] == pytest.approx(0.593)

    def test_pct_of_gdp_missing_quarter_is_null(self):
        out = pct_of_gdp_series([_row("1969-02", 5930, "nyse_legacy")], gdp_bn_by_quarter={})
        assert out[0]["level_pct_gdp"] is None


class TestAttachSpx:
    def test_maps_monthly_close_by_month_key(self):
        series = [_row("1969-01", 5930, "nyse_legacy"), _row("1969-02", 5750, "nyse_legacy")]
        out = attach_spx_series(series, spx_close_by_month={"1969-01": 103.01})
        assert out[0]["spx_close"] == pytest.approx(103.01)
        assert out[1]["spx_close"] is None


# ── run(): conditional-GET fast path ──────────────────────────────


class _StubClient:
    def __init__(self, result):
        self._result = result
        self.seen_if_modified_since = "unset"

    def fetch_margin_statistics(self, if_modified_since=None):
        self.seen_if_modified_since = if_modified_since
        return self._result


class TestRunConditionalGet:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_margin_debt as mod

        monkeypatch.setattr(mod, "MARGIN_DEBT_JSON", tmp_path / "margin_debt.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod, "_write_db_cache",
            lambda payload, scan_time, rows_changed: self.db_writes.append(rows_changed),
        )
        monkeypatch.setattr(mod, "_load_normalization_inputs", lambda: ({}, {}, False))
        self.mod = mod

    def test_unchanged_source_reuses_cached_payload_without_row_writes(self):
        stamp = "Tue, 16 Jun 2026 14:52:10 GMT"
        cached = {"scan_time": "old", "source_last_modified": stamp, "count": 809,
                  "series": [], "current": {}, "splice": {}, "normalization": {}}
        self.mod.MARGIN_DEBT_JSON.write_text(json.dumps(cached))

        client = _StubClient((None, stamp))
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since == stamp
        assert payload["count"] == 809
        assert payload["scan_time"] != "old"  # heartbeat still advances
        assert self.db_writes == [False]  # snapshot only, no 809-row upsert

    def test_changed_source_rebuilds_and_writes_rows(self):
        client = _StubClient((FINRA_XLSX, "Wed, 01 Jul 2026 00:00:00 GMT"))
        payload = self.mod.run(client=client)

        assert client.seen_if_modified_since is None  # no cache yet
        assert payload["current"]["date"] == "2026-05"
        assert self.db_writes == [True]


# ── Migration + upsert (sqlite3 stand-in for libsql) ──────────────


class TestMarginDebtStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 27
        cols = {r[1] for r in db.execute("PRAGMA table_info(margin_debt_history)")}
        assert {"date", "level", "level_yoy_pct", "free_credit_cash",
                "free_credit_margin", "source", "recorded_at"} <= cols

    def test_upsert_is_idempotent_per_month(self):
        from db import writer

        db = self._db()
        args1 = ("2026-05", 1415557.0, 33.1, 206600.0, 217256.0, "finra", "2026-07-03T00:00:00Z")
        args2 = ("2026-05", 1415600.0, 33.2, 206600.0, 217256.0, "finra", "2026-07-04T00:00:00Z")
        db.execute(writer.MARGIN_DEBT_UPSERT_SQL, args1)
        db.execute(writer.MARGIN_DEBT_UPSERT_SQL, args2)
        rows = db.execute("SELECT date, level, level_yoy_pct FROM margin_debt_history").fetchall()
        assert rows == [("2026-05", 1415600.0, 33.2)]

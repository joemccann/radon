"""CFTC COT cross-asset positioning — normalization, crowding stats, storage.

No live network: the stub client replays fixture payloads shaped like the
documented Equibles envelope (``{"data": [...], "meta": {"hasMore": ...}}``)
and borrows the real ``EquiblesClient.paginate`` so the bounded page walk is
exercised rather than re-implemented.

Dates are window-relative (Tuesdays counted back from today) — COT report
dates are always the prior Tuesday, and hardcoded dates rot in CI.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from clients.equibles_client import EquiblesClient
from fetch_equibles_cot_positioning import (
    BIAS_BEARISH,
    BIAS_BULLISH,
    BIAS_NEUTRAL,
    COT_UPSERT_SQL,
    CROWDING_LONG,
    CROWDING_NEUTRAL,
    CROWDING_SHORT,
    CROWDING_UNKNOWN,
    EXTREME_PERCENTILE_HIGH,
    EXTREME_PERCENTILE_LOW,
    FOCUS_ALIASES,
    MIN_STATS_WEEKS,
    CotIngestError,
    build_contract_positioning,
    build_payload,
    classify_crowding,
    contrarian_bias,
    cot_rows_for_db,
    cot_upsert_sql,
    normalize_position_row,
    percentile_rank,
    resolve_market_code,
    run,
    z_score,
)

MIGRATION = (
    Path(__file__).parents[1] / "db" / "migrations" / "0044_equibles_cot_positioning.sql"
)


# ── window-relative fixtures ──────────────────────────────────────


def weekly_report_dates(count: int, *, end: date | None = None) -> list[str]:
    """Ascending yyyy-MM-dd COT Tuesdays, oldest first, ending this week."""
    end = end or date.today()
    last_tuesday = end - timedelta(days=(end.weekday() - 1) % 7)
    return [(last_tuesday - timedelta(weeks=i)).isoformat() for i in range(count - 1, -1, -1)]


def raw_position(report_date: str, *, nc_long: float, nc_short: float, oi: float = 1_000_000.0):
    """A raw Equibles COT row in documented camelCase, newest-first ordering."""
    return {
        "reportDate": report_date,
        "openInterest": oi,
        "commercialLong": 400_000.0,
        "commercialShort": 300_000.0,
        "nonCommercialLong": nc_long,
        "nonCommercialShort": nc_short,
        "nonCommercialSpread": 50_000.0,
    }


# ── row normalization (defensive against renamed fields) ──────────


class TestNormalizePositionRow:
    def test_canonical_camel_case_row(self):
        row = normalize_position_row(raw_position("2026-08-04", nc_long=250_000, nc_short=100_000))
        assert row["report_date"] == "2026-08-04"
        assert row["open_interest"] == 1_000_000.0
        assert row["noncommercial_long"] == 250_000.0
        assert row["noncommercial_short"] == 100_000.0
        assert row["noncommercial_spread"] == 50_000.0

    def test_nets_are_derived_from_long_minus_short(self):
        row = normalize_position_row(raw_position("2026-08-04", nc_long=250_000, nc_short=100_000))
        assert row["net_noncommercial"] == pytest.approx(150_000.0)
        assert row["net_commercial"] == pytest.approx(100_000.0)

    def test_published_nets_win_over_derivation(self):
        raw = {**raw_position("2026-08-04", nc_long=1, nc_short=1), "netNonCommercial": -42_000.0}
        assert normalize_position_row(raw)["net_noncommercial"] == pytest.approx(-42_000.0)

    def test_field_matching_is_case_insensitive_so_renames_survive(self):
        raw = {
            "date": "2026-08-04",
            "OpenInterest": 500_000.0,
            "noncommercialLong": 200_000.0,
            "noncommercial_short": 50_000.0,
        }
        row = normalize_position_row(raw)
        assert row["report_date"] == "2026-08-04"
        assert row["open_interest"] == 500_000.0
        assert row["net_noncommercial"] == pytest.approx(150_000.0)

    def test_net_share_of_open_interest_is_a_percent(self):
        row = normalize_position_row(raw_position("2026-08-04", nc_long=250_000, nc_short=100_000))
        assert row["net_noncommercial_pct_oi"] == pytest.approx(15.0)

    def test_missing_open_interest_leaves_share_null_without_crashing(self):
        raw = {"reportDate": "2026-08-04", "nonCommercialLong": 10, "nonCommercialShort": 4}
        row = normalize_position_row(raw)
        assert row["open_interest"] is None
        assert row["net_noncommercial_pct_oi"] is None

    def test_unusable_row_without_a_report_date_is_dropped(self):
        assert normalize_position_row({"nonCommercialLong": 1}) is None

    def test_iso_timestamp_report_date_is_truncated_to_a_day(self):
        row = normalize_position_row({"reportDate": "2026-08-04T00:00:00Z", "netNonCommercial": 1})
        assert row["report_date"] == "2026-08-04"


# ── crowding statistics ───────────────────────────────────────────


class TestPercentileRank:
    def test_highest_value_in_the_window_ranks_at_100(self):
        assert percentile_rank([1, 2, 3, 4], 4) == pytest.approx(100.0)

    def test_lowest_value_ranks_at_its_own_share(self):
        assert percentile_rank([1, 2, 3, 4], 1) == pytest.approx(25.0)

    def test_midpoint_ranks_at_half(self):
        assert percentile_rank([1, 2, 3, 4], 2.5) == pytest.approx(50.0)

    def test_empty_window_has_no_rank(self):
        assert percentile_rank([], 1) is None


class TestZScore:
    def test_z_score_against_window_mean_and_population_sigma(self):
        # mean 3, population sigma 2 -> (7 - 3) / 2 = 2.0
        assert z_score([1, 5, 1, 5], 7) == pytest.approx(2.0)

    def test_flat_window_has_no_dispersion_so_no_z_score(self):
        assert z_score([5, 5, 5], 5) is None

    def test_single_observation_has_no_z_score(self):
        assert z_score([5], 5) is None


class TestClassifyCrowding:
    def test_top_decile_reads_as_crowded_long(self):
        assert classify_crowding(EXTREME_PERCENTILE_HIGH) == CROWDING_LONG
        assert classify_crowding(97.0) == CROWDING_LONG

    def test_bottom_decile_reads_as_crowded_short(self):
        assert classify_crowding(EXTREME_PERCENTILE_LOW) == CROWDING_SHORT
        assert classify_crowding(2.0) == CROWDING_SHORT

    def test_middle_of_the_range_is_neutral(self):
        assert classify_crowding(50.0) == CROWDING_NEUTRAL

    def test_missing_percentile_is_unknown_not_neutral(self):
        assert classify_crowding(None) == CROWDING_UNKNOWN

    def test_extremes_are_contrarian(self):
        assert contrarian_bias(CROWDING_LONG) == BIAS_BEARISH
        assert contrarian_bias(CROWDING_SHORT) == BIAS_BULLISH
        assert contrarian_bias(CROWDING_NEUTRAL) == BIAS_NEUTRAL
        assert contrarian_bias(CROWDING_UNKNOWN) == BIAS_NEUTRAL


# ── per-contract assembly ─────────────────────────────────────────


CONTRACT = {"market_code": "13874P", "name": "E-MINI S&P 500", "category": "EquityIndices"}


def rising_history(weeks: int) -> list[dict]:
    """Newest-first raw rows whose speculative net rises every week."""
    dates = weekly_report_dates(weeks)
    rows = [
        raw_position(d, nc_long=100_000 + i * 1_000, nc_short=50_000)
        for i, d in enumerate(dates)
    ]
    return list(reversed(rows))


class TestBuildContractPositioning:
    def test_series_is_ascending_by_report_date(self):
        out = build_contract_positioning("ES", CONTRACT, rising_history(MIN_STATS_WEEKS + 4))
        dates = [row["report_date"] for row in out["series"]]
        assert dates == sorted(dates)

    def test_current_reading_is_the_newest_week(self):
        weeks = MIN_STATS_WEEKS + 4
        out = build_contract_positioning("ES", CONTRACT, rising_history(weeks))
        assert out["report_date"] == weekly_report_dates(weeks)[-1]
        assert out["net_noncommercial"] == pytest.approx(100_000 + (weeks - 1) * 1_000 - 50_000)

    def test_monotone_rise_puts_the_latest_week_at_the_top_of_its_own_history(self):
        out = build_contract_positioning("ES", CONTRACT, rising_history(MIN_STATS_WEEKS + 4))
        assert out["percentile"] == pytest.approx(100.0)
        assert out["crowding"] == CROWDING_LONG
        assert out["contrarian_bias"] == BIAS_BEARISH

    def test_week_over_week_change_is_reported(self):
        out = build_contract_positioning("ES", CONTRACT, rising_history(MIN_STATS_WEEKS + 4))
        assert out["net_noncommercial_change"] == pytest.approx(1_000.0)

    def test_short_history_withholds_the_statistics_instead_of_faking_neutral(self):
        out = build_contract_positioning("ES", CONTRACT, rising_history(MIN_STATS_WEEKS - 1))
        assert out["percentile"] is None
        assert out["z_score"] is None
        assert out["crowding"] == CROWDING_UNKNOWN

    def test_contract_identity_is_carried_through(self):
        out = build_contract_positioning("ES", CONTRACT, rising_history(MIN_STATS_WEEKS + 1))
        assert out["alias"] == "ES"
        assert out["market_code"] == "13874P"
        assert out["name"] == "E-MINI S&P 500"
        assert out["category"] == "EquityIndices"

    def test_no_usable_rows_yields_nothing(self):
        assert build_contract_positioning("ES", CONTRACT, [{"nonCommercialLong": 1}]) is None


# ── roster resolution ─────────────────────────────────────────────


class TestResolveMarketCode:
    ROSTER = [
        {"marketCode": "13874P", "alias": "ES", "name": "E-MINI S&P 500", "category": "EquityIndices"},
        {"marketCode": "088691", "symbol": "GC", "name": "GOLD", "category": "Metals"},
        {"marketCode": "067651", "name": "CRUDE OIL, LIGHT SWEET (WTI)", "category": "Energy"},
    ]

    def test_alias_field_match(self):
        assert resolve_market_code(self.ROSTER, "ES")["market_code"] == "13874P"

    def test_symbol_field_match_is_case_insensitive(self):
        assert resolve_market_code(self.ROSTER, "gc")["market_code"] == "088691"

    def test_falls_back_to_a_name_token_match(self):
        assert resolve_market_code(self.ROSTER, "WTI")["market_code"] == "067651"

    def test_unknown_alias_resolves_to_nothing(self):
        assert resolve_market_code(self.ROSTER, "ZZZ") is None


# ── payload ───────────────────────────────────────────────────────


def contract_payload(alias: str, weeks: int = MIN_STATS_WEEKS + 4) -> dict:
    return build_contract_positioning(alias, {**CONTRACT, "market_code": f"C{alias}"}, rising_history(weeks))


class TestBuildPayload:
    def test_payload_carries_contracts_and_the_newest_report_date(self):
        contracts = [contract_payload("ES"), contract_payload("GC")]
        payload = build_payload(contracts, market_rows=[], scan_time="2026-08-11T00:00:00Z")
        assert payload["count"] == 2
        assert payload["report_date"] == contracts[0]["report_date"]
        assert payload["scan_time"] == "2026-08-11T00:00:00Z"

    def test_empty_contracts_is_never_cached_as_a_valid_payload(self):
        with pytest.raises(CotIngestError):
            build_payload([], market_rows=[], scan_time="2026-08-11T00:00:00Z")

    def test_market_board_rows_are_normalized(self):
        rows = [{"marketCode": "13874P", "name": "E-MINI S&P 500", **raw_position("2026-08-04", nc_long=9, nc_short=4)}]
        payload = build_payload([contract_payload("ES")], market_rows=rows, scan_time="t")
        assert payload["market"][0]["net_noncommercial"] == pytest.approx(5.0)
        assert payload["market"][0]["market_code"] == "13874P"


# ── run() orchestration against a stub client ─────────────────────


class _StubClient:
    """Replays fixture envelopes; reuses the real bounded pagination walk."""

    paginate = EquiblesClient.paginate

    def __init__(self, roster, history_by_code, latest_rows, *, max_pages=5):
        self._roster = roster
        self._history_by_code = history_by_code
        self._latest_rows = latest_rows
        self._max_pages = max_pages
        self.request_count = 0
        self.closed = False
        self.seen_start_dates = []

    def get_cftc_contracts(self, *, query=None, alias=None):
        self.request_count += 1
        return {"data": self._roster}

    def get_cftc_positions(self, market_code, *, start_date=None, end_date=None, limit=None, offset=None):
        self.request_count += 1
        self.seen_start_dates.append(start_date)
        rows = self._history_by_code.get(market_code, [])
        window = rows[(offset or 0):(offset or 0) + (limit or len(rows))]
        has_more = (offset or 0) + len(window) < len(rows)
        return {"data": window, "meta": {"hasMore": has_more}}

    def get_cftc_latest_positions(self, *, category=None):
        self.request_count += 1
        return {"data": self._latest_rows}

    def close(self):
        self.closed = True


def stub_client(**overrides):
    roster = [
        {"marketCode": f"C{alias}", "alias": alias, "name": f"{alias} FUTURES", "category": "EquityIndices"}
        for alias in FOCUS_ALIASES
    ]
    history = {f"C{alias}": rising_history(MIN_STATS_WEEKS + 4) for alias in FOCUS_ALIASES}
    latest = [{"marketCode": "CES", "name": "ES FUTURES", **raw_position(weekly_report_dates(1)[0], nc_long=9, nc_short=4)}]
    return _StubClient(overrides.get("roster", roster), overrides.get("history", history), overrides.get("latest", latest))


class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate_caches(self, tmp_path, monkeypatch):
        import fetch_equibles_cot_positioning as mod

        monkeypatch.setattr(mod, "COT_JSON", tmp_path / "equibles_cot_positioning.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod, "_write_db_cache", lambda payload, scan_time: self.db_writes.append(payload)
        )
        self.mod = mod

    def test_every_focus_contract_is_ingested(self):
        payload = run(client=stub_client(), now=datetime(2026, 8, 11, tzinfo=timezone.utc))
        assert [c["alias"] for c in payload["contracts"]] == list(FOCUS_ALIASES)

    def test_payload_is_mirrored_to_db_and_disk(self):
        payload = run(client=stub_client(), now=datetime(2026, 8, 11, tzinfo=timezone.utc))
        assert self.db_writes == [payload]
        assert json.loads(self.mod.COT_JSON.read_text())["count"] == len(FOCUS_ALIASES)

    def test_history_is_requested_from_a_trailing_lookback_window(self):
        client = stub_client()
        run(client=client, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
        expected = (date(2026, 8, 11) - timedelta(days=self.mod.HISTORY_LOOKBACK_DAYS)).isoformat()
        assert set(client.seen_start_dates) == {expected}

    def test_an_unresolvable_roster_raises_instead_of_caching_an_empty_payload(self):
        client = stub_client(roster=[{"marketCode": "X", "name": "SOMETHING ELSE"}], history={})
        with pytest.raises(CotIngestError):
            run(client=client, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
        assert self.db_writes == []

    def test_a_caller_supplied_client_is_not_closed_by_the_fetcher(self):
        client = stub_client()
        run(client=client, now=datetime(2026, 8, 11, tzinfo=timezone.utc))
        assert client.closed is False


# ── persistence ───────────────────────────────────────────────────


class TestCotStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 44
        cols = {r[1] for r in db.execute("PRAGMA table_info(cot_positioning)")}
        assert {
            "market_code", "report_date", "contract_name", "category", "open_interest",
            "commercial_long", "commercial_short", "noncommercial_long", "noncommercial_short",
            "noncommercial_spread", "net_commercial", "net_noncommercial",
            "net_noncommercial_pct_oi", "recorded_at",
        } <= cols

    def test_lookup_index_covers_the_per_contract_history_read(self):
        db = self._db()
        indices = {r[1] for r in db.execute("PRAGMA index_list(cot_positioning)")}
        assert "idx_cot_positioning_contract_date" in indices

    def test_upsert_is_idempotent_per_contract_week(self):
        db = self._db()
        base = ("13874P", "2026-08-04", "E-MINI S&P 500", "EquityIndices", 1_000_000.0,
                400_000.0, 300_000.0, 250_000.0, 100_000.0, 50_000.0, 100_000.0, 150_000.0, 15.0)
        db.execute(COT_UPSERT_SQL, (*base, "2026-08-11T00:00:00Z"))
        revised = (*base[:10], 90_000.0, 160_000.0, 16.0)
        db.execute(COT_UPSERT_SQL, (*revised, "2026-08-12T00:00:00Z"))
        rows = db.execute(
            "SELECT market_code, report_date, net_noncommercial FROM cot_positioning"
        ).fetchall()
        assert rows == [("13874P", "2026-08-04", 160_000.0)]

    def test_a_whole_chunk_upserts_in_one_statement(self):
        # Hrana bills a round-trip per statement, so bulk writes must be one
        # multi-row INSERT per chunk, never one statement per contract-week.
        db = self._db()
        rows = cot_rows_for_db(
            build_payload([contract_payload("ES", weeks=8)], market_rows=[], scan_time="t")
        )
        params = [v for row in rows for v in (
            row["market_code"], row["report_date"], row["contract_name"], row["category"],
            row["open_interest"], row["commercial_long"], row["commercial_short"],
            row["noncommercial_long"], row["noncommercial_short"], row["noncommercial_spread"],
            row["net_commercial"], row["net_noncommercial"], row["net_noncommercial_pct_oi"],
            "2026-08-11T00:00:00Z",
        )]
        db.execute(cot_upsert_sql(len(rows)), params)
        assert db.execute("SELECT COUNT(*) FROM cot_positioning").fetchone()[0] == 8

    def test_db_rows_flatten_every_contract_week(self):
        payload = build_payload(
            [contract_payload("ES", weeks=6), contract_payload("GC", weeks=6)],
            market_rows=[],
            scan_time="2026-08-11T00:00:00Z",
        )
        rows = cot_rows_for_db(payload)
        assert len(rows) == 12
        db = self._db()
        for row in rows:
            db.execute(COT_UPSERT_SQL, (
                row["market_code"], row["report_date"], row["contract_name"], row["category"],
                row["open_interest"], row["commercial_long"], row["commercial_short"],
                row["noncommercial_long"], row["noncommercial_short"], row["noncommercial_spread"],
                row["net_commercial"], row["net_noncommercial"], row["net_noncommercial_pct_oi"],
                "2026-08-11T00:00:00Z",
            ))
        assert db.execute("SELECT COUNT(*) FROM cot_positioning").fetchone()[0] == 12

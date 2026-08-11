"""Short crowding / squeeze scoreboard — normalization, gate readings, storage.

Fixture-driven: no live Equibles calls. The stub client returns documented
Equibles shapes already passed through EquiblesClient's fraction normalization
(``shortInterestPercentOfFloat`` arrives here as ``shortInterestPctOfFloat`` in
0-100), plus deliberately malformed rows so the defensive parser is pinned.

Settlement dates are window-relative (today minus N) so the vintage/staleness
assertions never rot in CI.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fetch_equibles_short_crowding import (
    SHORT_INTEREST_UPSERT_SQL,
    SQUEEZE_SCORE_UPSERT_SQL,
    build_payload,
    build_ticker_entry,
    crowding_tier,
    expand_values_clause,
    is_payload_valid,
    normalize_board_rows,
    normalize_short_interest_rows,
    normalize_squeeze_score,
    rank_entries,
    settlement_age_days,
)

MIGRATION = (
    Path(__file__).parents[1] / "db" / "migrations" / "0042_equibles_short_crowding.sql"
)


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


SETTLEMENT_LATEST = _days_ago(9)
SETTLEMENT_PRIOR = _days_ago(24)


def _short_interest_payload(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "data": [
            {
                "settlementDate": SETTLEMENT_LATEST,
                "shortPosition": 120_000_000,
                "changeInShortPosition": 20_000_000,
                "averageDailyVolume": 40_000_000,
                "daysToCover": 3.0,
            },
            {
                "settlementDate": SETTLEMENT_PRIOR,
                "shortPosition": 100_000_000,
                "changeInShortPosition": -5_000_000,
                "averageDailyVolume": 50_000_000,
                "daysToCover": 2.0,
            },
        ],
        "meta": {"hasMore": False},
    }


def _squeeze_payload(ticker: str = "AAPL", score: float = 78.0) -> dict:
    return {
        "data": [
            {
                "ticker": ticker,
                "score": score,
                "rank": 12,
                "baseScore": 70.0,
                "catalystBoost": 8.0,
                "marketCap": 3_100_000_000_000,
                "dollarVolume": 9_800_000_000,
                "shortInterestPctOfFloat": 4.23,
                "daysToCoverPercentile": 88.0,
            }
        ]
    }


# ── Short-interest normalization ──────────────────────────────────


class TestNormalizeShortInterestRows:
    def test_extracts_documented_fields_newest_first(self):
        rows = normalize_short_interest_rows(_short_interest_payload())
        assert [r["settlement_date"] for r in rows] == [SETTLEMENT_LATEST, SETTLEMENT_PRIOR]
        assert rows[0]["short_position"] == 120_000_000.0
        assert rows[0]["change_in_short_position"] == 20_000_000.0
        assert rows[0]["average_daily_volume"] == 40_000_000.0
        assert rows[0]["days_to_cover"] == pytest.approx(3.0)

    def test_drops_rows_without_a_settlement_date(self):
        payload = {"data": [{"shortPosition": 5}, {"settlementDate": SETTLEMENT_LATEST}]}
        rows = normalize_short_interest_rows(payload)
        assert [r["settlement_date"] for r in rows] == [SETTLEMENT_LATEST]

    def test_missing_and_unparseable_numbers_become_null_not_crash(self):
        payload = {
            "data": [
                {
                    "settlementDate": SETTLEMENT_LATEST,
                    "shortPosition": "n/a",
                    "daysToCover": None,
                }
            ]
        }
        rows = normalize_short_interest_rows(payload)
        assert rows[0]["short_position"] is None
        assert rows[0]["days_to_cover"] is None
        assert rows[0]["average_daily_volume"] is None

    def test_accepts_a_bare_list_response(self):
        rows = normalize_short_interest_rows(
            [{"settlementDate": SETTLEMENT_LATEST, "shortPosition": 10}]
        )
        assert len(rows) == 1

    def test_timestamped_settlement_dates_are_truncated_to_the_day(self):
        rows = normalize_short_interest_rows(
            {"data": [{"settlementDate": f"{SETTLEMENT_LATEST}T00:00:00Z", "shortPosition": 1}]}
        )
        assert rows[0]["settlement_date"] == SETTLEMENT_LATEST


# ── Squeeze-score normalization ───────────────────────────────────


class TestNormalizeSqueezeScore:
    def test_reads_the_single_row_lookup(self):
        squeeze = normalize_squeeze_score(_squeeze_payload())
        assert squeeze["score"] == pytest.approx(78.0)
        assert squeeze["rank"] == 12
        assert squeeze["base_score"] == pytest.approx(70.0)
        assert squeeze["catalyst_boost"] == pytest.approx(8.0)
        assert squeeze["market_cap"] == 3_100_000_000_000.0

    def test_keeps_percentile_factors_verbatim(self):
        squeeze = normalize_squeeze_score(_squeeze_payload())
        assert squeeze["factors"]["daysToCoverPercentile"] == pytest.approx(88.0)
        assert "score" not in squeeze["factors"]

    def test_accepts_a_bare_object_response(self):
        squeeze = normalize_squeeze_score({"ticker": "GME", "score": 91})
        assert squeeze["score"] == pytest.approx(91.0)

    def test_empty_payload_is_none(self):
        assert normalize_squeeze_score({"data": []}) is None
        assert normalize_squeeze_score(None) is None

    def test_reads_the_live_envelope_shape(self):
        # Verified live 2026-08-11: /short-squeeze-scores puts settlementDate on
        # the ENVELOPE, names market cap marketCapitalization, and reports
        # short interest as shortInterestPctOfShares.
        squeeze = normalize_squeeze_score(
            {
                "settlementDate": SETTLEMENT_LATEST,
                "rank": 1447,
                "scoredCount": 4200,
                "data": [
                    {
                        "ticker": "GME",
                        "rank": 1447,
                        "score": 68.38,
                        "shortInterestPctOfShares": 12.35,
                        "marketCapitalization": 8_489_238_528.0,
                        "averageDailyDollarVolume": 64_970_541.7,
                        "baseScore": 68.38,
                        "catalystBoost": 0.0,
                    }
                ],
            }
        )
        assert squeeze["as_of"] == SETTLEMENT_LATEST
        assert squeeze["market_cap"] == pytest.approx(8_489_238_528.0)
        assert squeeze["dollar_volume"] == pytest.approx(64_970_541.7)
        assert squeeze["short_interest_pct"] == pytest.approx(12.35)
        assert squeeze["short_interest_pct_basis"] == "shares"

    def test_fraction_factors_outside_the_client_registry_are_renamed_to_percent(self):
        # These arrive as FRACTIONS and are not in EquiblesClient.FRACTION_FIELDS,
        # so no ambiguous key may survive into the payload.
        squeeze = normalize_squeeze_score(
            {
                "data": [
                    {
                        "ticker": "GME",
                        "score": 68.38,
                        "shortInterestChangePercent": -0.0077,
                        "shortVolumeShareTrend": 0.0143,
                        "failsToDeliverPercentOfShares": 0.00046,
                        "shortInterestPercentile": 92.8,
                    }
                ]
            }
        )
        factors = squeeze["factors"]
        assert factors["shortInterestChangePct"] == pytest.approx(-0.77)
        assert factors["shortVolumeShareTrendPct"] == pytest.approx(1.43)
        assert factors["failsToDeliverPctOfShares"] == pytest.approx(0.046)
        assert "shortInterestChangePercent" not in factors
        assert "shortVolumeShareTrend" not in factors
        assert "failsToDeliverPercentOfShares" not in factors
        # Percentiles are already 0-100 and must not be rescaled.
        assert factors["shortInterestPercentile"] == pytest.approx(92.8)

    def test_percent_passes_through_as_a_percent_with_its_basis(self):
        # The client already converted 0.0423 -> 4.23 and renamed the key; this
        # layer must not scale it again, and percent-of-float must not be
        # relabelled as percent-of-shares.
        squeeze = normalize_squeeze_score(_squeeze_payload())
        assert squeeze["short_interest_pct"] == pytest.approx(4.23)
        assert squeeze["short_interest_pct_basis"] == "float"


# ── Crowding tiers + entry assembly ───────────────────────────────


class TestCrowdingTier:
    def test_tiers_ladder_on_days_to_cover(self):
        assert crowding_tier(6.0) == "extreme"
        assert crowding_tier(3.5) == "crowded"
        assert crowding_tier(1.8) == "moderate"
        assert crowding_tier(0.4) == "light"

    def test_unknown_days_to_cover_is_unknown(self):
        assert crowding_tier(None) == "unknown"


class TestSettlementAge:
    def test_age_is_derived_from_the_settlement_date(self):
        assert settlement_age_days(_days_ago(9), today=date.today()) == 9

    def test_missing_or_malformed_date_is_none(self):
        assert settlement_age_days(None, today=date.today()) is None
        assert settlement_age_days("not-a-date", today=date.today()) is None


class TestBuildTickerEntry:
    def _entry(self):
        return build_ticker_entry(
            "AAPL",
            normalize_short_interest_rows(_short_interest_payload()),
            normalize_squeeze_score(_squeeze_payload()),
            today=date.today(),
        )

    def test_carries_the_latest_settlement_vintage(self):
        entry = self._entry()
        assert entry["settlement_date"] == SETTLEMENT_LATEST
        assert entry["settlement_age_days"] == 9
        assert entry["prior_settlement_date"] == SETTLEMENT_PRIOR

    def test_change_pct_is_measured_against_the_prior_position(self):
        # 100,000,000 -> 120,000,000 = +20%
        assert self._entry()["short_position_change_pct"] == pytest.approx(20.0)

    def test_surfaces_both_gate_readings(self):
        readings = self._entry()["readings"]
        assert readings["upside_convexity"] in {"low", "moderate", "high", "extreme"}
        assert readings["short_leg_tail_risk"] == readings["upside_convexity"]

    def test_entry_without_short_interest_is_none(self):
        assert build_ticker_entry("AAPL", [], None, today=date.today()) is None

    def test_entry_without_a_squeeze_score_still_builds(self):
        entry = build_ticker_entry(
            "AAPL",
            normalize_short_interest_rows(_short_interest_payload()),
            None,
            today=date.today(),
        )
        assert entry["squeeze_score"] is None
        assert entry["days_to_cover"] == pytest.approx(3.0)

    def test_borrow_join_seam_is_present_and_unresolved(self):
        # IB borrow cost/availability is a separate live probe; the fetcher
        # leaves a declared seam rather than refactoring that route.
        entry = self._entry()
        assert entry["borrow"] == {"source": "ib-short-availability", "resolved": False}


class TestRankEntries:
    def test_orders_by_squeeze_score_then_days_to_cover(self):
        def entry(ticker, score, dtc):
            return {"ticker": ticker, "squeeze_score": score, "days_to_cover": dtc}

        ranked = rank_entries([entry("A", 10, 9.0), entry("B", 90, 1.0), entry("C", None, 8.0)])
        assert [e["ticker"] for e in ranked] == ["B", "A", "C"]

    def test_scoreless_entries_fall_back_to_days_to_cover(self):
        ranked = rank_entries(
            [
                {"ticker": "A", "squeeze_score": None, "days_to_cover": 1.0},
                {"ticker": "B", "squeeze_score": None, "days_to_cover": 7.0},
            ]
        )
        assert [e["ticker"] for e in ranked] == ["B", "A"]


# ── Market-wide board ─────────────────────────────────────────────


class TestNormalizeBoardRows:
    def test_reads_the_snapshot_rows_defensively(self):
        payload = {
            "data": [
                {
                    "ticker": "GME",
                    "settlementDate": SETTLEMENT_LATEST,
                    "daysToCover": 6.4,
                    "shortPosition": 55_000_000,
                    "shortInterestPctOfFloat": 22.5,
                },
                {"daysToCover": 3.0},
            ]
        }
        rows = normalize_board_rows(payload, limit=10)
        assert [r["ticker"] for r in rows] == ["GME"]
        assert rows[0]["short_interest_pct"] == pytest.approx(22.5)

    def test_envelope_settlement_date_is_carried_onto_rows(self):
        # Verified live 2026-08-11: snapshot rows carry no settlementDate of
        # their own, so the board would otherwise render without a vintage.
        rows = normalize_board_rows(
            {"settlementDate": SETTLEMENT_LATEST, "data": [{"ticker": "GPGI", "daysToCover": 25.6}]}
        )
        assert rows[0]["settlement_date"] == SETTLEMENT_LATEST

    def test_respects_the_row_limit(self):
        payload = {"data": [{"ticker": f"T{i}", "daysToCover": i} for i in range(30)]}
        assert len(normalize_board_rows(payload, limit=5)) == 5


# ── Payload shape + empty gating ──────────────────────────────────


class TestPayload:
    def _payload(self):
        entry = build_ticker_entry(
            "AAPL",
            normalize_short_interest_rows(_short_interest_payload()),
            normalize_squeeze_score(_squeeze_payload()),
            today=date.today(),
        )
        return build_payload(
            entries=[entry],
            board=[],
            scan_time="2026-08-11T12:00:00Z",
            requests_used=3,
        )

    def test_payload_carries_count_vintage_and_entries(self):
        payload = self._payload()
        assert payload["count"] == 1
        assert payload["scan_time"] == "2026-08-11T12:00:00Z"
        assert payload["latest_settlement_date"] == SETTLEMENT_LATEST
        assert payload["entries"][0]["ticker"] == "AAPL"
        assert payload["requests_used"] == 3

    def test_valid_payload_passes_the_write_gate(self):
        assert is_payload_valid(self._payload()) is True

    def test_empty_payload_fails_the_write_gate(self):
        empty = build_payload(entries=[], board=[], scan_time="2026-08-11T12:00:00Z")
        assert is_payload_valid(empty) is False


# ── run(): stub client, cache gating, heartbeat ───────────────────


class _StubClient:
    def __init__(self, *, short_interest=None, squeeze=None, board=None, fail=None):
        self._short_interest = short_interest if short_interest is not None else _short_interest_payload()
        self._squeeze = squeeze if squeeze is not None else _squeeze_payload()
        self._board = board if board is not None else {"data": []}
        self._fail = fail or set()
        self.request_count = 0
        self.rate_limit_remaining = 99_000
        self.seen_tickers = []

    def get_short_interest(self, ticker, **kwargs):
        self.request_count += 1
        self.seen_tickers.append(ticker)
        if "short_interest" in self._fail:
            raise RuntimeError("boom")
        return self._short_interest

    def get_short_squeeze_scores(self, **kwargs):
        self.request_count += 1
        if "squeeze" in self._fail:
            raise RuntimeError("boom")
        return self._squeeze

    def get_short_interest_snapshot(self, **kwargs):
        self.request_count += 1
        if "board" in self._fail:
            raise RuntimeError("boom")
        return self._board

    def close(self):
        pass


class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import fetch_equibles_short_crowding as mod

        monkeypatch.setattr(mod, "SHORT_CROWDING_JSON", tmp_path / "equibles_short_crowding.json")
        self.db_writes = []
        self.health = []
        monkeypatch.setattr(mod, "_write_db_cache", lambda payload, scan_time: self.db_writes.append(payload))
        monkeypatch.setattr(
            mod, "_record_health", lambda state, scan_time, error=None: self.health.append(state)
        )
        self.mod = mod

    def test_writes_caches_and_heartbeats_ok(self):
        client = _StubClient()
        payload = self.mod.run(client=client, tickers=["AAPL"], now=datetime.now(timezone.utc))

        assert payload["count"] == 1
        assert client.seen_tickers == ["AAPL"]
        assert len(self.db_writes) == 1
        assert json.loads(self.mod.SHORT_CROWDING_JSON.read_text())["count"] == 1
        assert self.health == ["ok"]

    def test_empty_result_writes_no_cache_and_heartbeats_error(self):
        client = _StubClient(short_interest={"data": []}, squeeze={"data": []})
        payload = self.mod.run(client=client, tickers=["AAPL"], now=datetime.now(timezone.utc))

        assert payload["missing"] is True
        assert self.db_writes == []
        assert not self.mod.SHORT_CROWDING_JSON.exists()
        assert self.health == ["error"]

    def test_one_failing_ticker_does_not_abort_the_cycle(self):
        client = _StubClient(fail={"squeeze"})
        payload = self.mod.run(client=client, tickers=["AAPL"], now=datetime.now(timezone.utc))

        assert payload["count"] == 1
        assert payload["entries"][0]["squeeze_score"] is None
        assert self.health == ["ok"]

    def test_board_failure_is_non_fatal(self):
        client = _StubClient(fail={"board"})
        payload = self.mod.run(client=client, tickers=["AAPL"], now=datetime.now(timezone.utc))
        assert payload["board"] == []
        assert payload["count"] == 1

    def test_ticker_universe_is_bounded(self, monkeypatch):
        monkeypatch.setattr(self.mod, "MAX_TICKERS", 2)
        client = _StubClient()
        self.mod.run(client=client, tickers=["AAPL", "MSFT", "NVDA"], now=datetime.now(timezone.utc))
        assert client.seen_tickers == ["AAPL", "MSFT"]


# ── Migration + upserts (sqlite3 stand-in for libsql) ─────────────


class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 42
        si_cols = {r[1] for r in db.execute("PRAGMA table_info(equibles_short_interest)")}
        assert {
            "ticker",
            "settlement_date",
            "short_position",
            "change_in_short_position",
            "average_daily_volume",
            "days_to_cover",
            "recorded_at",
        } <= si_cols
        sq_cols = {r[1] for r in db.execute("PRAGMA table_info(equibles_squeeze_scores)")}
        assert {
            "ticker",
            "score",
            "rank",
            "base_score",
            "catalyst_boost",
            "short_interest_pct",
            "short_interest_pct_basis",
            "factors",
            "recorded_at",
        } <= sq_cols

    def test_indices_cover_the_route_query_patterns(self):
        db = self._db()
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_equibles_short_interest_ticker_date" in names
        assert "idx_equibles_squeeze_scores_score" in names

    def test_short_interest_upsert_is_idempotent_per_ticker_settlement(self):
        db = self._db()
        args1 = ("AAPL", SETTLEMENT_LATEST, 120e6, 20e6, 40e6, 3.0, "2026-08-11T00:00:00Z")
        args2 = ("AAPL", SETTLEMENT_LATEST, 121e6, 21e6, 41e6, 3.1, "2026-08-12T00:00:00Z")
        db.execute(SHORT_INTEREST_UPSERT_SQL, args1)
        db.execute(SHORT_INTEREST_UPSERT_SQL, args2)
        rows = db.execute(
            "SELECT ticker, short_position, days_to_cover FROM equibles_short_interest"
        ).fetchall()
        assert rows == [("AAPL", 121e6, 3.1)]

    def test_bulk_expansion_writes_many_rows_in_one_statement(self):
        # executemany is one Hrana round-trip PER ROW, so the writer expands the
        # single-row upsert instead — same ON CONFLICT semantics, one statement.
        db = self._db()
        sql = expand_values_clause(SHORT_INTEREST_UPSERT_SQL, 2)
        db.execute(
            sql,
            ("AAPL", SETTLEMENT_LATEST, 120e6, 20e6, 40e6, 3.0, "2026-08-11T00:00:00Z")
            + ("AAPL", SETTLEMENT_PRIOR, 100e6, -5e6, 50e6, 2.0, "2026-08-11T00:00:00Z"),
        )
        db.execute(
            sql,
            ("AAPL", SETTLEMENT_LATEST, 121e6, 20e6, 40e6, 3.0, "2026-08-12T00:00:00Z")
            + ("MSFT", SETTLEMENT_LATEST, 9e6, 1e6, 20e6, 0.5, "2026-08-12T00:00:00Z"),
        )
        rows = db.execute(
            "SELECT ticker, settlement_date, short_position FROM equibles_short_interest "
            "ORDER BY ticker, settlement_date"
        ).fetchall()
        assert rows == [
            ("AAPL", SETTLEMENT_PRIOR, 100e6),
            ("AAPL", SETTLEMENT_LATEST, 121e6),
            ("MSFT", SETTLEMENT_LATEST, 9e6),
        ]

    def test_squeeze_score_upsert_is_idempotent_per_ticker(self):
        db = self._db()
        factors = json.dumps({"daysToCoverPercentile": 88.0})
        db.execute(
            SQUEEZE_SCORE_UPSERT_SQL,
            ("AAPL", 78.0, 12, 70.0, 8.0, 4.23, "shares", 3.1e12, factors, SETTLEMENT_LATEST, "2026-08-11T00:00:00Z"),
        )
        db.execute(
            SQUEEZE_SCORE_UPSERT_SQL,
            ("AAPL", 81.0, 9, 72.0, 9.0, 4.5, "shares", 3.1e12, factors, SETTLEMENT_LATEST, "2026-08-12T00:00:00Z"),
        )
        rows = db.execute("SELECT ticker, score, rank FROM equibles_squeeze_scores").fetchall()
        assert rows == [("AAPL", 81.0, 9)]

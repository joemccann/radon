"""13F smart-money depth layer — vintage honesty, normalization, and storage.

RED/GREEN TDD: written before scripts/fetch_equibles_smart_money_13f.py. No
network is ever touched — a stub client returns fixture payloads shaped like
EquiblesClient output (fraction fields ALREADY normalized to '...Pct' keys by
the client boundary, so every percent here is 0-100).

Dates are window-relative (today minus N quarters) so the suite cannot rot.
"""
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import fetch_equibles_smart_money_13f as mod
from clients.equibles_client import EquiblesAuthError, EquiblesRateLimitError

MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0043_equibles_smart_money_13f.sql"


# ── window-relative fixtures ──────────────────────────────────────


def quarter_end_before(reference: date, quarters_ago: int = 0) -> date:
    """The quarter end that is `quarters_ago` quarters before `reference`."""
    quarter_index = (reference.year * 4) + ((reference.month - 1) // 3) - 1 - quarters_ago
    year, quarter = divmod(quarter_index, 4)
    month = quarter * 3 + 3
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
    return date(year, month, last_day)


TODAY = date.today()
LATEST_QUARTER = quarter_end_before(TODAY, 0).isoformat()
PRIOR_QUARTER = quarter_end_before(TODAY, 1).isoformat()


# Fixture shapes below are the LIVE Equibles response shapes, captured from the
# real API on 2026-08-11 (AAPL). They differ from the published docs in several
# load-bearing ways, which is why they are pinned here:
#   holders   -> name / value / percentOfTotal (% of the REPORTED 13F position,
#                NOT of shares outstanding; already a percent 0-100, e.g.
#                BlackRock 12.42) / positionType; rows carry NO reportDate and
#                NO per-holder delta, so meta.reportDate +
#                meta.totalInstitutions carry the aggregate
#   ownership -> institutions / totalShares / totalValue / changePercent;
#                NO filer delta, NO new/exited counts
#   activity  -> deltaShares / deltaValue / isNewPosition / isSoldOut, with
#                authoritative totalBuyers / totalSellers
#   most-held -> deltaFilerCount (not filersDelta), reportDate at top level,
#                isFilingWindowOpen
#   super     -> managerName / firmName / asOf / isStale


def holders_payload(report_date: str = LATEST_QUARTER) -> dict:
    return {
        "data": [
            {
                "name": "Berkshire Hathaway Inc",
                "cik": "1067983",
                "shares": 300_000_000,
                "value": 62_000_000_000,
                "percentOfTotal": 2.03,
                "positionType": "Common",
            },
            {
                "name": "Vanguard Group Inc",
                "cik": "102909",
                "shares": 1_300_000_000,
                "value": 268_000_000_000,
                "percentOfTotal": 8.79,
                "positionType": "Common",
            },
            {
                # Partial row: no cik, no percentOfTotal, renamed value key.
                "name": "Tiny Family Office LLC",
                "shares": 1_200,
                "marketValue": 240_000,
            },
        ],
        "meta": {
            "reportDate": report_date,
            "isCombinedQuarter": True,
            "totalInstitutions": 6_161,
            "hasMore": False,
        },
    }


def ownership_payload() -> dict:
    return {
        "data": [
            {
                "reportDate": PRIOR_QUARTER,
                "institutions": 5_100,
                "totalShares": 9_000_000_000,
                "totalValue": 1_800_000_000_000,
                "changePercent": -5.19,
                "isCombinedQuarter": False,
            },
            {
                "reportDate": LATEST_QUARTER,
                "institutions": 5_240,
                "totalShares": 9_100_000_000,
                "totalValue": 1_900_000_000_000,
                "changePercent": 0.58,
                "isCombinedQuarter": True,
            },
        ]
    }


def activity_payload() -> dict:
    return {
        "reportDate": LATEST_QUARTER,
        "previousReportDate": PRIOR_QUARTER,
        "isFilingWindowOpen": True,
        "buyers": [
            {
                "name": "Vanguard Group Inc",
                "cik": "102909",
                "previousShares": 1_296_000_000,
                "currentShares": 1_300_000_000,
                "deltaShares": 4_000_000,
                "deltaValue": 820_000_000,
                "isNewPosition": False,
                "isSoldOut": False,
            },
            {
                "name": "Brand New Capital LLC",
                "cik": "999001",
                "previousShares": 0,
                "currentShares": 500_000,
                "deltaShares": 500_000,
                "deltaValue": 100_000_000,
                "isNewPosition": True,
                "isSoldOut": False,
            },
        ],
        "sellers": [
            {
                "name": "Berkshire Hathaway Inc",
                "cik": "1067983",
                "previousShares": 320_000_000,
                "currentShares": 300_000_000,
                "deltaShares": -20_000_000,
                "deltaValue": -4_100_000_000,
                "isNewPosition": False,
                "isSoldOut": False,
            },
            {
                "name": "Gone Fishing Partners",
                "cik": "999002",
                "previousShares": 900_000,
                "currentShares": 0,
                "deltaShares": -900_000,
                "deltaValue": -180_000_000,
                "isNewPosition": False,
                "isSoldOut": True,
            },
        ],
        "totalBuyers": 1_967,
        "totalSellers": 2_205,
    }


def most_held_payload() -> dict:
    return {
        "reportDate": LATEST_QUARTER,
        "isFilingWindowOpen": True,
        "data": [
            {"ticker": "MSFT", "company": "Microsoft Corp", "filerCount": 6_000, "deltaFilerCount": 210},
            {"ticker": "AAPL", "company": "Apple Inc", "filerCount": 5_240, "deltaFilerCount": 140},
        ],
    }


def market_activity_payload(tickers: list[str]) -> dict:
    return {"data": [{"ticker": t, "company": t, "filerCount": 30} for t in tickers]}


def super_investors_payload() -> dict:
    return {
        "data": [
            {
                "managerName": "Warren Buffett",
                "firmName": "Berkshire Hathaway Inc",
                "cik": "1067983",
                "asOf": PRIOR_QUARTER,
                "isStale": False,
            },
            {"managerName": "Ray Dalio", "firmName": "Bridgewater Associates", "cik": "1350694"},
        ]
    }


class StubClient:
    """Mirrors the EquiblesClient surface this fetcher calls; counts requests."""

    def __init__(self, **overrides):
        self.calls: list[str] = []
        self.overrides = overrides
        self.request_count = 0

    def _answer(self, name: str, default):
        self.calls.append(name)
        self.request_count += 1
        value = self.overrides.get(name, default)
        if isinstance(value, Exception):
            raise value
        return value

    def get_institutional_holders(self, ticker, **kwargs):
        return self._answer("holders", holders_payload())

    def get_institutional_ownership(self, ticker, **kwargs):
        return self._answer("ownership", ownership_payload())

    def get_institutional_activity(self, ticker, **kwargs):
        return self._answer("activity", activity_payload())

    def get_13f_most_held(self, **kwargs):
        return self._answer("most_held", most_held_payload())

    def get_13f_market_activity(self, *, bucket=None, **kwargs):
        default = market_activity_payload(["AAPL"] if bucket == "new-positions" else ["XYZ"])
        return self._answer(f"market_activity:{bucket}", default)

    def get_super_investors(self):
        return self._answer("super_investors", super_investors_payload())


# ── disclosure vintage (the honesty requirement) ──────────────────


class TestDisclosureVintage:
    def test_encodes_quarter_label_and_statutory_filing_deadline(self):
        disclosure = mod.build_disclosure(LATEST_QUARTER, now=datetime.now(timezone.utc))
        quarter = (int(LATEST_QUARTER[5:7]) - 1) // 3 + 1
        assert disclosure["report_date"] == LATEST_QUARTER
        assert disclosure["quarter"] == f"Q{quarter} {LATEST_QUARTER[:4]}"
        assert disclosure["filed_by"] == (
            date.fromisoformat(LATEST_QUARTER) + timedelta(days=mod.FILING_LAG_DAYS)
        ).isoformat()

    def test_marks_the_data_context_only_and_names_the_gate(self):
        disclosure = mod.build_disclosure(LATEST_QUARTER, now=datetime.now(timezone.utc))
        assert disclosure["context_only"] is True
        assert disclosure["cadence"] == "quarterly"
        assert disclosure["lag_days"] == mod.FILING_LAG_DAYS
        assert "Gate 2" in disclosure["gate_note"]

    def test_age_days_is_measured_from_the_quarter_end(self):
        now = datetime.now(timezone.utc)
        disclosure = mod.build_disclosure(LATEST_QUARTER, now=now)
        expected = (now.date() - date.fromisoformat(LATEST_QUARTER)).days
        assert disclosure["age_days"] == expected

    def test_staleness_tiers_track_the_quarterly_replacement_cycle(self):
        now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        assert mod.build_disclosure("2026-06-30", now=now)["staleness"] == "fresh"
        assert mod.build_disclosure("2026-03-31", now=now)["staleness"] == "current"
        assert mod.build_disclosure("2025-09-30", now=now)["staleness"] == "stale"

    def test_absent_report_date_yields_no_disclosure(self):
        assert mod.build_disclosure(None, now=datetime.now(timezone.utc)) is None


# ── holder normalization ──────────────────────────────────────────


class TestNormalizeHolders:
    def test_tolerates_renamed_and_missing_fields(self):
        holders = mod.normalize_holders(holders_payload())
        tiny = next(h for h in holders if h["institution"] == "Tiny Family Office LLC")
        assert tiny["cik"] == ""
        assert tiny["value_usd"] == 240_000
        assert tiny["pct_of_institutional_total"] is None
        assert tiny["change_in_shares"] is None

    def test_ranks_by_position_value_descending(self):
        holders = mod.normalize_holders(holders_payload())
        assert [h["institution"] for h in holders] == [
            "Vanguard Group Inc",
            "Berkshire Hathaway Inc",
            "Tiny Family Office LLC",
        ]

    def test_rows_without_a_report_date_inherit_the_meta_vintage(self):
        # Live holder rows carry no reportDate; only meta does.
        assert {h["report_date"] for h in mod.normalize_holders(holders_payload())} == {
            LATEST_QUARTER
        }

    def test_the_holders_endpoint_carries_no_quarter_over_quarter_move(self):
        # Live-verified: the holders payload has no delta field at all, so a
        # change_type invented here would be fabricated. It is joined in from
        # institutional-activity instead — see attach_holder_activity.
        assert all(h["change_type"] is None for h in mod.normalize_holders(holders_payload()))

    def test_percent_of_institutional_total_is_a_percent_not_a_fraction(self):
        # percentOfTotal is the filer's share of the REPORTED 13F position and
        # already arrives 0-100, so it is not in the client's fraction registry.
        by_name = {h["institution"]: h for h in mod.normalize_holders(holders_payload())}
        assert by_name["Vanguard Group Inc"]["pct_of_institutional_total"] == 8.79

    def test_empty_payload_is_an_empty_list_not_a_crash(self):
        assert mod.normalize_holders({}) == []
        assert mod.normalize_holders({"data": None}) == []


class TestAttachHolderActivity:
    def _joined(self):
        holders = mod.normalize_holders(holders_payload())
        return {
            h["institution"]: h
            for h in mod.attach_holder_activity(holders, mod.normalize_activity(activity_payload()))
        }

    def test_joins_the_qoq_move_onto_the_matching_filer(self):
        joined = self._joined()
        assert joined["Vanguard Group Inc"]["change_type"] == "ADDED"
        assert joined["Vanguard Group Inc"]["change_in_shares"] == 4_000_000
        assert joined["Berkshire Hathaway Inc"]["change_type"] == "TRIMMED"

    def test_a_holder_outside_the_movers_page_stays_unknown_not_held(self):
        assert self._joined()["Tiny Family Office LLC"]["change_type"] is None


# ── ownership series ──────────────────────────────────────────────


class TestOwnershipSeries:
    def test_computes_the_qoq_filer_delta_when_the_source_omits_it(self):
        series = mod.normalize_ownership_series(ownership_payload())
        assert [r["report_date"] for r in series] == [PRIOR_QUARTER, LATEST_QUARTER]
        assert series[0]["filer_count_delta"] is None  # no prior quarter to diff
        assert series[1]["filer_count_delta"] == 140

    def test_prefers_a_source_supplied_delta(self):
        payload = ownership_payload()
        payload["data"][1]["filerCountDelta"] = 999
        series = mod.normalize_ownership_series(payload)
        assert series[1]["filer_count_delta"] == 999

    def test_carries_the_quarter_over_quarter_share_change(self):
        latest = mod.normalize_ownership_series(ownership_payload())[-1]
        assert latest["shares_qoq_pct"] == 0.58
        assert latest["combined_quarter"] is True

    def test_position_churn_is_absent_from_this_endpoint_not_faked(self):
        # Live-verified: institutional-ownership publishes no new/exited counts.
        # The headline sources those from institutional-activity instead.
        latest = mod.normalize_ownership_series(ownership_payload())[-1]
        assert latest["new_positions"] is None
        assert latest["exited_positions"] is None


# ── activity, super investors, market context ─────────────────────


class TestActivity:
    def test_splits_largest_buyers_from_largest_sellers(self):
        activity = mod.normalize_activity(activity_payload())
        assert [b["institution"] for b in activity["buyers"]] == [
            "Vanguard Group Inc",
            "Brand New Capital LLC",
        ]
        assert [s["institution"] for s in activity["sellers"]] == [
            "Berkshire Hathaway Inc",
            "Gone Fishing Partners",
        ]

    def test_uses_the_endpoints_authoritative_participant_totals(self):
        activity = mod.normalize_activity(activity_payload())
        assert activity["buyer_count"] == 1_967
        assert activity["seller_count"] == 2_205

    def test_page_scoped_churn_counts_are_labelled_as_page_scoped(self):
        # totalBuyers/totalSellers are universe-wide, but new/exited can only be
        # counted off the returned movers page. Saying so is the honest move.
        activity = mod.normalize_activity(activity_payload())
        assert activity["new_positions"] == 1
        assert activity["exited_positions"] == 1
        assert activity["positions_scope"] == "top-movers"

    def test_missing_payload_degrades_to_empty_sides(self):
        activity = mod.normalize_activity(None)
        assert activity["buyers"] == []
        assert activity["sellers"] == []
        assert activity["buyer_count"] is None


class TestSuperInvestorPositions:
    def test_matches_a_roster_manager_to_its_position_by_cik(self):
        holders = mod.normalize_holders(holders_payload())
        named = mod.super_investor_positions(super_investors_payload(), holders)
        assert len(named) == 1
        assert named[0]["investor"] == "Warren Buffett"
        assert named[0]["institution"] == "Berkshire Hathaway Inc"
        assert named[0]["shares"] == 300_000_000

    def test_falls_back_to_a_normalized_institution_name_when_cik_is_absent(self):
        holders = mod.normalize_holders(holders_payload())
        roster = {"data": [{"name": "Vanguard Group, Inc."}]}
        named = mod.super_investor_positions(roster, holders)
        assert [n["institution"] for n in named] == ["Vanguard Group Inc"]

    def test_roster_managers_without_a_position_are_not_invented(self):
        holders = mod.normalize_holders(holders_payload())
        named = mod.super_investor_positions(super_investors_payload(), holders)
        assert all(n["investor"] != "Bridgewater Associates" for n in named)


class TestMarketContext:
    def test_locates_the_ticker_in_the_most_held_filer_delta_ranking(self):
        context = mod.build_market_context("AAPL", mod.fetch_market_snapshot(StubClient()))
        assert context["most_held_rank"] == 2
        assert context["most_held_filers_delta"] == 140

    def test_flags_bucket_membership(self):
        context = mod.build_market_context("AAPL", mod.fetch_market_snapshot(StubClient()))
        assert context["in_new_positions_bucket"] is True
        assert context["in_sold_out_bucket"] is False

    def test_unlisted_ticker_reads_as_absent_not_as_zero(self):
        context = mod.build_market_context("ZZZZ", mod.fetch_market_snapshot(StubClient()))
        assert context["most_held_rank"] is None
        assert context["most_held_filers_delta"] is None


class TestMarketSnapshotResilience:
    def test_one_dead_endpoint_does_not_take_the_surface_down(self):
        client = StubClient(super_investors=RuntimeError("503"))
        snapshot = mod.fetch_market_snapshot(client)
        assert snapshot["super_investors"] == []
        assert snapshot["most_held"]  # the healthy sources still landed

    def test_market_snapshot_is_fetched_once_for_many_tickers(self):
        client = StubClient()
        mod.run_many(["AAPL", "MSFT"], client=client, persist=False)
        assert client.calls.count("most_held") == 1
        assert client.calls.count("super_investors") == 1
        assert client.calls.count("holders") == 2


# ── payload assembly ──────────────────────────────────────────────


class TestBuildPayload:
    def _payload(self, client=None):
        client = client or StubClient()
        return mod.build_payload("AAPL", client=client, market=mod.fetch_market_snapshot(client))

    def test_headline_carries_the_named_metrics(self):
        headline = self._payload()["headline"]
        assert headline["filer_count"] == 5_240
        assert headline["filer_count_delta"] == 140
        assert headline["buyer_count"] == 1_967
        assert headline["seller_count"] == 2_205
        assert headline["new_positions"] == 1
        assert headline["exited_positions"] == 1
        assert headline["positions_scope"] == "top-movers"

    def test_top_holders_are_ranked_by_value_and_bounded(self):
        payload = self._payload()
        assert payload["headline"]["top_holders"][0]["institution"] == "Vanguard Group Inc"
        assert len(payload["holders"]) <= mod.MAX_HOLDERS_IN_PAYLOAD

    def test_named_super_investor_positions_reach_the_headline(self):
        assert self._payload()["headline"]["super_investors"][0]["investor"] == "Warren Buffett"

    def test_vintage_is_encoded_in_the_payload_not_only_the_view(self):
        payload = self._payload()
        assert payload["disclosure"]["report_date"] == LATEST_QUARTER
        assert payload["disclosure"]["context_only"] is True
        assert payload["report_date"] == LATEST_QUARTER

    def test_complements_rather_than_duplicates_the_uw_summary(self):
        # fetch_informed_flow.py already owns the thin UW institutional summary
        # ({ownership_pct, total_institutions, report_date}). This payload must
        # add position-level depth, not restate that shape.
        payload = self._payload()
        assert "institutional_summary" not in payload
        assert payload["holders"] and "shares" in payload["holders"][0]


class TestSignalGate:
    def test_a_structurally_empty_payload_is_not_a_signal(self):
        client = StubClient(
            holders={"data": []},
            ownership={"data": []},
            activity={"buyers": [], "sellers": []},
        )
        payload = mod.build_payload("ZZZZ", client=client, market=mod.fetch_market_snapshot(client))
        assert mod.has_signal(payload) is False

    def test_holders_alone_are_enough_to_count_as_a_signal(self):
        client = StubClient(ownership={"data": []}, activity=None)
        payload = mod.build_payload("AAPL", client=client, market=mod.fetch_market_snapshot(client))
        assert mod.has_signal(payload) is True


# ── run(): persistence, heartbeat, empty-result gating ────────────


class _Recorder:
    def __init__(self):
        self.rows = []
        self.snapshots = []
        self.health = []

    def install(self, monkeypatch):
        monkeypatch.setattr(mod, "_upsert_holder_rows", lambda *a, **k: self.rows.append(a))
        monkeypatch.setattr(mod, "_upsert_snapshot", lambda *a, **k: self.snapshots.append(a))
        monkeypatch.setattr(mod, "_record_health", lambda *a, **k: self.health.append(a))


@pytest.fixture
def recorder(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "equibles_13f")
    rec = _Recorder()
    rec.install(monkeypatch)
    return rec


class TestRun:
    def test_writes_rows_snapshot_disk_cache_and_heartbeat(self, recorder):
        payload = mod.run("AAPL", client=StubClient())
        assert payload["ticker"] == "AAPL"
        assert recorder.rows and recorder.snapshots
        assert recorder.health == [("ok",)]
        cached = json.loads((mod.CACHE_DIR / "AAPL.json").read_text())
        assert cached["report_date"] == LATEST_QUARTER

    def test_empty_result_is_never_cached_but_still_heartbeats(self, recorder):
        client = StubClient(holders={"data": []}, ownership={"data": []}, activity=None)
        payload = mod.run("ZZZZ", client=client)
        assert payload["missing"] is True
        assert recorder.rows == []
        assert recorder.snapshots == []
        assert recorder.health == [("ok",)]
        assert not (mod.CACHE_DIR / "ZZZZ.json").exists()

    def test_a_dead_single_endpoint_still_heartbeats_ok(self, recorder):
        # One dead section is not a dead cycle: the surface degrades, the
        # writer stays healthy, and the payload still carries what landed.
        payload = mod.run("AAPL", client=StubClient(activity=RuntimeError("503")))
        assert payload["activity"]["buyers"] == []
        assert payload["activity"]["sellers"] == []
        assert payload["holders"]
        assert recorder.health == [("ok",)]

    def test_a_rejected_key_is_cycle_fatal_and_heartbeats_error(self, recorder):
        # A 401/429 makes EVERY call fail; swallowing it would cache an empty
        # payload and report 'ok', hiding a dead integration.
        client = StubClient(holders=EquiblesAuthError("unauthorized", status_code=401))
        with pytest.raises(EquiblesAuthError):
            mod.run("AAPL", client=client)
        assert recorder.health == [("error",)]

    def test_an_exhausted_daily_allowance_is_cycle_fatal(self, recorder):
        client = StubClient(most_held=EquiblesRateLimitError("rate limited", status_code=429))
        with pytest.raises(EquiblesRateLimitError):
            mod.run("AAPL", client=client)
        assert recorder.health == [("error",)]

    def test_reports_the_billed_request_count(self, recorder):
        client = StubClient()
        payload = mod.run("AAPL", client=client)
        assert payload["requests_used"] == client.request_count


# ── migration + storage ───────────────────────────────────────────


class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version_43(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 43

    def test_holder_table_columns(self):
        cols = {r[1] for r in self._db().execute("PRAGMA table_info(equibles_13f_holders)")}
        assert {
            "ticker", "report_date", "cik", "institution", "shares", "value_usd",
            "pct_of_institutional_total", "position_type", "change_in_shares",
            "change_type", "recorded_at",
        } <= cols

    def test_top_holders_index_exists_for_the_by_value_read(self):
        names = {r[1] for r in self._db().execute("PRAGMA index_list(equibles_13f_holders)")}
        assert "idx_equibles_13f_holders_by_value" in names

    def test_holder_upsert_is_idempotent_per_ticker_quarter_filer(self):
        db = self._db()
        first = ("AAPL", LATEST_QUARTER, "102909", "Vanguard Group Inc",
                 1_300_000_000.0, 268e9, 8.79, "Common", 4e6, "ADDED", "2026-08-11T00:00:00Z")
        second = ("AAPL", LATEST_QUARTER, "102909", "Vanguard Group Inc",
                  1_310_000_000.0, 270e9, 8.85, "Common", 5e6, "ADDED", "2026-08-12T00:00:00Z")
        db.execute(mod.HOLDER_UPSERT_SQL, first)
        db.execute(mod.HOLDER_UPSERT_SQL, second)
        rows = db.execute(
            "SELECT shares, pct_of_institutional_total FROM equibles_13f_holders"
        ).fetchall()
        assert rows == [(1_310_000_000.0, 8.85)]

    def test_a_new_quarter_lands_beside_the_old_vintage(self):
        db = self._db()
        base = ("AAPL", None, "102909", "Vanguard Group Inc",
                1.0, 1.0, 1.0, "Common", 0.0, "HELD", "t")
        db.execute(mod.HOLDER_UPSERT_SQL, (*base[:1], PRIOR_QUARTER, *base[2:]))
        db.execute(mod.HOLDER_UPSERT_SQL, (*base[:1], LATEST_QUARTER, *base[2:]))
        assert db.execute("SELECT COUNT(*) FROM equibles_13f_holders").fetchone()[0] == 2

    def test_chunked_multi_row_upsert_writes_every_holder(self):
        # Hrana bounding: a liquid name's holders land as chunked multi-row
        # INSERTs, never one statement per row.
        db = self._db()
        holders = [
            ("AAPL", LATEST_QUARTER, str(i), f"Filer {i}",
             1.0, float(i), 1.0, "Common", 0.0, "HELD", "t")
            for i in range(3)
        ]
        db.execute(mod._holder_upsert_sql(len(holders)), tuple(v for row in holders for v in row))
        assert db.execute("SELECT COUNT(*) FROM equibles_13f_holders").fetchone()[0] == 3

    def test_snapshot_upsert_is_idempotent_per_ticker_quarter(self):
        db = self._db()
        db.execute(mod.SNAPSHOT_UPSERT_SQL, ("AAPL", LATEST_QUARTER, "t1", '{"v":1}'))
        db.execute(mod.SNAPSHOT_UPSERT_SQL, ("AAPL", LATEST_QUARTER, "t2", '{"v":2}'))
        rows = db.execute("SELECT scan_time, payload FROM equibles_13f_snapshots").fetchall()
        assert rows == [("t2", '{"v":2}')]

"""Pre-trade filing-forensics dossier — flag derivation, absence semantics, storage.

The load-bearing behaviour under test is the three-way distinction the feature
exists for:

  flagged      a filing exists AND breaches a named threshold
  clear        filings exist and none breach       -> genuinely no overhang
  no-filings   the source answered with zero rows  -> nothing on file
  unavailable  the source failed                   -> we do NOT know

A fetch failure or an empty result must never render as an all-clear, and every
flagged check must cite the filing it came from.

No live network: every test drives a stub client.
"""
import json
import sqlite3
from datetime import date
from typing import Optional
from pathlib import Path

import pytest

from clients.equibles_client import EquiblesServerError
from fetch_equibles_filing_forensics import (
    ATM_REMAINING_MATERIAL_USD,
    BUYBACK_REMAINING_MATERIAL_USD,
    CHECK_BUYBACK_SUPPORT,
    CHECK_DILUTION_OVERHANG,
    CHECK_EXEC_CHURN,
    CHECK_GOING_CONCERN,
    CHECK_INSIDER_SUPPLY,
    EQUIBLES_FILING_FORENSICS_UPSERT_SQL,
    EXEC_CHURN_LOOKBACK_DAYS,
    EXEC_CHURN_MIN_DEPARTURES,
    FORM_144_LOOKBACK_DAYS,
    FORM_144_MATERIAL_USD,
    SERVICE_NAME,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    VERDICT_CLEAR,
    VERDICT_FLAGGED,
    VERDICT_NO_FILINGS,
    VERDICT_UNAVAILABLE,
    GoingConcernUniverse,
    SourceResult,
    build_dossier,
    fetch_dossier,
    fetch_going_concern_universe,
    has_usable_data,
    run,
)

MIGRATION = (
    Path(__file__).parents[1] / "db" / "migrations" / "0045_equibles_filing_forensics.sql"
)

TODAY = date(2026, 8, 11)


def _days_ago(days: int) -> str:
    return (TODAY - __import__("datetime").timedelta(days=days)).isoformat()


# ── fixtures: documented Equibles payload shapes ──────────────────


def _proposed_sale(value: float, days: int = 5, **extra) -> dict:
    return {
        "filingDate": _days_ago(days),
        "sellerName": "Jane Insider",
        "relationshipToIssuer": "Officer",
        "sharesToBeSold": 100_000,
        "aggregateMarketValue": value,
        "approxSaleDate": _days_ago(days - 2),
        "brokerName": "Morgan Stanley",
        "filingUrl": "https://sec.gov/144.htm",
        **extra,
    }


def _atm_program(remaining: float, **extra) -> dict:
    return {
        "announcedDate": _days_ago(200),
        "lastAmendedDate": _days_ago(30),
        "capacity": 100_000_000.0,
        "soldAmount": 100_000_000.0 - remaining,
        "remainingAmount": remaining,
        "isFullyUtilized": False,
        "isExpired": False,
        "latestForm": "424B5",
        "latestFilingUrl": "https://sec.gov/atm.htm",
        **extra,
    }


def _buyback_program(remaining: Optional[float], **extra) -> dict:
    return {
        "announcedDate": _days_ago(90),
        "authorizedAmount": 500_000_000.0,
        "currency": "USD",
        "expiresDate": None,
        "remainingAmount": remaining,
        "remainingAsOf": _days_ago(40),
        "latestForm": "8-K",
        "latestFilingUrl": "https://sec.gov/8k.htm",
        **extra,
    }


def _buyback_source(remaining: Optional[float], envelope_remaining: Optional[float] = None) -> SourceResult:
    """Live shape: programs[] beside an envelope-level remainingAuthorizedAmount."""
    return SourceResult(
        STATUS_OK,
        [_buyback_program(remaining)],
        envelope={
            "ticker": "AAPL",
            "remainingAuthorizedAmount": envelope_remaining,
            "programs": [_buyback_program(remaining)],
        },
    )


def _departure(days: int, action: str = "Resigned", name: str = "Chris Exec", **extra) -> dict:
    return {
        "personName": name,
        "role": "CFO",
        "roleText": "Chief Financial Officer",
        "action": action,
        "filedDate": _days_ago(days),
        "effectiveDate": _days_ago(days - 1),
        "disclosure": "Item 5.02",
        "form": "8-K",
        "filingUrl": "https://sec.gov/8k-502.htm",
        **extra,
    }


def _sources(**overrides) -> dict[str, SourceResult]:
    base = {
        "proposed_sales": SourceResult(STATUS_EMPTY, []),
        "atm_programs": SourceResult(STATUS_EMPTY, []),
        "buyback_programs": SourceResult(STATUS_EMPTY, []),
        "executive_changes": SourceResult(STATUS_EMPTY, []),
    }
    base.update(overrides)
    return base


def _clean_universe(*tickers: str) -> GoingConcernUniverse:
    return GoingConcernUniverse(status=STATUS_OK, tickers=set(tickers), truncated=False)


def _dossier(**kwargs) -> dict:
    sources = kwargs.pop("sources", None) or _sources()
    universe = kwargs.pop("going_concern", None) or _clean_universe()
    return build_dossier(
        "AAPL", sources, universe, as_of="2026-08-11T13:00:00Z", today=TODAY
    )


def _check(dossier: dict, code: str) -> dict:
    return next(c for c in dossier["checks"] if c["code"] == code)


# ── flag derivation ───────────────────────────────────────────────


class TestDilutionOverhang:
    def test_large_unused_shelf_is_flagged_with_its_figure(self):
        remaining = ATM_REMAINING_MATERIAL_USD * 4
        dossier = _dossier(
            sources=_sources(atm_programs=SourceResult(STATUS_OK, [_atm_program(remaining)]))
        )
        check = _check(dossier, CHECK_DILUTION_OVERHANG)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["figure"]["value"] == pytest.approx(remaining)
        assert check["gate"] == 3

    def test_remaining_below_threshold_is_clear_not_flagged(self):
        dossier = _dossier(
            sources=_sources(
                atm_programs=SourceResult(
                    STATUS_OK, [_atm_program(ATM_REMAINING_MATERIAL_USD - 1)]
                )
            )
        )
        assert _check(dossier, CHECK_DILUTION_OVERHANG)["verdict"] == VERDICT_CLEAR

    def test_fully_utilized_and_expired_shelves_are_not_overhang(self):
        rows = [
            _atm_program(ATM_REMAINING_MATERIAL_USD * 9, isFullyUtilized=True),
            _atm_program(ATM_REMAINING_MATERIAL_USD * 9, isExpired=True),
        ]
        dossier = _dossier(sources=_sources(atm_programs=SourceResult(STATUS_OK, rows)))
        assert _check(dossier, CHECK_DILUTION_OVERHANG)["verdict"] == VERDICT_CLEAR

    def test_remaining_amount_split_into_currency_and_share_units(self):
        # Equibles reports remaining capacity in separate currency/share units;
        # only the currency leg is dollars.
        row = _atm_program(0.0)
        row["remainingAmount"] = {
            "currency": ATM_REMAINING_MATERIAL_USD * 3,
            "shares": 4_000_000,
        }
        dossier = _dossier(sources=_sources(atm_programs=SourceResult(STATUS_OK, [row])))
        check = _check(dossier, CHECK_DILUTION_OVERHANG)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["figure"]["value"] == pytest.approx(ATM_REMAINING_MATERIAL_USD * 3)

    def test_multiple_shelves_sum_into_one_figure(self):
        rows = [
            _atm_program(ATM_REMAINING_MATERIAL_USD * 2),
            _atm_program(ATM_REMAINING_MATERIAL_USD * 3),
        ]
        dossier = _dossier(sources=_sources(atm_programs=SourceResult(STATUS_OK, rows)))
        assert _check(dossier, CHECK_DILUTION_OVERHANG)["figure"]["value"] == pytest.approx(
            ATM_REMAINING_MATERIAL_USD * 5
        )


class TestInsiderSupplyPending:
    def test_recent_material_form_144_is_flagged(self):
        dossier = _dossier(
            sources=_sources(
                proposed_sales=SourceResult(
                    STATUS_OK, [_proposed_sale(FORM_144_MATERIAL_USD * 3, days=10)]
                )
            )
        )
        check = _check(dossier, CHECK_INSIDER_SUPPLY)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["gate"] == 2

    def test_filings_outside_the_lookback_window_do_not_flag(self):
        stale = FORM_144_LOOKBACK_DAYS + 30
        dossier = _dossier(
            sources=_sources(
                proposed_sales=SourceResult(
                    STATUS_OK, [_proposed_sale(FORM_144_MATERIAL_USD * 9, days=stale)]
                )
            )
        )
        assert _check(dossier, CHECK_INSIDER_SUPPLY)["verdict"] == VERDICT_CLEAR

    def test_aggregate_value_below_threshold_is_clear(self):
        dossier = _dossier(
            sources=_sources(
                proposed_sales=SourceResult(
                    STATUS_OK, [_proposed_sale(FORM_144_MATERIAL_USD / 4, days=1)]
                )
            )
        )
        assert _check(dossier, CHECK_INSIDER_SUPPLY)["verdict"] == VERDICT_CLEAR

    def test_headline_names_form_144_as_forward_looking(self):
        dossier = _dossier(
            sources=_sources(
                proposed_sales=SourceResult(
                    STATUS_OK, [_proposed_sale(FORM_144_MATERIAL_USD * 2)]
                )
            )
        )
        check = _check(dossier, CHECK_INSIDER_SUPPLY)
        assert "144" in check["filing"]["form"]
        assert "before" in check["detail"].lower()


class TestBuybackSupport:
    def test_material_remaining_authorization_is_flagged_as_supportive(self):
        dossier = _dossier(
            sources=_sources(
                buyback_programs=_buyback_source(BUYBACK_REMAINING_MATERIAL_USD * 10)
            )
        )
        check = _check(dossier, CHECK_BUYBACK_SUPPORT)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["tone"] == "supportive"

    def test_exhausted_authorization_is_clear(self):
        dossier = _dossier(
            sources=_sources(buyback_programs=_buyback_source(0.0))
        )
        assert _check(dossier, CHECK_BUYBACK_SUPPORT)["verdict"] == VERDICT_CLEAR


class TestExecChurn:
    def test_two_departures_inside_the_window_flag(self):
        rows = [_departure(10), _departure(60, action="Terminated", name="Pat Exec")]
        assert EXEC_CHURN_MIN_DEPARTURES == 2
        dossier = _dossier(sources=_sources(executive_changes=SourceResult(STATUS_OK, rows)))
        check = _check(dossier, CHECK_EXEC_CHURN)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["figure"]["value"] == 2

    def test_appointments_are_not_departures(self):
        rows = [_departure(10, action="Appointed"), _departure(20, action="Appointed")]
        dossier = _dossier(sources=_sources(executive_changes=SourceResult(STATUS_OK, rows)))
        assert _check(dossier, CHECK_EXEC_CHURN)["verdict"] == VERDICT_CLEAR

    def test_departures_older_than_the_window_do_not_count(self):
        stale = EXEC_CHURN_LOOKBACK_DAYS + 5
        rows = [_departure(stale), _departure(stale + 10)]
        dossier = _dossier(sources=_sources(executive_changes=SourceResult(STATUS_OK, rows)))
        assert _check(dossier, CHECK_EXEC_CHURN)["verdict"] == VERDICT_CLEAR


class TestGoingConcern:
    def test_membership_in_the_screener_universe_flags(self):
        dossier = _dossier(going_concern=_clean_universe("AAPL", "ZZZZ"))
        check = _check(dossier, CHECK_GOING_CONCERN)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["gate"] == 3

    def test_absence_from_a_complete_universe_is_clear(self):
        assert _check(_dossier(), CHECK_GOING_CONCERN)["verdict"] == VERDICT_CLEAR

    def test_absence_from_a_truncated_universe_is_unavailable_not_clear(self):
        universe = GoingConcernUniverse(status=STATUS_OK, tickers={"ZZZZ"}, truncated=True)
        dossier = _dossier(going_concern=universe)
        assert _check(dossier, CHECK_GOING_CONCERN)["verdict"] == VERDICT_UNAVAILABLE

    def test_screener_failure_is_unavailable(self):
        universe = GoingConcernUniverse(status=STATUS_ERROR, tickers=set(), truncated=False, error="503")
        dossier = _dossier(going_concern=universe)
        assert _check(dossier, CHECK_GOING_CONCERN)["verdict"] == VERDICT_UNAVAILABLE


# ── absence semantics (the point of the feature) ──────────────────


class TestAbsenceIsNotAnAllClear:
    def test_empty_source_reads_as_no_filings_found(self):
        check = _check(_dossier(), CHECK_DILUTION_OVERHANG)
        assert check["verdict"] == VERDICT_NO_FILINGS
        assert check["detail"] == "No filings found"

    def test_failed_source_reads_as_unavailable_never_clear(self):
        dossier = _dossier(
            sources=_sources(atm_programs=SourceResult(STATUS_ERROR, [], error="boom"))
        )
        check = _check(dossier, CHECK_DILUTION_OVERHANG)
        assert check["verdict"] == VERDICT_UNAVAILABLE
        assert check["verdict"] not in (VERDICT_CLEAR, VERDICT_NO_FILINGS)
        assert dossier["data_complete"] is False

    def test_no_filings_and_clear_are_distinct_verdicts(self):
        assert VERDICT_NO_FILINGS != VERDICT_CLEAR

    def test_all_sources_healthy_marks_the_dossier_complete(self):
        assert _dossier()["data_complete"] is True

    def test_flags_only_contain_flagged_checks(self):
        dossier = _dossier(
            sources=_sources(
                atm_programs=SourceResult(
                    STATUS_OK, [_atm_program(ATM_REMAINING_MATERIAL_USD * 5)]
                ),
                buyback_programs=SourceResult(STATUS_ERROR, [], error="boom"),
            )
        )
        assert [f["code"] for f in dossier["flags"]] == [CHECK_DILUTION_OVERHANG]
        assert dossier["flag_count"] == 1


class TestEveryFlagCitesItsFiling:
    def test_each_flagged_check_carries_a_source_filing_url(self):
        dossier = _dossier(
            sources=_sources(
                atm_programs=SourceResult(
                    STATUS_OK, [_atm_program(ATM_REMAINING_MATERIAL_USD * 5)]
                ),
                proposed_sales=SourceResult(
                    STATUS_OK, [_proposed_sale(FORM_144_MATERIAL_USD * 5)]
                ),
                buyback_programs=_buyback_source(BUYBACK_REMAINING_MATERIAL_USD * 5),
                executive_changes=SourceResult(
                    STATUS_OK, [_departure(5), _departure(15, name="Pat Exec")]
                ),
            )
        )
        assert len(dossier["flags"]) == 4
        for flag in dossier["flags"]:
            assert flag["filing"]["url"], flag["code"]
            assert flag["filing"]["form"], flag["code"]

    def test_missing_urls_degrade_without_crashing(self):
        row = _atm_program(ATM_REMAINING_MATERIAL_USD * 5)
        row.pop("latestFilingUrl")
        row.pop("latestForm")
        dossier = _dossier(sources=_sources(atm_programs=SourceResult(STATUS_OK, [row])))
        check = _check(dossier, CHECK_DILUTION_OVERHANG)
        assert check["verdict"] == VERDICT_FLAGGED
        assert check["filing"]["url"] is None


class TestDefensiveParsing:
    def test_rows_missing_every_documented_field_do_not_crash(self):
        dossier = _dossier(
            sources=_sources(
                atm_programs=SourceResult(STATUS_OK, [{}]),
                proposed_sales=SourceResult(STATUS_OK, [{}]),
                buyback_programs=SourceResult(STATUS_OK, [{}]),
                executive_changes=SourceResult(STATUS_OK, [{}]),
            )
        )
        assert {c["verdict"] for c in dossier["checks"]} <= {
            VERDICT_CLEAR,
            VERDICT_UNAVAILABLE,
        }

    def test_string_amounts_are_coerced(self):
        row = _atm_program(0.0)
        row["remainingAmount"] = str(ATM_REMAINING_MATERIAL_USD * 2)
        dossier = _dossier(sources=_sources(atm_programs=SourceResult(STATUS_OK, [row])))
        assert _check(dossier, CHECK_DILUTION_OVERHANG)["verdict"] == VERDICT_FLAGGED


# ── client wiring ─────────────────────────────────────────────────


class _StubClient:
    """Minimal stand-in for EquiblesClient with per-endpoint scripting."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls: list[str] = []

    def _resolve(self, name):
        self.calls.append(name)
        value = self.responses.get(name, {"data": []})
        if isinstance(value, Exception):
            raise value
        return value

    def get_proposed_sales(self, ticker, **kwargs):
        return self._resolve("proposed_sales")

    def get_atm_programs(self, ticker):
        return self._resolve("atm_programs")

    def get_buyback_programs(self, ticker):
        return self._resolve("buyback_programs")

    def get_executive_changes(self, ticker, **kwargs):
        return self._resolve("executive_changes")

    def get_stock_screener(self, **filters):
        return self._resolve("screener")


class TestFetchDossier:
    def test_maps_every_endpoint_into_a_source_status(self):
        client = _StubClient(
            proposed_sales={"data": [_proposed_sale(FORM_144_MATERIAL_USD * 2)]},
            atm_programs={"data": []},
            buyback_programs=EquiblesServerError("503", status_code=503),
            executive_changes={"data": [_departure(3)]},
        )
        dossier = fetch_dossier(client, "AAPL", _clean_universe(), today=TODAY)
        statuses = {k: v["status"] for k, v in dossier["sources"].items()}
        assert statuses["proposed_sales"] == STATUS_OK
        assert statuses["atm_programs"] == STATUS_EMPTY
        assert statuses["buyback_programs"] == STATUS_ERROR
        assert statuses["executive_changes"] == STATUS_OK

    def test_programs_envelope_rows_are_extracted(self):
        # Live shape: /buyback-programs answers with programs[] beside envelope
        # fields, not a `data` list.
        client = _StubClient(
            buyback_programs={
                "ticker": "AAPL",
                "remainingAuthorizedAmount": None,
                "programs": [_buyback_program(BUYBACK_REMAINING_MATERIAL_USD * 4)],
            }
        )
        dossier = fetch_dossier(client, "AAPL", _clean_universe(), today=TODAY)
        assert _check(dossier, CHECK_BUYBACK_SUPPORT)["verdict"] == VERDICT_FLAGGED

    def test_ticker_is_uppercased_on_the_dossier(self):
        dossier = fetch_dossier(_StubClient(), "aapl", _clean_universe(), today=TODAY)
        assert dossier["ticker"] == "AAPL"


class TestGoingConcernUniverse:
    def test_collects_the_screener_tickers(self):
        client = _StubClient(
            screener={"totalCount": 2, "count": 2, "data": [{"ticker": "aa"}, {"ticker": "BB"}]}
        )
        universe = fetch_going_concern_universe(client)
        assert universe.tickers == {"AA", "BB"}
        assert universe.truncated is False

    def test_flags_truncation_when_the_screener_has_more_rows(self):
        client = _StubClient(screener={"totalCount": 900, "count": 1, "data": [{"ticker": "AA"}]})
        assert fetch_going_concern_universe(client).truncated is True

    def test_screener_failure_is_captured_not_raised(self):
        client = _StubClient(screener=EquiblesServerError("500", status_code=500))
        universe = fetch_going_concern_universe(client)
        assert universe.status == STATUS_ERROR
        assert universe.tickers == set()


# ── write gating + run() ──────────────────────────────────────────


class TestWriteGating:
    def test_dossier_with_any_answering_source_is_usable(self):
        assert has_usable_data(_dossier()) is True

    def test_dossier_whose_every_source_failed_is_not_usable(self):
        dossier = _dossier(
            sources={
                name: SourceResult(STATUS_ERROR, [], error="boom")
                for name in ("proposed_sales", "atm_programs", "buyback_programs", "executive_changes")
            },
            going_concern=GoingConcernUniverse(STATUS_ERROR, set(), False, "boom"),
        )
        assert has_usable_data(dossier) is False


class TestRun:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import fetch_equibles_filing_forensics as mod

        monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "equibles_filing_forensics")
        self.db_writes: list[dict] = []
        self.health: list[tuple[str, str]] = []
        monkeypatch.setattr(mod, "_write_db_row", lambda dossier: self.db_writes.append(dossier))
        monkeypatch.setattr(
            mod, "_record_health", lambda state, error=None: self.health.append((state, error))
        )
        self.mod = mod

    def test_persists_each_usable_dossier_to_db_and_disk(self):
        client = _StubClient(atm_programs={"data": [_atm_program(ATM_REMAINING_MATERIAL_USD * 3)]})
        result = self.mod.run(["AAPL", "MSFT"], client=client, today=TODAY)

        assert result["persisted"] == 2
        assert [d["ticker"] for d in self.db_writes] == ["AAPL", "MSFT"]
        cached = json.loads((self.mod.CACHE_DIR / "AAPL.json").read_text())
        assert cached["flag_count"] == 1

    def test_never_caches_an_all_sources_failed_dossier(self):
        boom = EquiblesServerError("503", status_code=503)
        client = _StubClient(
            proposed_sales=boom,
            atm_programs=boom,
            buyback_programs=boom,
            executive_changes=boom,
            screener=boom,
        )
        result = self.mod.run(["AAPL"], client=client, today=TODAY)

        assert result["persisted"] == 0
        assert result["skipped"] == ["AAPL"]
        assert self.db_writes == []
        assert not (self.mod.CACHE_DIR / "AAPL.json").exists()

    def test_heartbeats_ok_on_every_successful_cycle(self):
        self.mod.run(["AAPL"], client=_StubClient(), today=TODAY)
        assert self.health == [("ok", None)]

    def test_heartbeats_error_when_nothing_could_be_persisted(self):
        boom = EquiblesServerError("503", status_code=503)
        self.mod.run(
            ["AAPL"],
            client=_StubClient(
                proposed_sales=boom,
                atm_programs=boom,
                buyback_programs=boom,
                executive_changes=boom,
                screener=boom,
            ),
            today=TODAY,
        )
        assert self.health[0][0] == "error"

    def test_going_concern_universe_is_fetched_once_per_cycle(self):
        client = _StubClient()
        self.mod.run(["AAPL", "MSFT", "NVDA"], client=client, today=TODAY)
        assert client.calls.count("screener") == 1


# ── migration + upsert ────────────────────────────────────────────


class TestStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version_45(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 45
        cols = {r[1] for r in db.execute("PRAGMA table_info(equibles_filing_forensics)")}
        assert {"ticker", "as_of", "flag_count", "data_complete", "payload", "recorded_at"} <= cols

    def test_index_supports_the_flagged_listing_query(self):
        db = self._db()
        indexes = {r[1] for r in db.execute("PRAGMA index_list(equibles_filing_forensics)")}
        assert any("flagged" in name for name in indexes)

    def test_upsert_is_idempotent_per_ticker(self):
        db = self._db()
        db.execute(
            EQUIBLES_FILING_FORENSICS_UPSERT_SQL,
            ("AAPL", "2026-08-10T00:00:00Z", 1, 1, '{"v":1}', "2026-08-10T00:00:00Z"),
        )
        db.execute(
            EQUIBLES_FILING_FORENSICS_UPSERT_SQL,
            ("AAPL", "2026-08-11T00:00:00Z", 2, 0, '{"v":2}', "2026-08-11T00:00:00Z"),
        )
        rows = db.execute(
            "SELECT ticker, flag_count, data_complete, payload FROM equibles_filing_forensics"
        ).fetchall()
        assert rows == [("AAPL", 2, 0, '{"v":2}')]

    def test_service_name_matches_the_health_row_the_watchdog_reads(self):
        assert SERVICE_NAME == "equibles-filing-forensics"

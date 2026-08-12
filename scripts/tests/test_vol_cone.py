"""VOL CONE cheap-wing scanner — parse, interpolate, regime, storage tests.

Ground truth is the checked-in UW captures (2026-08-12):
  fixtures/vol_cone_nvda_greeks_current.json — NVDA Sep 18 chain as-of 2026-08-12
  fixtures/vol_cone_nvda_greeks_hist.json    — same expiry as-of 2026-06-15
  fixtures/vol_cone_smh_greeks_current.json  — SMH Sep 18, strikes around spot
  fixtures/vol_cone_nvda_weekly_series.json  — 18 extracted Friday points
Expected numbers were derived by interpolating the fixtures, not by hand.
Spec: docs/indicators/vol-cone.md.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from fetch_vol_cone import (
    classify_regime,
    compute_name,
    interpolate_iv_at_moneyness,
    merge_universe,
    parse_greek_rows,
    percentile,
    rank_strictly_below,
    select_target_expiry,
    session_ivs,
    third_friday,
)

FIXTURES = Path(__file__).parent / "fixtures"
NVDA_CURRENT = json.loads((FIXTURES / "vol_cone_nvda_greeks_current.json").read_text())
NVDA_HIST = json.loads((FIXTURES / "vol_cone_nvda_greeks_hist.json").read_text())
SMH_CURRENT = json.loads((FIXTURES / "vol_cone_smh_greeks_current.json").read_text())
WEEKLY = json.loads((FIXTURES / "vol_cone_nvda_weekly_series.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0041_vol_cone.sql"

NVDA_SPOT = 223.95
NVDA_HIST_SPOT = 212.45
SMH_SPOT = 588.12

NVDA_ATM = 0.3851329156797111
NVDA_C10 = 0.3862120615005326
NVDA_P10 = 0.39731998999142565
NVDA_C10_K = 246.345
NVDA_P10_K = 201.555

HIST_ATM = 0.4169907697613465
HIST_C10 = 0.4344290213578079
HIST_P10 = 0.4073064139284684

SMH_ATM = 0.38707413534789115
SMH_C10 = 0.3812494942003351
SMH_P10 = 0.40943558183811746

WEEKLY_P10 = 0.38790363361714875
WEEKLY_P90 = 0.44298068625543574


# ── Chain parsing + moneyness interpolation ───────────────────────


class TestParseAndInterpolate:
    def test_nvda_current_fixture_parses(self):
        rows = parse_greek_rows(NVDA_CURRENT)
        assert len(rows) == 77
        assert rows[0]["expiry"] == "2026-09-18"
        assert rows[0]["date"] == "2026-08-12"

    def test_nvda_atm_and_wings_match_fixture_derivation(self):
        rows = parse_greek_rows(NVDA_CURRENT)
        ivs = session_ivs(rows, NVDA_SPOT)
        assert ivs is not None
        assert ivs["atm_iv"] == pytest.approx(NVDA_ATM, abs=1e-12)
        assert ivs["call_10_iv"] == pytest.approx(NVDA_C10, abs=1e-12)
        assert ivs["put_10_iv"] == pytest.approx(NVDA_P10, abs=1e-12)
        assert ivs["call_10_strike"] == pytest.approx(NVDA_C10_K, abs=1e-6)
        assert ivs["put_10_strike"] == pytest.approx(NVDA_P10_K, abs=1e-6)

    def test_hist_session_uses_that_day_spot(self):
        rows = parse_greek_rows(NVDA_HIST)
        assert rows[0]["date"] == "2026-06-15"
        ivs = session_ivs(rows, NVDA_HIST_SPOT)
        assert ivs is not None
        assert ivs["atm_iv"] == pytest.approx(HIST_ATM, abs=1e-12)
        assert ivs["call_10_iv"] == pytest.approx(HIST_C10, abs=1e-12)
        assert ivs["put_10_iv"] == pytest.approx(HIST_P10, abs=1e-12)

    def test_smh_wings_match_fixture_derivation(self):
        rows = parse_greek_rows(SMH_CURRENT)
        ivs = session_ivs(rows, SMH_SPOT)
        assert ivs is not None
        assert ivs["atm_iv"] == pytest.approx(SMH_ATM, abs=1e-12)
        assert ivs["call_10_iv"] == pytest.approx(SMH_C10, abs=1e-12)
        assert ivs["put_10_iv"] == pytest.approx(SMH_P10, abs=1e-12)

    def test_exact_moneyness_hit_short_circuits(self):
        rows = [
            {"strike": 100.0, "call_volatility": 0.20, "put_volatility": 0.22},
            {"strike": 110.0, "call_volatility": 0.25, "put_volatility": 0.24},
        ]
        iv, strike = interpolate_iv_at_moneyness(rows, 100.0, 1.10, "call")
        assert iv == pytest.approx(0.25)
        assert strike == pytest.approx(110.0)

    def test_linear_interpolation_between_brackets(self):
        rows = [
            {"strike": 100.0, "call_volatility": 0.10, "put_volatility": 0.10},
            {"strike": 120.0, "call_volatility": 0.30, "put_volatility": 0.30},
        ]
        iv, _ = interpolate_iv_at_moneyness(rows, 100.0, 1.10, "call")
        assert iv == pytest.approx(0.20)

    def test_unbracketed_target_returns_none(self):
        rows = [{"strike": 100.0, "call_volatility": 0.2, "put_volatility": 0.2}]
        iv, strike = interpolate_iv_at_moneyness(rows, 100.0, 1.10, "call")
        assert iv is None and strike is None

    def test_non_positive_iv_is_dropped(self):
        rows = [
            {"strike": 90.0, "call_volatility": 0.0, "put_volatility": 0.2},
            {"strike": 100.0, "call_volatility": 0.2, "put_volatility": 0.2},
            {"strike": 110.0, "call_volatility": 0.2, "put_volatility": 0.2},
        ]
        iv, _ = interpolate_iv_at_moneyness(rows, 100.0, 0.90, "call")
        assert iv is None


# ── Expiry selection ──────────────────────────────────────────────


class TestSelectTargetExpiry:
    def test_third_friday_september_2026(self):
        assert third_friday(2026, 9) == date(2026, 9, 18)

    def test_as_of_aug_12_picks_sep_monthly(self):
        assert select_target_expiry(date(2026, 8, 12)) == date(2026, 9, 18)

    def test_rejects_monthlies_inside_21_dte(self):
        # 2026-08-21 is 9 DTE from 2026-08-12; must not win.
        assert select_target_expiry(date(2026, 8, 12)) != date(2026, 8, 21)


# ── Cone stats + regime ───────────────────────────────────────────


class TestConeStats:
    def test_weekly_percentiles_match_fixture_derivation(self):
        atms = [row["atm"] for row in WEEKLY]
        assert percentile(atms, 0.10) == pytest.approx(WEEKLY_P10, abs=1e-12)
        assert percentile(atms, 0.90) == pytest.approx(WEEKLY_P90, abs=1e-12)

    def test_rank_is_strictly_below_share(self):
        atms = [row["atm"] for row in WEEKLY]
        c10s = [row["call10"] for row in WEEKLY]
        p10s = [row["put10"] for row in WEEKLY]
        assert rank_strictly_below(atms[-1], atms) == pytest.approx(0.0)
        assert rank_strictly_below(c10s[-1], c10s) == pytest.approx(1 / 18)
        assert rank_strictly_below(p10s[-1], p10s) == pytest.approx(2 / 18)

    def test_nvda_weekly_latest_is_cheap_wings(self):
        series = [
            {
                "date": row["date"],
                "spot": row["spot"],
                "atm_iv": row["atm"],
                "call_10_iv": row["call10"],
                "put_10_iv": row["put10"],
            }
            for row in WEEKLY
        ]
        name = compute_name("NVDA", "2026-09-18", series)
        assert name["regime"] == "CHEAP_WINGS"
        assert name["atm_percentile"] == pytest.approx(0.0)
        assert name["call_10_percentile"] == pytest.approx(1 / 18)
        assert name["put_10_percentile"] == pytest.approx(2 / 18)
        assert name["p10"] == pytest.approx(WEEKLY_P10, abs=1e-12)
        assert name["p90"] == pytest.approx(WEEKLY_P90, abs=1e-12)
        assert name["dte"] == (date(2026, 9, 18) - date(2026, 8, 12)).days


class TestClassifyRegime:
    def test_cheap_wings_inclusive_bounds(self):
        assert classify_regime(0.15, 0.20, 0.20) == "CHEAP_WINGS"
        assert classify_regime(0.0, 0.05, 0.11) == "CHEAP_WINGS"

    def test_cheap_atm_when_a_wing_is_not_cheap(self):
        assert classify_regime(0.10, 0.40, 0.10) == "CHEAP_ATM"
        assert classify_regime(0.15, 0.21, 0.10) == "CHEAP_ATM"

    def test_rich_inclusive(self):
        assert classify_regime(0.85, 0.50, 0.50) == "RICH"
        assert classify_regime(1.0, 0.9, 0.9) == "RICH"

    def test_neutral_middle(self):
        assert classify_regime(0.151, 0.10, 0.10) == "NEUTRAL"
        assert classify_regime(0.50, 0.50, 0.50) == "NEUTRAL"
        assert classify_regime(0.849, 0.50, 0.50) == "NEUTRAL"


class TestMergeUniverse:
    def test_unions_seed_and_watchlist_uppercase_capped(self):
        merged = merge_universe(["smh", "xyz"], seed=["NVDA", "SMH"], cap=3)
        assert merged[0] == "NVDA"
        assert "SMH" in merged
        assert "XYZ" in merged
        assert len(merged) == 3

    def test_empty_watchlist_keeps_seed(self):
        merged = merge_universe([], seed=["NVDA", "SMH"], cap=40)
        assert merged == ["NVDA", "SMH"]


# ── Incremental run / heartbeat ───────────────────────────────────


class _StubClient:
    def __init__(self, greeks, closes, spot):
        self.greeks = greeks
        self.closes = closes
        self.spot = spot
        self.calls = []

    def fetch_greeks(self, ticker, expiry, as_of=None):
        self.calls.append((ticker, expiry, as_of))
        return self.greeks.get((ticker, expiry, as_of), {"data": []})

    def fetch_closes(self, ticker):
        return self.closes.get(ticker, {})

    def fetch_spot(self, ticker):
        return self.spot[ticker]


class TestRunHeartbeat:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import fetch_vol_cone as mod

        monkeypatch.setattr(mod, "VOL_CONE_JSON", tmp_path / "vol_cone.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod,
            "_write_db_cache",
            lambda payload, scan_time, rows_changed: self.db_writes.append(rows_changed),
        )
        monkeypatch.setattr(mod, "_read_history_rows", lambda: [])
        self.mod = mod

    def _seed_complete_history(self):
        series = [
            {
                "date": row["date"],
                "spot": row["spot"],
                "atm_iv": row["atm"],
                "call_10_iv": row["call10"],
                "put_10_iv": row["put10"],
                "call_10_strike": row.get("call_k"),
                "put_10_strike": row.get("put_k"),
            }
            for row in WEEKLY
        ]
        name = compute_name("NVDA", "2026-09-18", series)
        payload = self.mod.build_output(
            [name], scan_time="old", source_as_of="2026-08-12"
        )
        self.mod.VOL_CONE_JSON.write_text(json.dumps(payload))
        self.mod._read_history_rows = lambda: [
            {
                "ticker": "NVDA",
                "date": row["date"],
                "expiry": "2026-09-18",
                "dte": 37,
                "spot": row["spot"],
                "atm_iv": row["atm"],
                "call_10_iv": row["call10"],
                "put_10_iv": row["put10"],
                "call_10_strike": row.get("call_k"),
                "put_10_strike": row.get("put_k"),
            }
            for row in WEEKLY
        ]
        return payload

    def test_no_missing_sessions_heartbeats_without_row_writes(self):
        self._seed_complete_history()
        client = _StubClient({}, {"NVDA": {}}, {"NVDA": NVDA_SPOT})
        payload = self.mod.run(
            client=client,
            now=datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc),
            tickers=["NVDA"],
        )
        assert client.calls == []
        assert payload["scan_time"] != "old"
        assert payload["hit_count"] >= 1
        assert self.db_writes == [False]

    def test_missing_session_fetches_and_upserts(self):
        prior = [row for row in WEEKLY if row["date"] != "2026-08-12"]
        self.mod._read_history_rows = lambda: [
            {
                "ticker": "NVDA",
                "date": row["date"],
                "expiry": "2026-09-18",
                "dte": 37,
                "spot": row["spot"],
                "atm_iv": row["atm"],
                "call_10_iv": row["call10"],
                "put_10_iv": row["put10"],
                "call_10_strike": row.get("call_k"),
                "put_10_strike": row.get("put_k"),
            }
            for row in prior
        ]
        closes = {row["date"]: row["spot"] for row in WEEKLY}
        client = _StubClient(
            {("NVDA", "2026-09-18", "2026-08-12"): NVDA_CURRENT},
            {"NVDA": closes},
            {"NVDA": NVDA_SPOT},
        )
        payload = self.mod.run(
            client=client,
            now=datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc),
            tickers=["NVDA"],
        )
        assert ("NVDA", "2026-09-18", "2026-08-12") in client.calls
        assert payload["current"]["ticker"] == "NVDA"
        assert payload["current"]["atm_iv"] == pytest.approx(NVDA_ATM, abs=1e-9)
        assert self.db_writes == [True]


# ── Migration + upsert ────────────────────────────────────────────


class TestVolConeStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_and_registers_version(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 41
        cols = {r[1] for r in db.execute("PRAGMA table_info(vol_cone_history)")}
        assert {
            "ticker",
            "date",
            "expiry",
            "dte",
            "spot",
            "atm_iv",
            "call_10_iv",
            "put_10_iv",
            "call_10_strike",
            "put_10_strike",
            "recorded_at",
        } <= cols

    def test_upsert_is_idempotent_per_ticker_date_expiry(self):
        from db import writer

        db = self._db()
        args1 = (
            "NVDA",
            "2026-08-12",
            "2026-09-18",
            37,
            223.95,
            0.385,
            0.386,
            0.397,
            246.345,
            201.555,
            "2026-08-12T20:45:00Z",
        )
        args2 = (
            "NVDA",
            "2026-08-12",
            "2026-09-18",
            37,
            223.95,
            0.384,
            0.386,
            0.397,
            246.345,
            201.555,
            "2026-08-12T21:00:00Z",
        )
        db.execute(writer.VOL_CONE_UPSERT_SQL, args1)
        db.execute(writer.VOL_CONE_UPSERT_SQL, args2)
        rows = db.execute(
            "SELECT ticker, date, expiry, atm_iv FROM vol_cone_history"
        ).fetchall()
        assert rows == [("NVDA", "2026-08-12", "2026-09-18", 0.384)]

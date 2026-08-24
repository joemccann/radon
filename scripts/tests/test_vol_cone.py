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
    select_target_expiries,
    session_ivs,
    third_friday,
)

FIXTURES = Path(__file__).parent / "fixtures"
NVDA_CURRENT = json.loads((FIXTURES / "vol_cone_nvda_greeks_current.json").read_text())
NVDA_HIST = json.loads((FIXTURES / "vol_cone_nvda_greeks_hist.json").read_text())
SMH_CURRENT = json.loads((FIXTURES / "vol_cone_smh_greeks_current.json").read_text())
WEEKLY = json.loads((FIXTURES / "vol_cone_nvda_weekly_series.json").read_text())
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0047_vol_cone.sql"

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


class TestSelectTargetExpiries:
    def test_third_friday_september_2026(self):
        assert third_friday(2026, 9) == date(2026, 9, 18)

    def test_as_of_aug_12_returns_every_standard_monthly_in_window(self):
        assert select_target_expiries(date(2026, 8, 12)) == [
            date(2026, 9, 18),
            date(2026, 10, 16),
            date(2026, 11, 20),
            date(2026, 12, 18),
            date(2027, 1, 15),
        ]

    def test_rejects_weeklies_and_front_monthlies_inside_21_dte(self):
        expiries = select_target_expiries(date(2026, 8, 12))
        assert date(2026, 8, 21) not in expiries
        assert date(2026, 8, 14) not in expiries


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
        assert name["month"] == "SEP"
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

    def _history_rows(self, expiries, weekly=WEEKLY):
        rows = []
        for expiry in expiries:
            expiry_s = expiry.isoformat() if isinstance(expiry, date) else expiry
            for row in weekly:
                rows.append(
                    {
                        "ticker": "NVDA",
                        "date": row["date"],
                        "expiry": expiry_s,
                        "dte": 37,
                        "spot": row["spot"],
                        "atm_iv": row["atm"],
                        "call_10_iv": row["call10"],
                        "put_10_iv": row["put10"],
                        "call_10_strike": row.get("call_k"),
                        "put_10_strike": row.get("put_k"),
                    }
                )
        return rows

    def _seed_complete_history(self):
        expiries = select_target_expiries(date(2026, 8, 12))
        history = self._history_rows(expiries)
        names = []
        for expiry in expiries:
            expiry_s = expiry.isoformat()
            series = [
                {
                    "date": row["date"],
                    "spot": row["spot"],
                    "atm_iv": row["atm_iv"],
                    "call_10_iv": row["call_10_iv"],
                    "put_10_iv": row["put_10_iv"],
                }
                for row in history
                if row["expiry"] == expiry_s
            ]
            names.append(compute_name("NVDA", expiry_s, series))
        payload = self.mod.build_output(
            names, scan_time="old", source_as_of="2026-08-12"
        )
        self.mod.VOL_CONE_JSON.write_text(json.dumps(payload))
        self.mod._read_history_rows = lambda: history
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
        expiries = select_target_expiries(date(2026, 8, 12))
        complete = self._history_rows(
            [e for e in expiries if e != date(2026, 9, 18)]
        )
        sep_prior = self._history_rows(
            [date(2026, 9, 18)],
            weekly=[row for row in WEEKLY if row["date"] != "2026-08-12"],
        )
        self.mod._read_history_rows = lambda: complete + sep_prior
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
        assert {call[1] for call in client.calls} == {"2026-09-18"}
        sep = next(name for name in payload["names"] if name["expiry"] == "2026-09-18")
        assert sep["atm_iv"] == pytest.approx(NVDA_ATM, abs=1e-9)
        assert sep["month"] == "SEP"
        assert {name["expiry"] for name in payload["names"]} >= {
            e.isoformat() for e in expiries
        }
        assert self.db_writes == [True]


class _InterruptingClient(_StubClient):
    """Stub that dies mid-scan the way a deploy SIGTERM kills the oneshot."""

    def __init__(self, greeks, closes, spot, *, die_after):
        super().__init__(greeks, closes, spot)
        self.die_after = die_after

    def fetch_greeks(self, ticker, expiry, as_of=None):
        if len(self.calls) >= self.die_after:
            raise KeyboardInterrupt("SIGTERM during stop-clean")
        return super().fetch_greeks(ticker, expiry, as_of)


class TestRunSurvivesInterruption:
    """Incident 2026-08-12: radon-vol-cone.service was SIGTERM'd mid-ExecStart
    by the deploy's stop-clean. Everything fetched was held in memory until a
    single terminal write, so each kill discarded 100% of the work and the
    cold-start backfill (~15,600 UW calls, ~5h) could never converge.
    """

    SESSIONS = [row["date"] for row in WEEKLY[-3:]]
    EXPIRY = date(2026, 9, 18)
    NOW = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import fetch_vol_cone as mod
        from db import writer

        monkeypatch.setattr(mod, "VOL_CONE_JSON", tmp_path / "vol_cone.json")
        monkeypatch.setattr(mod, "_read_history_rows", lambda: [])
        monkeypatch.setattr(mod, "select_target_expiries", lambda as_of: [self.EXPIRY])

        self.checkpointed: list[dict] = []
        self.health: list[tuple[str, str]] = []
        monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
        monkeypatch.setattr(
            writer,
            "upsert_vol_cone_rows",
            lambda rows, recorded_at=None: self.checkpointed.extend(rows),
        )
        monkeypatch.setattr(writer, "upsert_scan_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(
            writer,
            "record_service_health",
            lambda service, state, **kwargs: self.health.append((service, state)),
        )
        self.mod = mod

    def _client(self, cls=_StubClient, **kwargs):
        closes = {row["date"]: row["spot"] for row in WEEKLY if row["date"] in self.SESSIONS}
        greeks = {
            (ticker, self.EXPIRY.isoformat(), session): NVDA_CURRENT
            for ticker in ("NVDA", "SMH")
            for session in self.SESSIONS
        }
        return cls(greeks, {"NVDA": closes, "SMH": closes},
                   {"NVDA": NVDA_SPOT, "SMH": SMH_SPOT}, **kwargs)

    def test_completed_ticker_is_persisted_before_the_interrupt(self):
        client = self._client(_InterruptingClient, die_after=len(self.SESSIONS))
        with pytest.raises(KeyboardInterrupt):
            self.mod.run(client=client, now=self.NOW, tickers=["NVDA", "SMH"])
        nvda = [row for row in self.checkpointed if row["ticker"] == "NVDA"]
        assert {row["date"] for row in nvda} == set(self.SESSIONS)
        assert all(row["expiry"] == self.EXPIRY.isoformat() for row in nvda)

    def test_wall_clock_budget_stops_the_scan_and_still_heartbeats(self, monkeypatch):
        monkeypatch.setenv("VOL_CONE_BUDGET_S", "100")
        clock = iter([0.0, 60.0, 120.0, 180.0, 240.0])
        monkeypatch.setattr(self.mod, "_monotonic", lambda: next(clock))
        client = self._client()
        payload = self.mod.run(client=client, now=self.NOW, tickers=["NVDA", "SMH"])
        assert {call[0] for call in client.calls} == {"NVDA"}
        assert payload["count"] == 1
        assert ("vol-cone", "ok") in self.health

    def test_budget_defaults_inside_the_unit_start_timeout(self, monkeypatch):
        monkeypatch.delenv("VOL_CONE_BUDGET_S", raising=False)
        assert 0 < self.mod._budget_seconds() < 3600


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
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 47
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


# ── Universe breadth ──────────────────────────────────────────────


class TestDefaultUniverse:
    """The cone is a relative-value screen: 25 names is too few to rank.

    ``data/presets/`` is runtime-owned and gitignored (the rebalance handler
    writes it), so these pin the composition rather than the checked-out
    file — including the degradation when the preset is not on the box.
    """

    def test_default_universe_extends_the_seed_with_the_preset(self, monkeypatch):
        import fetch_vol_cone as mod

        monkeypatch.setattr(
            mod, "_preset_tickers", lambda name=mod._UNIVERSE_PRESET: ["NVDA", "PLTR", "ADBE"]
        )
        universe = mod.default_universe()

        assert universe[: len(mod.SEED_UNIVERSE)] == mod.SEED_UNIVERSE
        assert universe[len(mod.SEED_UNIVERSE):] == ["PLTR", "ADBE"]
        assert len(universe) == len(dict.fromkeys(universe))

    def test_missing_preset_degrades_to_the_seed_list(self, monkeypatch):
        import fetch_vol_cone as mod

        monkeypatch.setattr(mod, "_preset_tickers", lambda name=mod._UNIVERSE_PRESET: [])
        assert mod.default_universe() == mod.SEED_UNIVERSE

    def test_universe_cap_admits_far_more_than_the_seed(self, monkeypatch):
        import fetch_vol_cone as mod

        assert mod._UNIVERSE_CAP > len(mod.SEED_UNIVERSE)
        monkeypatch.setattr(
            mod, "_preset_tickers", lambda name=mod._UNIVERSE_PRESET: [f"T{i}" for i in range(200)]
        )
        assert len(merge_universe([], cap=mod._UNIVERSE_CAP)) == mod._UNIVERSE_CAP


# ── Intraday live sample ──────────────────────────────────────────


class TestIntradayTargets:
    """A live pass costs one greeks call per pair, every cycle, all session.

    Refreshing the whole universe at that cadence would cost more than the
    daily UW cap, and a name deep in the rich tail cannot become tradeable
    before the close anyway.
    """

    def _name(self, ticker, wing, atm):
        return {"ticker": ticker, "expiry": "2026-09-18", "wing_score": wing, "atm_percentile": atm}

    def test_cheapest_names_come_first_and_the_cap_holds(self):
        from fetch_vol_cone import intraday_targets

        names = [self._name(f"T{i}", i / 100.0, i / 100.0) for i in range(60)]
        targets = intraday_targets(names, watchlist=[], cap=10)
        assert len(targets) == 10
        assert [ticker for ticker, _ in targets] == [f"T{i}" for i in range(10)]

    def test_rich_names_are_not_refreshed(self):
        from fetch_vol_cone import intraday_targets

        names = [self._name("RICH", 0.95, 0.95), self._name("CHEAP", 0.05, 0.10)]
        assert intraday_targets(names, watchlist=[], cap=10) == [("CHEAP", "2026-09-18")]

    def test_watchlist_names_are_refreshed_however_rich(self):
        from fetch_vol_cone import intraday_targets

        names = [self._name("RICH", 0.95, 0.95)]
        assert intraday_targets(names, watchlist=["rich"], cap=10) == [("RICH", "2026-09-18")]


class TestRunIntraday:
    """Today's cone does not exist until the close, so the session's own IV
    is ranked against the stored completed-session history and marked live.
    """

    OPEN = datetime(2026, 8, 13, 17, 0, tzinfo=timezone.utc)      # 13:00 ET Thu
    CLOSED = datetime(2026, 8, 13, 23, 0, tzinfo=timezone.utc)    # 19:00 ET Thu
    SESSION = "2026-08-13"

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        import fetch_vol_cone as mod

        monkeypatch.setattr(mod, "VOL_CONE_JSON", tmp_path / "vol_cone.json")
        self.db_writes = []
        monkeypatch.setattr(
            mod,
            "_write_intraday_db_cache",
            lambda payload, scan_time: self.db_writes.append((False, "vol-cone-intraday")),
        )
        monkeypatch.setattr(mod, "_load_watchlist", lambda: [])
        self.mod = mod

    def _seed_history(self, monkeypatch, expiry="2026-09-18"):
        rows = [
            {
                "ticker": "NVDA",
                "date": row["date"],
                "expiry": expiry,
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
        monkeypatch.setattr(self.mod, "_read_history_rows", lambda: rows)
        return rows

    def test_live_point_is_ranked_and_flagged(self, monkeypatch):
        self._seed_history(monkeypatch)
        client = _StubClient(
            {("NVDA", "2026-09-18", self.SESSION): NVDA_CURRENT},
            {},
            {"NVDA": NVDA_SPOT},
        )
        payload = self.mod.run_intraday(client=client, now=self.OPEN)

        assert payload["source_as_of"] == self.SESSION
        name = payload["names"][0]
        assert name["is_intraday"] is True
        assert name["series"][-1]["date"] == self.SESSION
        assert name["series"][-1]["is_intraday"] is True
        assert client.calls == [("NVDA", "2026-09-18", self.SESSION)]

    def test_live_point_never_becomes_a_stored_session(self, monkeypatch):
        self._seed_history(monkeypatch)
        client = _StubClient(
            {("NVDA", "2026-09-18", self.SESSION): NVDA_CURRENT},
            {},
            {"NVDA": NVDA_SPOT},
        )
        payload = self.mod.run_intraday(client=client, now=self.OPEN)

        assert self.db_writes == [(False, "vol-cone-intraday")]
        stored = self.mod._flatten_history_rows(payload)
        assert stored, "completed sessions still flatten"
        assert all(row["date"] != self.SESSION for row in stored)

    def test_closed_market_spends_no_uw_requests(self, monkeypatch):
        self._seed_history(monkeypatch)
        client = _StubClient({}, {}, {"NVDA": NVDA_SPOT})
        payload = self.mod.run_intraday(client=client, now=self.CLOSED)

        assert client.calls == []
        assert payload["source_as_of"] == WEEKLY[-1]["date"]
        assert self.db_writes == [(False, "vol-cone-intraday")]

    def test_holds_when_the_uw_daily_budget_is_nearly_spent(self, monkeypatch):
        self._seed_history(monkeypatch)
        monkeypatch.setattr(self.mod, "_uw_remaining", lambda: 10)
        client = _StubClient(
            {("NVDA", "2026-09-18", self.SESSION): NVDA_CURRENT},
            {},
            {"NVDA": NVDA_SPOT},
        )
        payload = self.mod.run_intraday(client=client, now=self.OPEN)

        assert client.calls == []
        assert payload["source_as_of"] == WEEKLY[-1]["date"]

    def test_expiries_outside_the_window_are_dropped(self, monkeypatch):
        """History keeps monthlies that have since rolled inside 21 DTE. The
        live pass targets the same window the EOD run does, and raises rather
        than publishing an empty cone when nothing in the window is stored
        yet - a failed unit says the backfill is behind, silence would not.
        """
        assert self._seed_history(monkeypatch, expiry="2026-08-21")
        client = _StubClient({}, {}, {"NVDA": NVDA_SPOT})

        with pytest.raises(ValueError, match="no stored cone"):
            self.mod.run_intraday(client=client, now=self.OPEN)
        assert client.calls == []

    def _seed_rich_beside_nvda(self, monkeypatch):
        """NVDA rides the cheap tail; RICH's latest close tops its own cone,
        so ``intraday_targets`` leaves RICH on last night's numbers."""
        nvda = self._seed_history(monkeypatch)
        by_date = sorted(nvda, key=lambda row: row["date"])
        rich = [
            {
                **row,
                "ticker": "RICH",
                "atm_iv": 0.30 + i * 0.01,
                "call_10_iv": 0.30 + i * 0.01,
                "put_10_iv": 0.30 + i * 0.01,
            }
            for i, row in enumerate(by_date)
        ]
        monkeypatch.setattr(self.mod, "_read_history_rows", lambda: nvda + rich)

    def test_only_names_that_earn_a_refresh_are_marked_live(self, monkeypatch):
        """T-106: a partial refresh must say how partial. ``any()`` alone lets
        the tab stamp every row LIVE while the rich tail shows yesterday's IV."""
        self._seed_rich_beside_nvda(monkeypatch)
        client = _StubClient(
            {("NVDA", "2026-09-18", self.SESSION): NVDA_CURRENT},
            {},
            {"NVDA": NVDA_SPOT, "RICH": 100.0},
        )
        payload = self.mod.run_intraday(client=client, now=self.OPEN)

        by_ticker = {name["ticker"]: name for name in payload["names"]}
        assert by_ticker["NVDA"]["is_intraday"] is True
        assert by_ticker["RICH"].get("is_intraday") is not True
        assert by_ticker["RICH"]["series"][-1]["date"] == WEEKLY[-1]["date"]
        assert client.calls == [("NVDA", "2026-09-18", self.SESSION)]
        assert payload["is_intraday"] is True
        assert payload["count"] == 2
        assert payload["intraday_count"] == 1

    def test_closed_market_reports_nothing_live(self, monkeypatch):
        """Kills the ``payload["is_intraday"] = True`` mutation."""
        self._seed_rich_beside_nvda(monkeypatch)
        client = _StubClient({}, {}, {"NVDA": NVDA_SPOT, "RICH": 100.0})
        payload = self.mod.run_intraday(client=client, now=self.CLOSED)

        assert client.calls == []
        assert payload["is_intraday"] is False
        assert payload["intraday_count"] == 0
        assert all(not name.get("is_intraday") for name in payload["names"])

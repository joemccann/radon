"""Panic Proxy — four-leg Cboe composite tests.

Ground-truth values are read from checked-in fixtures (last ~800 Cboe
sessions from 2023-07, written with Python, real headers). Expected
numbers below were derived by inspecting those fixtures, never by mental
arithmetic. The 10y rank is tested with a synthetic series.

Spec: docs/indicators/panic-index.md.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fetch_vixts import parse_index_csv
from lib.panic_index_math import (
    ALERT_SIGMA,
    ALERT_TOP_N,
    MAX_DROPPED_SHARE,
    MIN_SERIES_ROWS,
    RANK_MIN_ROWS,
    RANK_WINDOW,
    SKEW_SANITY,
    TS_RATIO_SANITY,
    VIX_SANITY,
    VVIX_SANITY,
    Z_STD_FLOOR,
    Z_WINDOW,
    attach_delta,
    attach_level,
    attach_z_scores,
    build_current,
    compute_stats,
    ensure_plausible_series,
    format_alert_message,
    join_series,
    rolling_z,
    should_fire_decline,
    trailing_delta_stats,
)

FIXTURES = Path(__file__).parent / "fixtures"
VIX_CSV = (FIXTURES / "panic_index_vix_sample.csv").read_text()
VIX3M_CSV = (FIXTURES / "panic_index_vix3m_sample.csv").read_text()
VVIX_CSV = (FIXTURES / "panic_index_vvix_sample.csv").read_text()
SKEW_CSV = (FIXTURES / "panic_index_skew_sample.csv").read_text()
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0077_panic_index.sql"

_NOW = datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc)


def _fixture_legs():
    return (
        parse_index_csv(VIX_CSV, "CLOSE"),
        parse_index_csv(VIX3M_CSV, "CLOSE"),
        parse_index_csv(VVIX_CSV, "VVIX"),
        parse_index_csv(SKEW_CSV, "SKEW"),
    )


def _fixture_joined():
    series, dropped, base = join_series(*_fixture_legs())
    return series, dropped, base


def _fixture_computed():
    series, dropped, base = _fixture_joined()
    attach_z_scores(series)
    attach_level(series)
    attach_delta(series)
    return series, dropped, base


def _rows(pairs):
    return [{"date": d, "value": v} for d, v in pairs]


# ── Cboe CSV parsing ──────────────────────────────────────────────


class TestParseIndexCsv:
    def test_vvix_uses_its_own_value_column(self):
        rows = parse_index_csv(VVIX_CSV, "VVIX")
        assert rows[0] == {"date": "2023-07-03", "value": 84.17}
        assert rows[-1] == {"date": "2026-09-17", "value": 87.72}
        assert parse_index_csv(VVIX_CSV, "CLOSE") == []

    def test_skew_uses_its_own_value_column(self):
        rows = parse_index_csv(SKEW_CSV, "SKEW")
        assert rows[0] == {"date": "2023-07-03", "value": 145.24}
        assert rows[-1] == {"date": "2026-09-17", "value": 145.7}
        assert parse_index_csv(SKEW_CSV, "CLOSE") == []

    def test_malformed_rows_are_skipped(self):
        text = "DATE,VVIX\n09/17/2026,87.72\nnot-a-date,1\n09/18/2026,\n"
        assert parse_index_csv(text, "VVIX") == [{"date": "2026-09-17", "value": 87.72}]


# ── Join ──────────────────────────────────────────────────────────


class TestJoinSeries:
    def test_four_way_inner_join_and_base_count(self):
        series, dropped, base = _fixture_joined()
        assert len(series) == 805
        assert base == 806
        assert dropped == ["2024-11-29"]
        assert series[0]["date"] == "2023-07-03"
        assert series[-1]["date"] == "2026-09-17"

    def test_removed_vvix_date_is_dropped_not_emitted(self):
        vix, vix3m, vvix, skew = _fixture_legs()
        vvix = [row for row in vvix if row["date"] != "2026-09-16"]
        series, dropped, base = join_series(vix, vix3m, vvix, skew)
        assert "2026-09-16" in dropped
        assert all(row["date"] != "2026-09-16" for row in series)
        assert base == 806

    def test_non_positive_vix3m_is_dropped(self):
        series, dropped, base = join_series(
            _rows([("2026-09-16", 17.71), ("2026-09-17", 15.44)]),
            _rows([("2026-09-16", 0.0), ("2026-09-17", 18.55)]),
            _rows([("2026-09-16", 95.41), ("2026-09-17", 87.72)]),
            _rows([("2026-09-16", 145.95), ("2026-09-17", 145.70)]),
        )
        assert dropped == ["2026-09-16"]
        assert [row["date"] for row in series] == ["2026-09-17"]
        assert series[0]["ts"] == round(15.44 / 18.55, 4)
        assert base == 2


# ── rolling z / level / delta ─────────────────────────────────────


class TestRollingZ:
    def test_none_for_the_first_251_rows(self):
        series, _, _ = _fixture_computed()
        assert all(row["z_vix"] is None for row in series[:251])
        assert series[251]["z_vix"] is not None

    def test_row_252_matches_hand_computed_statistics(self):
        series, _, _ = _fixture_joined()
        window = [row["vix"] for row in series[:252]]
        expected = (window[-1] - statistics.fmean(window)) / statistics.stdev(window)
        zs = rolling_z(window)
        assert zs[251] == pytest.approx(expected, abs=1e-9)
        assert zs[251] == pytest.approx(-1.2049510882221126, abs=1e-9)

    def test_constant_window_emits_none_never_inf(self):
        zs = rolling_z([10.0] * Z_WINDOW)
        assert zs[-1] is None
        assert all(z is None or z != float("inf") for z in zs)


class TestLevelAndDelta:
    def test_level_none_when_any_leg_z_missing(self):
        series, _, _ = _fixture_computed()
        assert series[0]["level"] is None
        assert series[250]["level"] is None

    def test_level_equals_mean_when_all_four_exist(self):
        series, _, _ = _fixture_computed()
        row = series[251]
        assert row["level"] == pytest.approx(
            statistics.fmean([row["z_vix"], row["z_vvix"], row["z_ts"], row["z_skew"]]),
            abs=1e-4,
        )

    def test_delta_none_on_first_level_row_then_difference(self):
        series, _, _ = _fixture_computed()
        first_level = next(i for i, row in enumerate(series) if row["level"] is not None)
        assert series[first_level]["delta_1d"] is None
        nxt = series[first_level + 1]
        assert nxt["delta_1d"] == pytest.approx(
            nxt["level"] - series[first_level]["level"], abs=1e-4
        )


# ── trailing stats / compute_stats ────────────────────────────────


class TestTrailingDeltaStats:
    def _series(self, deltas):
        rows = []
        level = 0.0
        for i, delta in enumerate(deltas):
            level = 0.0 if i == 0 else level + delta
            rows.append(
                {
                    "date": f"2010-01-{(i % 28) + 1:02d}",
                    "delta_1d": None if i == 0 else delta,
                    "level": None if i == 0 else level,
                    "z_vix": 0.0, "z_vvix": 0.0, "z_ts": 0.0, "z_skew": 0.0,
                    "vix": 15.0, "vix3m": 18.0, "vvix": 90.0, "ts": 0.83, "skew": 140.0,
                }
            )
        return rows

    def test_window_capped_at_2520_and_planted_minimum_is_rank_1(self):
        deltas = [0.01] * 3000
        deltas[-1] = -4.0
        stats = trailing_delta_stats(self._series(deltas))
        assert stats["rank_n"] == RANK_WINDOW
        assert stats["rank_decline_10y"] == 1
        assert stats["delta_z"] is not None

    def test_ties_share_the_better_rank(self):
        deltas = [0.0] * (RANK_MIN_ROWS + 2)
        deltas[-1] = -1.0
        deltas[-3] = -1.0
        stats = trailing_delta_stats(self._series(deltas))
        assert stats["rank_decline_10y"] == 1

    def test_none_below_rank_min_rows(self):
        stats = trailing_delta_stats(self._series([0.01] * (RANK_MIN_ROWS - 1)))
        assert stats["rank_n"] is None
        assert stats["delta_z"] is None


class TestComputeStats:
    def test_low_and_high_dates_match_fixture_extremes(self):
        series, _, _ = _fixture_computed()
        stats = compute_stats(series)
        assert stats["low_date"] == "2024-08-06"
        assert stats["high_date"] == "2024-08-05"
        assert stats["low"] == pytest.approx(-2.3411, abs=1e-4)
        assert stats["high"] == pytest.approx(2.8165, abs=1e-3)


# ── plausibility ──────────────────────────────────────────────────


def _plausible_row(date="2026-09-17", **overrides):
    row = {
        "date": date,
        "vix": 15.44,
        "vix3m": 18.55,
        "vvix": 87.72,
        "ts": 0.8323,
        "skew": 145.70,
        "z_vix": -0.8,
        "z_vvix": -1.0,
        "z_ts": -0.7,
        "z_skew": 0.1,
        "level": -0.6,
        "delta_1d": -0.1,
    }
    row.update(overrides)
    return row


def _plausible_series(n=MIN_SERIES_ROWS, **latest):
    series = [_plausible_row(date=f"2010-01-{(i % 28) + 1:02d}") for i in range(n - 1)]
    series.append(_plausible_row(**latest))
    return series


class TestEnsurePlausibleSeries:
    def test_too_few_rows_raises(self):
        with pytest.raises(ValueError, match="rows"):
            ensure_plausible_series(_plausible_series(MIN_SERIES_ROWS - 1), [], MIN_SERIES_ROWS)

    def test_dropped_share_above_1pct_raises(self):
        dropped = [f"2010-01-{(i % 28) + 1:02d}" for i in range(20)]
        with pytest.raises(ValueError, match="dropped"):
            ensure_plausible_series(_plausible_series(), dropped, 100)

    def test_each_out_of_band_latest_leg_raises(self):
        cases = (
            {"vix": VIX_SANITY[1] + 1},
            {"vvix": VVIX_SANITY[0] - 1},
            {"ts": TS_RATIO_SANITY[1] + 0.1},
            {"skew": SKEW_SANITY[0] - 1},
        )
        for override in cases:
            with pytest.raises(ValueError, match="sane band"):
                ensure_plausible_series(_plausible_series(**override), [], MIN_SERIES_ROWS)

    def test_latest_level_none_raises(self):
        with pytest.raises(ValueError, match="no composite level"):
            ensure_plausible_series(_plausible_series(level=None), [], MIN_SERIES_ROWS)

    def test_non_positive_vix3m_raises(self):
        series = _plausible_series()
        series[10]["vix3m"] = 0.0
        with pytest.raises(ValueError, match="non-positive"):
            ensure_plausible_series(series, [], MIN_SERIES_ROWS)

    def test_healthy_series_passes(self):
        ensure_plausible_series(_plausible_series(), ["2010-11-11"], 4268)


# ── fetcher: 304 / freshness / write isolation / alert ────────────


class _Recorder:
    def __init__(self, rows_raise=False, snapshot_raise=False):
        self.rows_raise = rows_raise
        self.snapshot_raise = snapshot_raise
        self.row_upserts = []
        self.snapshots = []
        self.health = []

    def ensure_no_replica_for_writers(self):
        return None

    def upsert_panic_index_rows(self, rows, recorded_at=None):
        if self.rows_raise:
            raise RuntimeError("hrana 502")
        self.row_upserts.append((len(rows), recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        if self.snapshot_raise:
            raise RuntimeError("snapshot 502")
        self.snapshots.append((service, scan_time))

    def record_service_health(self, service, state, *, finished_at=None, error=None):
        self.health.append((service, state, error))


class _StubClient:
    def __init__(self, texts=None, stamps=None):
        self.calls = []
        self.texts = texts or {s: None for s in ("VIX", "VIX3M", "VVIX", "SKEW")}
        self.stamps = stamps or {s.lower(): "stamp" for s in ("VIX", "VIX3M", "VVIX", "SKEW")}

    def fetch_history(self, symbol, if_modified_since=None):
        self.calls.append((symbol, if_modified_since))
        return self.texts[symbol], self.stamps[symbol.lower()]


def _cached_payload(*, data_date, alert=None):
    return {
        "data_date": data_date,
        "count": 4200,
        "delta_count": 4000,
        "series": [],
        "current": {"date": data_date, "level": 0.0, "delta_1d": 0.0},
        "stats": {},
        "alert": alert or {"last_fired_date": None, "last_fired_kind": None},
        "source_last_modified": {"vix": "a", "vix3m": "b", "vvix": "c", "skew": "d"},
        "scan_time": "2026-09-01T00:00:00Z",
        "dropped_dates": [],
    }


class TestConditionalGet:
    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _Recorder()
        import fetch_panic_index as mod

        monkeypatch.setattr(mod, "writer", rec)
        monkeypatch.setattr(mod, "_write_json_cache", lambda _p: None)
        monkeypatch.setattr(mod, "load_skew25d_rows", lambda: [])
        return rec

    def test_all_304_restates_without_row_upsert_or_alert(self, recorder, monkeypatch):
        import fetch_panic_index as mod
        from utils.market_calendar import last_completed_session_date

        now = _NOW
        session = last_completed_session_date(now)
        monkeypatch.setattr(mod, "_read_json_cache", lambda: _cached_payload(data_date=session))
        dispatched = []
        monkeypatch.setattr(mod, "_dispatch_alert", lambda msg: dispatched.append(msg))
        payload = mod.run(client=_StubClient(), now=now)
        assert recorder.row_upserts == []
        assert recorder.snapshots
        assert "ok" in [h[1] for h in recorder.health]
        assert payload["scan_time"] != "2026-09-01T00:00:00Z"
        assert dispatched == []

    def test_one_changed_file_rebuilds_and_upserts(self, recorder, monkeypatch):
        import fetch_panic_index as mod

        vix, vix3m, vvix, skew = _fixture_legs()
        # Rebuild needs the full four texts; stub returns CSV for the changed
        # file and None for the others so _refetch_unchanged pulls the rest.
        csvs = {
            "VIX": VIX_CSV,
            "VIX3M": VIX3M_CSV,
            "VVIX": VVIX_CSV,
            "SKEW": SKEW_CSV,
        }
        client = _StubClient(texts={"VIX": VIX_CSV, "VIX3M": None, "VVIX": None, "SKEW": None})
        # After the first pass, refetch must succeed for the None symbols.
        original = client.fetch_history

        def fetch(symbol, if_modified_since=None):
            client.calls.append((symbol, if_modified_since))
            if symbol == "VIX" and if_modified_since is not None:
                return csvs[symbol], "new"
            return csvs[symbol], "stamp"

        client.fetch_history = fetch
        monkeypatch.setattr(mod, "_read_json_cache", lambda: _cached_payload(data_date="2026-09-17"))
        monkeypatch.setattr(
            mod, "ensure_plausible_series", lambda *a, **k: None
        )
        payload = mod.run(client=client, now=_NOW, no_alert=True)
        assert recorder.row_upserts, "a changed file must upsert the rebuilt series"
        assert payload["count"] == 805


class TestFreshnessVerdict:
    @pytest.fixture
    def recorder(self, monkeypatch):
        rec = _Recorder()
        import fetch_panic_index as mod

        monkeypatch.setattr(mod, "writer", rec)
        monkeypatch.setattr(mod, "_write_json_cache", lambda _p: None)
        return rec

    def test_304_stale_source_heartbeats_error(self, recorder, monkeypatch):
        import fetch_panic_index as mod

        monkeypatch.setattr(
            mod, "_read_json_cache", lambda: _cached_payload(data_date="2026-08-01")
        )
        payload = mod.run(client=_StubClient(), now=_NOW)
        assert payload["status"] == "stale_source"
        assert payload["lag_days"] > 4
        assert any(h[1] == "error" for h in recorder.health)

    def test_rebuild_stale_source_heartbeats_error(self, recorder, monkeypatch):
        import fetch_panic_index as mod

        monkeypatch.setattr(mod, "_read_json_cache", lambda: None)
        monkeypatch.setattr(mod, "load_skew25d_rows", lambda: [])
        monkeypatch.setattr(mod, "ensure_plausible_series", lambda *a, **k: None)
        client = _StubClient(texts={s: t for s, t in zip(
            ("VIX", "VIX3M", "VVIX", "SKEW"),
            (VIX_CSV, VIX3M_CSV, VVIX_CSV, SKEW_CSV),
        )})
        now = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)
        payload = mod.run(client=client, now=now, no_alert=True)
        assert payload["status"] == "stale_source"
        assert any(h[1] == "error" for h in recorder.health)


class TestWriteDbIsolation:
    def test_failed_row_upsert_still_snapshots_and_error_heartbeat(self, monkeypatch):
        import fetch_panic_index as mod

        fake = _Recorder(rows_raise=True)
        monkeypatch.setattr(mod, "writer", fake)
        mod._write_db({"series": [{"date": "d", "vix": 1}]}, "2026-09-17T02:50:00Z", rows_changed=True)
        assert fake.snapshots == [("panic-index", "2026-09-17T02:50:00Z")]
        assert fake.health[0][1] == "error"
        assert fake.health[0][2]["class"] == "db_write_failed"

    def test_failed_snapshot_still_heartbeats(self, monkeypatch):
        import fetch_panic_index as mod

        fake = _Recorder(snapshot_raise=True)
        monkeypatch.setattr(mod, "writer", fake)
        mod._write_db({"series": []}, "2026-09-17T02:50:00Z", rows_changed=False)
        assert fake.health[0][1] == "error"


class TestAlertWire:
    def _qualifying_current(self, date):
        return {
            "date": date,
            "level": -0.63,
            "delta_1d": -1.20,
            "delta_z": -3.4,
            "delta_std_10y": 0.35,
            "rank_decline_10y": 3,
            "rank_surge_10y": 2500,
            "rank_n": 2509,
            "legs": {
                "vix": {"value": 15.44, "z": -0.8},
                "vvix": {"value": 87.72, "z": -1.1},
                "ts": {"value": 0.8323, "z": -0.8, "vix3m": 18.55},
                "skew": {"value": 145.70, "z": 0.2},
            },
        }

    def test_fires_once_with_priority_0_payload(self, monkeypatch):
        import fetch_panic_index as mod
        from watchdog import notify

        captured = []

        def _http_post(url, payload, headers=None):
            captured.append(payload)
            return 200, b'{"status":1}'

        monkeypatch.setenv("PUSHOVER_USER", "user")
        monkeypatch.setenv("PUSHOVER_TOKEN", "token")
        monkeypatch.setattr(notify, "_http_post", _http_post)

        current = self._qualifying_current("2026-09-17")
        payload = {
            "status": "ok",
            "expected_session": "2026-09-17",
            "current": current,
            "alert": {},
        }
        assert should_fire_decline(current, status="ok", expected_session="2026-09-17")
        mod._maybe_alert(payload, {"alert": {}}, no_alert=False)
        assert len(captured) == 1
        sent = captured[0]
        assert sent["priority"] == 0
        assert sent["url"] == "https://app.radon.run/regime/panic-index"
        assert sent["url_title"] == "Open Panic Proxy"
        assert sent["title"] == "radon panic proxy: record 1d decline"
        assert "Rank #3 most negative of 2509 sessions" in sent["message"]
        assert "-3.4σ" in sent["message"] or "-3.4" in sent["message"]
        assert sent["message"].startswith("TOP-3 IN 10Y. ")
        assert payload["alert"]["last_fired_date"] == "2026-09-17"
        assert payload["alert"]["last_fired_kind"] == "decline"

    def test_does_not_fire_when_already_fired_this_session(self, monkeypatch):
        import fetch_panic_index as mod

        dispatched = []
        monkeypatch.setattr(mod, "_dispatch_alert", lambda msg: dispatched.append(msg))
        current = self._qualifying_current("2026-09-17")
        payload = {
            "status": "ok",
            "expected_session": "2026-09-17",
            "current": current,
            "alert": {"last_fired_date": "2026-09-17", "last_fired_kind": "decline"},
        }
        mod._maybe_alert(
            payload,
            {"alert": {"last_fired_date": "2026-09-17", "last_fired_kind": "decline"}},
            no_alert=False,
        )
        assert dispatched == []

    def test_does_not_fire_when_data_date_is_not_expected_session(self, monkeypatch):
        import fetch_panic_index as mod

        dispatched = []
        monkeypatch.setattr(mod, "_dispatch_alert", lambda msg: dispatched.append(msg))
        current = self._qualifying_current("2026-09-16")
        payload = {
            "status": "ok",
            "expected_session": "2026-09-17",
            "current": current,
            "alert": {},
        }
        mod._maybe_alert(payload, {}, no_alert=False)
        assert dispatched == []

    def test_does_not_fire_under_no_alert(self, monkeypatch):
        import fetch_panic_index as mod

        dispatched = []
        monkeypatch.setattr(mod, "_dispatch_alert", lambda msg: dispatched.append(msg))
        current = self._qualifying_current("2026-09-17")
        payload = {
            "status": "ok",
            "expected_session": "2026-09-17",
            "current": current,
            "alert": {},
        }
        mod._maybe_alert(payload, {}, no_alert=True)
        assert dispatched == []

    def test_missing_creds_do_not_fail_the_job(self, monkeypatch):
        import fetch_panic_index as mod

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        current = self._qualifying_current("2026-09-17")
        payload = {
            "status": "ok",
            "expected_session": "2026-09-17",
            "current": current,
            "alert": {},
        }
        mod._maybe_alert(payload, {}, no_alert=False)
        assert payload["alert"].get("last_fired_date") is None


class TestAlertCopy:
    def test_format_contains_rank_and_sigma(self):
        message = format_alert_message(
            {
                "level": -0.63,
                "delta_1d": -1.20,
                "delta_z": -3.4,
                "rank_decline_10y": 12,
                "rank_n": 2509,
                "legs": {
                    "vix": {"z": -0.8},
                    "vvix": {"z": -1.1},
                    "ts": {"z": -0.8},
                    "skew": {"z": 0.2},
                },
            }
        )
        assert "Rank #12 most negative of 2509 sessions" in message
        assert "Radon proxy, not GS." in message
        assert not message.startswith("TOP-")


# ── migration + upsert ────────────────────────────────────────────


class TestPanicIndexStorage:
    def _db(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
        db.executescript(MIGRATION.read_text())
        return db

    def test_migration_applies_version_77_and_null_round_trip(self):
        db = self._db()
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 77
        cols = {r[1]: r[3] for r in db.execute("PRAGMA table_info(panic_index_history)")}
        assert cols["vix_close"] == 1
        assert cols["z_vix"] == 0
        assert cols["level"] == 0
        assert cols["delta_1d"] == 0
        from db import writer

        db.execute(
            writer.PANIC_INDEX_UPSERT_SQL,
            ("2010-09-18", 20.0, 22.0, 90.0, 120.0, 0.91, None, None, None, None, None, None, "t"),
        )
        row = db.execute(
            "SELECT z_vix, level, delta_1d FROM panic_index_history"
        ).fetchone()
        assert row == (None, None, None)

    def test_upsert_is_idempotent_per_date(self):
        from db import writer

        db = self._db()
        args1 = ("2026-09-17", 15.44, 18.55, 87.72, 145.7, 0.8323, -0.8, -1.0, -0.7, 0.1, -0.6, -0.1, "t1")
        args2 = ("2026-09-17", 15.44, 18.55, 87.72, 145.7, 0.8323, -0.9, -1.0, -0.7, 0.1, -0.7, -0.2, "t2")
        db.execute(writer.PANIC_INDEX_UPSERT_SQL, args1)
        db.execute(writer.PANIC_INDEX_UPSERT_SQL, args2)
        rows = db.execute("SELECT level, delta_1d FROM panic_index_history").fetchall()
        assert rows == [(-0.7, -0.2)]


# ── live C.8 anchors ──────────────────────────────────────────────


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("CI") == "true", reason="live Cboe CDN; CI uses fixtures")
def test_live_files_reproduce_c8_anchors():
    from clients.cboe_client import CboeClient
    from lib.panic_index_math import attach_delta, attach_level, attach_z_scores, compute_stats, join_series

    client = CboeClient()
    parsed = {}
    for symbol, column in (("VIX", "CLOSE"), ("VIX3M", "CLOSE"), ("VVIX", "VVIX"), ("SKEW", "SKEW")):
        text, _ = client.fetch_history(symbol)
        # C8 anchors describe the historical window through September 17.
        # New CDN sessions must not change its fixed distribution/count proof.
        parsed[symbol] = [
            row for row in parse_index_csv(text, column)
            if row["date"] <= "2026-09-17"
        ]
    series, dropped, _base = join_series(parsed["VIX"], parsed["VIX3M"], parsed["VVIX"], parsed["SKEW"])
    attach_z_scores(series)
    attach_level(series)
    attach_delta(series)
    by = {row["date"]: row for row in series}

    a = by["2024-08-05"]
    assert a["vix"] == pytest.approx(38.57, abs=1e-4)
    assert a["vvix"] == pytest.approx(173.32, abs=1e-4)
    assert a["ts"] == pytest.approx(1.1442, abs=1e-4)
    assert a["skew"] == pytest.approx(145.30, abs=1e-2)
    assert a["z_vix"] == pytest.approx(8.908, abs=1e-3)
    assert a["level"] == pytest.approx(5.8223, abs=1e-4)

    b = by["2024-08-06"]
    assert b["level"] == pytest.approx(3.4812, abs=1e-4)
    assert b["delta_1d"] == pytest.approx(-2.3411, abs=1e-4)

    c = by["2026-09-16"]
    assert c["level"] == pytest.approx(0.0013, abs=1e-4)

    d = by["2026-09-17"]
    assert d["vix"] == pytest.approx(15.44, abs=1e-4)
    assert d["vvix"] == pytest.approx(87.72, abs=1e-4)
    assert d["ts"] == pytest.approx(0.8323, abs=1e-4)
    assert d["level"] == pytest.approx(-0.6256, abs=1e-4)
    assert d["delta_1d"] == pytest.approx(-0.6269, abs=1e-4)

    stats = compute_stats(series)
    assert stats["stddev"] == pytest.approx(0.3574, abs=1e-4)
    assert stats["low_date"] == "2024-08-06"
    assert stats["high_date"] == "2018-02-05"
    assert stats["high"] == pytest.approx(3.7143, abs=1e-4)
    deltas = [row["delta_1d"] for row in series if row["delta_1d"] is not None]
    assert len(deltas) == 4016
    assert dropped == [
        "2010-11-11", "2011-02-09", "2013-05-13", "2017-09-14",
        "2018-12-03", "2019-07-05", "2024-11-29",
    ]

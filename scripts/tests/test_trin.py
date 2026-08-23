"""TRIN (NYSE Arms Index) 60-minute indicator — red tests against
docs/indicators/trin.md.

Daily fixture facts come from running breadth's StockCharts parser over the
captured $TRIN sample (37 sessions, 2026-07-01 1.26 → 2026-08-21 0.68). Hourly
sampler facts use synthetic UTC stamps inside a known ET session.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from breadth_scan import parse_stockcharts_daily
from fetch_trin import (
    MA_PERIOD,
    ZONE_HIGH,
    ZONE_LOW,
    ZONE_NEAR,
    HOURLY_PAYLOAD_CAP,
    bucket_hourly,
    build_output,
    classify_state,
    extract_index_value,
    hourly_bucket,
    main,
    moving_average,
    persist_result,
    session_date,
)

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0054_trin.sql"

# 2026-08-21 is a Friday; 13:30Z = 09:30 ET (EDT, UTC-4).
SESSION_OPEN_UTC = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)


def _stamp(minutes_after_open: int) -> str:
    return (SESSION_OPEN_UTC + timedelta(minutes=minutes_after_open)).isoformat().replace("+00:00", "Z")


def _samples(values: list[float], step_min: int = 5) -> list[dict]:
    return [
        {"ts": _stamp(i * step_min), "session_date": "2026-08-21", "trin": v}
        for i, v in enumerate(values)
    ]


@pytest.fixture(scope="module")
def daily():
    return parse_stockcharts_daily(json.load((FIXTURES / "trin_sample.json").open()))


class TestConstants:
    def test_zone_thresholds_match_the_chart(self):
        assert ZONE_LOW == 0.60
        assert ZONE_NEAR == 0.65
        assert ZONE_HIGH == 1.50
        assert MA_PERIOD == 10


class TestDailyFixture:
    def test_parses_37_sessions(self, daily):
        assert len(daily) == 37
        assert daily[0] == ("2026-07-01", 1.26)
        assert daily[-1] == ("2026-08-21", 0.68)
        assert [d for d, _ in daily] == sorted(d for d, _ in daily)


class TestSessionBuckets:
    def test_rth_buckets_in_et(self):
        assert hourly_bucket(_stamp(0)) == "2026-08-21T09:30"
        assert hourly_bucket(_stamp(59)) == "2026-08-21T09:30"
        assert hourly_bucket(_stamp(60)) == "2026-08-21T10:30"
        assert hourly_bucket(_stamp(6 * 60)) == "2026-08-21T15:30"
        assert hourly_bucket(_stamp(6 * 60 + 29)) == "2026-08-21T15:30"

    def test_outside_rth_is_none(self):
        assert hourly_bucket(_stamp(-5)) is None
        assert hourly_bucket(_stamp(6 * 60 + 30)) is None  # 16:00 ET close

    def test_session_date_is_et(self):
        assert session_date(_stamp(0)) == "2026-08-21"
        # 23:30Z Friday is still 19:30 ET Friday.
        assert session_date(_stamp(10 * 60)) == "2026-08-21"

    def test_bucket_hourly_keeps_the_last_sample_per_bucket(self):
        samples = _samples([1.0, 0.9, 0.8, 0.7] + [1.1] * 8 + [0.5] * 12)  # 0-15,20-55,60-115 min
        bars = bucket_hourly(samples)
        assert [b["bucket"] for b in bars] == ["2026-08-21T09:30", "2026-08-21T10:30"]
        assert bars[0]["trin"] == 1.1  # last sample inside 09:30-10:30
        assert bars[1]["trin"] == 0.5
        assert bars[0]["ts"] == _stamp(55)

    def test_bucket_hourly_drops_off_session_samples(self):
        samples = (
            [{"ts": _stamp(-30), "session_date": "2026-08-21", "trin": 9.0}]
            + _samples([0.7])
            + [{"ts": _stamp(6 * 60 + 35), "session_date": "2026-08-21", "trin": 8.0}]  # 16:05 ET
        )
        assert [b["trin"] for b in bucket_hourly(samples)] == [0.7]


class TestMovingAverageAndState:
    def test_ma_needs_ten_bars(self):
        assert moving_average([0.5] * 9) is None
        assert moving_average([0.5] * 10) == pytest.approx(0.5)
        assert moving_average([float(i) for i in range(1, 21)]) == pytest.approx(15.5)  # last ten: 11..20

    def test_state_boundaries_are_inclusive_on_the_zone_side(self):
        assert classify_state(0.60) == "in_zone"
        assert classify_state(0.6000001) == "near_zone"
        assert classify_state(0.65) == "near_zone"
        assert classify_state(0.6500001) == "neutral"
        assert classify_state(1.50) == "elevated"
        assert classify_state(1.4999999) == "neutral"
        # REL-067 / R-160: no MA(10) is not a mid-range reading. `state` is
        # the field the tab colours on, so "no reading yet" and "reading is
        # mid-range, no signal" used to render identically as an all-clear.
        assert classify_state(None) is None


class TestExtractIndexValue:
    def test_prefers_last_then_close_then_bid_ask_mid(self):
        assert extract_index_value({"last": 0.68, "close": 0.7, "bid": 0.6, "ask": 0.8}) == 0.68
        assert extract_index_value({"last": float("nan"), "close": 0.7, "bid": 0.6, "ask": 0.8}) == 0.7
        assert extract_index_value({"last": None, "close": None, "bid": 0.6, "ask": 0.8}) == pytest.approx(0.7)

    def test_non_positive_or_non_finite_is_none(self):
        assert extract_index_value({"last": 0.0, "close": -1.0, "bid": None, "ask": None}) is None
        assert extract_index_value({"last": float("inf")}) is None


class TestBuildOutput:
    def test_current_from_samples_and_daily(self, daily):
        # 14 hourly bars across two sessions: ten at 0.9 then four descending.
        samples = []
        for hour, trin in enumerate([0.9] * 10 + [0.7, 0.62, 0.58, 0.55]):
            base = SESSION_OPEN_UTC - timedelta(days=1) if hour < 7 else SESSION_OPEN_UTC
            minute = (hour % 7) * 60 + 25  # slot 6 = 15:55 ET, inside the last bar
            ts = (base + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")
            samples.append({"ts": ts, "session_date": session_date(ts), "trin": trin, "adv": 1500, "dec": 1300, "up_vol": 2.0e9, "down_vol": 1.5e9})
        payload = build_output(samples, daily, scan_time="2026-08-21T19:55:00Z", source="ib+stockcharts")
        assert payload["scan_time"] == "2026-08-21T19:55:00Z"
        assert len(payload["hourly"]) == 14
        current = payload["current"]
        assert current["trin"] == 0.55
        assert current["ma10"] == pytest.approx(sum([0.9] * 6 + [0.7, 0.62, 0.58, 0.55]) / 10)
        assert current["state"] == "neutral"
        assert current["daily_close"] == 0.68
        assert current["daily_date"] == "2026-08-21"
        assert current["zone_low"] == ZONE_LOW and current["zone_near"] == ZONE_NEAR and current["zone_high"] == ZONE_HIGH
        assert payload["hourly"][-1]["ma10"] == pytest.approx(current["ma10"])
        assert payload["hourly"][0]["ma10"] is None
        assert payload["daily"][-1] == {"date": "2026-08-21", "close": 0.68}

    def test_in_zone_when_ma_drops_to_the_line(self, daily):
        samples = []
        for hour, trin in enumerate([0.60] * 10):
            base = SESSION_OPEN_UTC - timedelta(days=1) if hour < 7 else SESSION_OPEN_UTC
            ts = (base + timedelta(minutes=(hour % 7) * 60 + 10)).isoformat().replace("+00:00", "Z")
            samples.append({"ts": ts, "session_date": session_date(ts), "trin": trin})
        current = build_output(samples, daily)["current"]
        assert current["ma10"] == pytest.approx(0.60)
        assert current["state"] == "in_zone"

    def test_no_samples_yet_still_carries_daily(self, daily):
        payload = build_output([], daily)
        assert payload["hourly"] == []
        assert payload["current"]["trin"] is None
        assert payload["current"]["ma10"] is None
        assert payload["current"]["state"] is None  # R-160
        assert payload["current"]["daily_close"] == 0.68

    def test_hourly_payload_is_capped(self, daily):
        samples = []
        for hour in range(HOURLY_PAYLOAD_CAP + 30):
            day, slot = divmod(hour, 7)
            base = SESSION_OPEN_UTC - timedelta(days=70 - day)  # all inside EDT
            ts = (base + timedelta(minutes=slot * 60 + 5)).isoformat().replace("+00:00", "Z")
            samples.append({"ts": ts, "session_date": session_date(ts), "trin": 1.0})
        assert len(build_output(samples, daily)["hourly"]) == HOURLY_PAYLOAD_CAP


@pytest.fixture()
def persist_calls(monkeypatch, tmp_path):
    import fetch_trin as ft

    calls: list[tuple] = []
    monkeypatch.setattr(ft, "TRIN_JSON", tmp_path / "trin.json")
    monkeypatch.setattr(ft.writer, "ensure_no_replica_for_writers", lambda: calls.append(("guard",)))
    monkeypatch.setattr(ft.writer, "upsert_trin_samples", lambda rows, recorded_at: calls.append(("samples", len(rows))))
    monkeypatch.setattr(ft.writer, "upsert_trin_daily_rows", lambda rows, recorded_at: calls.append(("daily", len(rows))))
    monkeypatch.setattr(ft.writer, "upsert_scan_snapshot", lambda service, scan_time, payload: calls.append(("snapshot", service)))
    monkeypatch.setattr(
        ft.writer,
        "record_service_health",
        lambda service, state, finished_at=None: calls.append(("health", service, state)),
    )
    return calls


class TestPersistResult:
    def test_refuses_when_nothing_to_persist(self, persist_calls):
        persist_result(build_output([], []), [], [])
        assert persist_calls == []

    def test_writes_in_order(self, persist_calls, daily):
        sample = {"ts": _stamp(5), "session_date": "2026-08-21", "trin": 0.68, "source": "ib"}
        persist_result(build_output([sample], daily), [sample], [("2026-08-21", 0.68)])
        assert persist_calls == [("guard",), ("samples", 1), ("daily", 1), ("snapshot", "trin"), ("health", "trin", "ok")]

    def test_off_hours_heartbeats_daily_only(self, persist_calls, daily):
        persist_result(build_output([], daily), [], [])
        kinds = [c[0] for c in persist_calls]
        assert "samples" not in kinds
        assert ("snapshot", "trin") in persist_calls
        assert ("health", "trin", "ok") in persist_calls


class TestStorage:
    @pytest.fixture()
    def db(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
        conn.executescript(MIGRATION.read_text())
        yield conn
        conn.close()

    def test_migration_registers_version_54(self, db):
        assert [r[0] for r in db.execute("SELECT version FROM schema_migrations")] == [54]

    def test_schema_columns(self, db):
        assert [r[1] for r in db.execute("PRAGMA table_info(trin_samples)")] == [
            "ts", "session_date", "trin", "adv", "dec", "up_vol", "down_vol", "source", "recorded_at",
        ]
        assert [r[1] for r in db.execute("PRAGMA table_info(trin_daily)")] == ["date", "close", "recorded_at"]

    def test_upserts_are_idempotent(self, db):
        from db import writer

        db.execute(writer.TRIN_SAMPLE_UPSERT_SQL, ("2026-08-21T13:35:00Z", "2026-08-21", 0.70, None, None, None, None, "ib", "r1"))
        db.execute(writer.TRIN_SAMPLE_UPSERT_SQL, ("2026-08-21T13:35:00Z", "2026-08-21", 0.68, 1500, 1300, 2.0e9, 1.5e9, "ib", "r2"))
        assert list(db.execute("SELECT trin, adv, recorded_at FROM trin_samples")) == [(0.68, 1500, "r2")]
        db.execute(writer.TRIN_DAILY_UPSERT_SQL, ("2026-08-21", 0.70, "r1"))
        db.execute(writer.TRIN_DAILY_UPSERT_SQL, ("2026-08-21", 0.68, "r2"))
        assert list(db.execute("SELECT close FROM trin_daily")) == [(0.68,)]


class TestCli:
    def test_json_flag_prints_payload_only(self, monkeypatch, capsys, daily):
        import fetch_trin as ft

        monkeypatch.setattr(ft, "load_cached_samples", lambda: [])
        monkeypatch.setattr(ft, "load_cached_daily", lambda: [])
        monkeypatch.setattr(ft, "sample_live", lambda: (None, "ib-skipped"))
        monkeypatch.setattr(ft, "fetch_daily", lambda: daily)
        monkeypatch.setattr(ft, "persist_result", lambda payload, s, d, health_error=None: None)
        assert main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["current"]["daily_close"] == 0.68
        assert payload["hourly"] == []

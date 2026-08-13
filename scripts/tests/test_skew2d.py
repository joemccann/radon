"""SKEW 2D indicator — two-session change in SPX 1M put/call ratio.

Ground truth is the checked-in skew_history dump:
  fixtures/skew2d_ratios_sample.json — 733 sessions captured 2026-08-09.
Expected 2d stats below were derived by applying ratio_t - ratio_{t-2} to
that fixture directly, not computed by hand. Spec: docs/indicators/skew2d.md.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = json.loads((FIXTURES / "skew2d_ratios_sample.json").read_text())
SAMPLE_ROWS: list[dict[str, Any]] = SAMPLE["rows"]
MIGRATION = Path(__file__).parents[1] / "db" / "migrations" / "0039_skew2d.sql"

# Fixture-derived pins (recomputed below in test_fixture_stats_match_pin so the
# constants stay honest if the fixture is refreshed).
PIN_COUNT = 733
PIN_CHANGE_COUNT = 731
PIN_HIGH = 0.37540121157999673
PIN_LOW = -0.2550619076592615
PIN_AVG = -9.866255423597131e-05
PIN_STDDEV = 0.04798679737838151
PIN_LAST_DATE = "2026-08-07"
PIN_LAST_RATIO = 1.23838134631528
PIN_LAST_CHANGE = -0.0036750737861990235

HAND_RATIOS = [
    {"date": "2026-08-01", "expiry": "2026-08-21", "dte": 20, "put_iv": 0.15, "call_iv": 0.12, "ratio": 1.30},
    {"date": "2026-08-04", "expiry": "2026-08-21", "dte": 17, "put_iv": 0.15, "call_iv": 0.12, "ratio": 1.28},
    {"date": "2026-08-05", "expiry": "2026-08-21", "dte": 16, "put_iv": 0.15, "call_iv": 0.12, "ratio": 1.25},
    {"date": "2026-08-06", "expiry": "2026-08-21", "dte": 15, "put_iv": 0.15, "call_iv": 0.12, "ratio": 1.20},
    {"date": "2026-08-07", "expiry": "2026-08-21", "dte": 14, "put_iv": 0.15, "call_iv": 0.12, "ratio": 1.24},
]
HAND_CHANGES = [None, None, -0.05, -0.08, -0.01]


def _import_fetch():
    """Import after path is set; red phase fails on missing module."""
    from fetch_skew2d import (  # type: ignore[import-not-found]
        compute_change_2d_series,
        compute_stats,
        build_payload,
        run,
    )
    return compute_change_2d_series, compute_stats, build_payload, run


# ── Fixture integrity ─────────────────────────────────────────────


class TestFixturePins:
    def test_sample_has_expected_row_count(self):
        assert len(SAMPLE_ROWS) == PIN_COUNT
        assert SAMPLE_ROWS[0]["date"] == "2023-09-06"
        assert SAMPLE_ROWS[-1]["date"] == PIN_LAST_DATE
        assert SAMPLE_ROWS[-1]["ratio"] == pytest.approx(PIN_LAST_RATIO, abs=1e-12)

    def test_fixture_stats_match_pin(self):
        """Recompute 2d stats from the fixture so pins cannot rot silently."""
        changes: list[float] = []
        for i, row in enumerate(SAMPLE_ROWS):
            if i < 2:
                continue
            changes.append(float(row["ratio"]) - float(SAMPLE_ROWS[i - 2]["ratio"]))
        assert len(changes) == PIN_CHANGE_COUNT
        assert max(changes) == pytest.approx(PIN_HIGH, abs=1e-12)
        assert min(changes) == pytest.approx(PIN_LOW, abs=1e-12)
        assert sum(changes) / len(changes) == pytest.approx(PIN_AVG, abs=1e-12)
        assert statistics.pstdev(changes) == pytest.approx(PIN_STDDEV, abs=1e-12)
        assert changes[-1] == pytest.approx(PIN_LAST_CHANGE, abs=1e-12)


# ── Pure transforms ───────────────────────────────────────────────


class TestComputeChange2d:
    def test_hand_series_two_session_lag(self):
        compute_change_2d_series, _, _, _ = _import_fetch()
        series = compute_change_2d_series(HAND_RATIOS)
        assert [r["change"] for r in series] == HAND_CHANGES
        assert [r["ratio"] for r in series] == [r["ratio"] for r in HAND_RATIOS]
        assert series[0]["date"] == "2026-08-01"
        assert series[-1]["expiry"] == "2026-08-21"

    def test_short_series_all_null_changes(self):
        compute_change_2d_series, _, _, _ = _import_fetch()
        series = compute_change_2d_series(HAND_RATIOS[:2])
        assert all(r["change"] is None for r in series)

    def test_empty_input(self):
        compute_change_2d_series, _, _, _ = _import_fetch()
        assert compute_change_2d_series([]) == []

    def test_fixture_last_change(self):
        compute_change_2d_series, _, _, _ = _import_fetch()
        series = compute_change_2d_series(SAMPLE_ROWS)
        assert series[0]["change"] is None
        assert series[1]["change"] is None
        assert series[-1]["change"] == pytest.approx(PIN_LAST_CHANGE, abs=1e-12)
        assert series[-1]["date"] == PIN_LAST_DATE


class TestComputeStats:
    def test_hand_stats(self):
        _, compute_stats, _, _ = _import_fetch()
        series = [
            {"change": c} for c in HAND_CHANGES
        ]
        stats = compute_stats(series)
        assert stats is not None
        vals = [c for c in HAND_CHANGES if c is not None]
        assert stats["high"] == pytest.approx(max(vals))
        assert stats["low"] == pytest.approx(min(vals))
        assert stats["avg"] == pytest.approx(sum(vals) / len(vals))
        assert stats["stddev"] == pytest.approx(statistics.pstdev(vals))

    def test_all_null_returns_none(self):
        _, compute_stats, _, _ = _import_fetch()
        assert compute_stats([{"change": None}, {"change": None}]) is None

    def test_fixture_stats(self):
        compute_change_2d_series, compute_stats, _, _ = _import_fetch()
        series = compute_change_2d_series(SAMPLE_ROWS)
        stats = compute_stats(series)
        assert stats is not None
        assert stats["high"] == pytest.approx(PIN_HIGH, abs=1e-12)
        assert stats["low"] == pytest.approx(PIN_LOW, abs=1e-12)
        assert stats["avg"] == pytest.approx(PIN_AVG, abs=1e-12)
        assert stats["stddev"] == pytest.approx(PIN_STDDEV, abs=1e-12)


class TestBuildPayload:
    def test_payload_contract_shape(self):
        compute_change_2d_series, _, build_payload, _ = _import_fetch()
        series = compute_change_2d_series(SAMPLE_ROWS)
        scan_time = "2026-08-09T21:50:00+00:00"
        payload = build_payload(series, scan_time=scan_time)
        assert payload["scan_time"] == scan_time
        assert payload["source"] == "skew_history"
        assert payload["count"] == PIN_COUNT
        assert payload["current"]["date"] == PIN_LAST_DATE
        assert payload["current"]["ratio"] == pytest.approx(PIN_LAST_RATIO, abs=1e-12)
        assert payload["current"]["change"] == pytest.approx(PIN_LAST_CHANGE, abs=1e-12)
        assert payload["stats"]["stddev"] == pytest.approx(PIN_STDDEV, abs=1e-12)
        assert len(payload["series"]) == PIN_COUNT
        # series entries only need date/ratio/change for the chart contract
        assert set(payload["series"][0].keys()) >= {"date", "ratio", "change"}


# ── Migration schema pin ──────────────────────────────────────────


class TestMigration:
    def test_schema_and_version(self):
        assert MIGRATION.exists(), "0039_skew2d.sql must exist"
        sql = MIGRATION.read_text()
        conn = sqlite3.connect(":memory:")
        conn.executescript(sql)
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(skew2d_history)").fetchall()
        }
        assert cols == {
            "date", "expiry", "dte", "put_iv", "call_iv", "ratio", "change", "recorded_at",
        }
        version = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 39"
        ).fetchone()
        assert version == (39,)

    def test_upsert_idempotent_on_date(self):
        from db import writer  # type: ignore

        assert hasattr(writer, "upsert_skew2d_rows"), "writer.upsert_skew2d_rows missing"
        assert hasattr(writer, "SKEW2D_UPSERT_SQL"), "writer.SKEW2D_UPSERT_SQL missing"

        sql = MIGRATION.read_text()
        conn = sqlite3.connect(":memory:")
        conn.executescript(sql)
        # Apply the single-row SQL template twice and assert one row remains.
        stamp = "2026-08-09T21:50:00Z"
        args = (
            "2026-08-07", "2026-08-21", 14, 0.15, 0.12, 1.24, -0.01, stamp,
        )
        conn.execute(writer.SKEW2D_UPSERT_SQL, args)
        conn.execute(
            writer.SKEW2D_UPSERT_SQL,
            ("2026-08-07", "2026-08-21", 14, 0.16, 0.13, 1.25, 0.02, stamp),
        )
        rows = conn.execute("SELECT ratio, change FROM skew2d_history").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == pytest.approx(1.25)
        assert rows[0][1] == pytest.approx(0.02)


# ── run() heartbeat / no-change path ──────────────────────────────


class TestRunHeartbeat:
    def test_stale_parent_session_cannot_emit_fresh_ok_child(self, monkeypatch):
        _, _, _, run = _import_fetch()
        import fetch_skew2d as mod

        monkeypatch.setattr(mod, "load_ratio_rows", lambda: HAND_RATIOS)
        monkeypatch.setattr(mod, "persist_json", lambda _payload: pytest.fail("stale payload persisted"))
        with pytest.raises(ValueError, match="stale parent session"):
            run(now=datetime(2026, 8, 10, 22, 0, tzinfo=timezone.utc))

    def test_unchanged_source_refreshes_snapshot_without_row_upserts(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        _, _, _, run = _import_fetch()
        import fetch_skew2d as mod  # type: ignore

        series_rows = HAND_RATIOS
        monkeypatch.setattr(mod, "load_ratio_rows", lambda: series_rows)

        upserts: list[Any] = []
        snapshots: list[Any] = []
        healths: list[Any] = []

        class FakeWriter:
            def ensure_no_replica_for_writers(self):
                return None

            def upsert_skew2d_rows(self, rows, recorded_at=None):
                upserts.append((rows, recorded_at))

            def upsert_scan_snapshot(self, service, scan_time, payload):
                snapshots.append((service, scan_time, payload))

            def record_service_health(self, service, status, finished_at=None, **_kw):
                healths.append((service, status, finished_at))

        monkeypatch.setattr(mod, "writer", FakeWriter())
        # Seed a prior payload fingerprint matching the computed series.
        compute_change_2d_series, _, build_payload, _ = _import_fetch()
        prior = build_payload(
            compute_change_2d_series(series_rows),
            scan_time="2026-08-08T21:50:00+00:00",
        )
        monkeypatch.setattr(mod, "load_prior_payload", lambda: prior)
        monkeypatch.setattr(mod, "persist_json", lambda payload: None)

        now = datetime(2026, 8, 9, 21, 50, tzinfo=timezone.utc)
        payload = run(now=now)

        assert payload["count"] == 5
        assert snapshots, "snapshot must refresh even when rows unchanged"
        assert snapshots[0][0] == "skew2d"
        assert healths and healths[0][0] == "skew2d" and healths[0][1] == "ok"
        assert upserts == [], "no history upsert when ratios fingerprint matches"

"""REL-213 (R-574, R-575, R-577): every iv-spread exit path writes a
heartbeat, and the heartbeat is decoupled from the snapshot write."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_iv_spread as ivs  # noqa: E402


@pytest.fixture()
def health_rows(monkeypatch):
    rows: list[dict] = []

    class FakeWriter:
        @staticmethod
        def ensure_no_replica_for_writers():
            return None

        @staticmethod
        def upsert_iv_spread_rows(*a, **k):
            return None

        @staticmethod
        def upsert_scan_snapshot(*a, **k):
            return None

        @staticmethod
        def record_service_health(service, state, **kwargs):
            rows.append({"service": service, "state": state, **kwargs})

    monkeypatch.setattr(ivs, "writer", FakeWriter())
    return rows


class TestHeartbeatDecoupledFromSnapshot:
    def test_snapshot_failure_still_writes_the_error_row(self, monkeypatch, health_rows):
        """R-575: one try covered both writes, so the dead-writer failure the
        heartbeat exists to surface also killed the heartbeat."""

        def boom(*a, **k):
            raise RuntimeError("turso down")

        monkeypatch.setattr(ivs.writer, "upsert_scan_snapshot", boom)
        ivs._write_db({"x": 1}, "2026-09-03T22:15:00Z", rows_changed=False, health_error=None)
        assert health_rows, "no heartbeat after the snapshot write failed"
        assert health_rows[-1]["state"] == "error"


class TestEveryExitPathHeartbeats:
    def _no_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ivs, "load_prior_payload", lambda: None)
        monkeypatch.setattr(ivs, "persist_json", lambda payload: None)

    def test_ib_down_with_no_cache_writes_error_before_raising(
        self, monkeypatch, tmp_path, health_rows
    ):
        """R-574: first-ever run on a fresh host with IB in 2FA exited 1 with
        no error row."""
        self._no_cache(monkeypatch, tmp_path)
        monkeypatch.setattr(ivs, "gateway_auth_state", lambda: None)

        def failing_fetch(duration):
            raise RuntimeError("socket dead")

        monkeypatch.setattr(
            sys, "argv", ["fetch_iv_spread.py"]
        )
        monkeypatch.setattr(ivs, "_real_ib_fetch", failing_fetch)
        with pytest.raises(SystemExit) as exc:
            ivs.main()
        assert exc.value.code not in (0, None)
        assert health_rows and health_rows[-1]["state"] == "error"

    def test_auth_pregate_crash_writes_error(self, monkeypatch, tmp_path, health_rows):
        """R-577: gateway_auth_state ran outside any health-writing handler."""
        self._no_cache(monkeypatch, tmp_path)

        def boom():
            raise RuntimeError("preflight import broke")

        monkeypatch.setattr(ivs, "gateway_auth_state", boom)
        monkeypatch.setattr(sys, "argv", ["fetch_iv_spread.py"])
        with pytest.raises(SystemExit):
            ivs.main()
        assert health_rows and health_rows[-1]["state"] == "error"

    def test_no_common_session_writes_error(self, monkeypatch, tmp_path, health_rows):
        self._no_cache(monkeypatch, tmp_path)
        monkeypatch.setattr(ivs, "gateway_auth_state", lambda: "authenticated")
        monkeypatch.setattr(
            ivs, "_real_ib_fetch",
            lambda duration: {"SPX": [], "NDX": []},
        )
        monkeypatch.setattr(ivs, "_validate_legs", lambda raw: raw)
        monkeypatch.setattr(ivs, "load_history", lambda: [])
        monkeypatch.setattr(ivs, "merge_history", lambda stored, legs: [])
        monkeypatch.setattr(ivs, "count_unpaired", lambda stored, legs: 0)
        monkeypatch.setattr(sys, "argv", ["fetch_iv_spread.py"])
        with pytest.raises(SystemExit):
            ivs.main()
        assert health_rows and health_rows[-1]["state"] == "error"
        assert "session" in str(health_rows[-1].get("error", "")).lower()

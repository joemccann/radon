"""R-395 / R-396 / R-421 / REL-139: a failed scan is not quiet, an unpersisted one is not ok.

R-395: `_heartbeat_soft_fail` writes `next_attempt_at = now + 900s`, but
`next_attempt_at` is not "the next cadence fire" anywhere else in this repo --
it is a writer's own CIRCUIT-BREAKER embargo, and two independent consumers read
it as documented-normal quiet: `check.py` suppresses the alert past hysteresis
while the embargo is in the future, and `classify.py`'s `_is_expected_quiet`
drops the row with no hysteresis at all. A fresh 15-minute embargo written every
cycle is always in the future, so a permanently-failing cri-scan pages exactly
once and then never again.

R-396: when the persist reserve is exhausted the payload still lands in
`results`, `_write_disk_mirror` restamps `generated_at`, and the cycle exits
`ok` -- nothing distinguishes a run that wrote all three indices to Turso from
one that wrote none.

R-421: `persist_deadline = deadline + PERSIST_RESERVE_S` is exactly
`TimeoutStartSec`, so the guard is unreachable on the first index; and the same
absolute point is shared by every index, so two of three can start a ~600s
persist into a 599s window.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import data_refresh  # noqa: E402
from watchdog import check as watchdog_check  # noqa: E402
from incident_watchdog import classify  # noqa: E402


class TestSoftFailIsNotAnEmbargo:
    def test_the_heartbeat_carries_no_next_attempt_at(self, monkeypatch):
        """An ordinary next-cadence retry is not a circuit-breaker embargo."""
        rows: list[tuple] = []

        class _Writer:
            @staticmethod
            def ensure_no_replica_for_writers():
                return None

            @staticmethod
            def record_service_health(service, state, **kwargs):
                rows.append((service, state, kwargs))

        monkeypatch.setitem(sys.modules, "db.writer", _Writer)
        script = next(iter(data_refresh._SCRIPT_SERVICES))
        data_refresh._heartbeat_soft_fail(script, "cri_scan.py exited 1")

        assert rows, "no heartbeat written"
        _service, state, kwargs = rows[0]
        assert state == "error"
        error = kwargs["error"]
        assert "next_attempt_at" not in error, (
            "two consumers read next_attempt_at as a circuit-breaker embargo and "
            f"go quiet for good: {error}"
        )
        assert "cri_scan.py exited 1" in error["message"]

    def test_a_permanently_failing_scan_keeps_firing(self):
        """check.py must not suppress past hysteresis without an embargo."""
        now = datetime.now(timezone.utc)
        row = {"message": "cri_scan.py exited 1"}
        assert watchdog_check._embargo_deadline(row) is None, (
            "a soft-fail row must carry no embargo deadline at all"
        )

    def test_the_incident_watchdog_does_not_call_it_expected_quiet(self):
        now = datetime.now(timezone.utc)
        soft_fail = {"state": "error", "last_error": json.dumps({"message": "cri_scan.py exited 1"})}
        assert classify._is_expected_quiet(soft_fail, now) is False

    def test_a_real_circuit_breaker_embargo_is_still_honoured(self):
        """The mechanism itself is intact — only the soft-fail misuse is gone."""
        now = datetime.now(timezone.utc)
        future = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
        embargoed = {"state": "error", "last_error": json.dumps({"next_attempt_at": future})}
        assert classify._is_expected_quiet(embargoed, now) is True


class TestUnpersistedBpiIsNotOk:
    def _run(self, monkeypatch, tmp_path, *, clock, indices):
        import bpi_scan

        persisted: list[str] = []
        monkeypatch.setattr(bpi_scan, "_persist_index",
                            lambda sym, payload: persisted.append(sym))
        monkeypatch.setattr(bpi_scan, "install_sigterm_unwind", lambda: None)
        monkeypatch.setattr(
            bpi_scan, "scan_index",
            lambda symbol, **_k: {"taken_at": "2026-08-29T00:00:00Z", "_bpi_rows": [{"d": 1}]},
        )
        monkeypatch.setattr(bpi_scan, "_now_iso", lambda: "2026-08-29T12:00:00Z")
        monkeypatch.setenv(bpi_scan.DATA_DIR_ENV, str(tmp_path))
        monkeypatch.setattr(bpi_scan.time, "monotonic", clock)

        import contextlib

        class _Cycle:
            finished_at = None

        @contextlib.contextmanager
        def _hb(_no_db):
            yield _Cycle()

        monkeypatch.setattr(bpi_scan, "_heartbeat_cycle", _hb)
        return bpi_scan, persisted

    def test_a_run_that_persisted_nothing_is_not_ok(self, monkeypatch, tmp_path):
        """One second before the absolute deadline all three used to start."""
        bpi_scan, persisted = self._run(
            monkeypatch, tmp_path, clock=lambda: 599.0, indices=["NDX", "SPX", "RUT"]
        )
        mirror = tmp_path / "bpi.json"
        mirror.write_text('{"generated_at": "2026-08-01T00:00:00Z", "indices": {}}')

        with pytest.raises(bpi_scan.BpiPersistIncomplete) as exc:
            bpi_scan.run_scan(
                ["NDX", "SPX", "RUT"], backfill=False, no_db=False, sweep_deadline=0.0
            )

        assert persisted == [], (
            "three ~600s persists started into a 1s window and are SIGTERMed "
            f"mid-upsert: {persisted}"
        )
        for symbol in ("NDX", "SPX", "RUT"):
            assert symbol in str(exc.value), str(exc.value)

        assert json.loads(mirror.read_text())["generated_at"] == "2026-08-01T00:00:00Z", (
            "generated_at was restamped for data Turso never received"
        )

    def test_a_run_with_room_still_persists_every_index(self, monkeypatch, tmp_path):
        """Control: the divided reserve must not refuse a healthy run."""
        bpi_scan, persisted = self._run(
            monkeypatch, tmp_path, clock=lambda: 0.0, indices=["NDX", "SPX", "RUT"]
        )
        out = bpi_scan.run_scan(
            ["NDX", "SPX", "RUT"], backfill=False, no_db=False, sweep_deadline=0.0
        )
        assert persisted == ["NDX", "SPX", "RUT"], persisted
        assert (tmp_path / "bpi.json").exists()
        assert out["generated_at"] == "2026-08-29T12:00:00Z"

    def test_an_index_that_did_persist_is_still_mirrored(self, monkeypatch, tmp_path):
        """A partial run publishes what Turso actually received, and no more."""
        import bpi_scan as module

        ticks = {"t": 0.0}

        def clock():
            value = ticks["t"]
            ticks["t"] = 599.0  # the first write consumed the rest of the reserve
            return value

        bpi_scan, persisted = self._run(
            monkeypatch, tmp_path, clock=clock, indices=["NDX", "SPX"]
        )
        with pytest.raises(module.BpiPersistIncomplete):
            bpi_scan.run_scan(["NDX", "SPX"], backfill=False, no_db=False, sweep_deadline=0.0)
        assert persisted == ["NDX"], persisted
        mirrored = json.loads((tmp_path / "bpi.json").read_text())["indices"]
        assert set(mirrored) == {"NDX"}, mirrored

    def test_the_guard_is_reachable_before_the_unit_timeout(self):
        """persist_deadline must sit strictly inside TimeoutStartSec."""
        import bpi_scan

        unit = (Path(__file__).resolve().parents[2] / "cloud" / "services"
                / "radon-bpi.service").read_text(encoding="utf-8")
        timeout = int(
            next(ln for ln in unit.splitlines() if ln.startswith("TimeoutStartSec="))
            .split("=", 1)[1]
        )
        assert bpi_scan.SWEEP_BUDGET_S + bpi_scan.PERSIST_RESERVE_S < timeout, (
            "the guard can only become true at the instant systemd already "
            f"SIGTERMs the unit: {bpi_scan.SWEEP_BUDGET_S}+"
            f"{bpi_scan.PERSIST_RESERVE_S} vs TimeoutStartSec={timeout}"
        )

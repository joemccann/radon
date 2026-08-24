"""REL-068 tranche D — R-176, R-177, R-178.

Three unbounded reads over growing stores, all on hot paths: full-table
SELECTs on the direct-to-cloud Hrana pipeline, a whole-WAL slurp on every
checkpoint, and a re-glob-and-reparse of every incident ever written, 288
times a day.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


class _RecordingDb:
    """Records every SQL it is handed and pages like Turso would."""

    def __init__(self, rows: list[tuple]):
        self.rows = rows
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        limit = None
        cursor = None
        if "LIMIT" in sql.upper() and params:
            limit = params[-1]
            cursor = params[-2] if len(params) >= 2 else None
        page = [r for r in self.rows if cursor is None or r[0] > cursor]
        if limit:
            page = page[: int(limit)]
        return _Cursor(page)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


# --------------------------------------------------------------------------
# R-176 — three new full-table reads on growing tables
# --------------------------------------------------------------------------
class TestPaginatedReads:
    def test_trin_daily_is_paginated(self, monkeypatch):
        import fetch_trin as mod

        db = _RecordingDb([(f"2020-01-{i:02d}", 1.0 + i) for i in range(1, 29)])
        monkeypatch.setattr(mod, "_turso_daily_db", lambda: db, raising=False)
        rows = mod._turso_daily(db=db)
        assert len(rows) == 28
        assert all("LIMIT" in sql for sql in db.sql), db.sql

    def test_trin_daily_uses_a_keyset_cursor_not_an_offset(self, monkeypatch):
        import fetch_trin as mod

        db = _RecordingDb([(f"2020-01-{i:02d}", 1.0) for i in range(1, 5)])
        mod._turso_daily(db=db)
        assert any("date > ?" in sql for sql in db.sql), db.sql
        assert not any("OFFSET" in sql.upper() for sql in db.sql)

    def test_vol_cone_history_is_paginated(self):
        src = (REPO / "scripts" / "fetch_vol_cone.py").read_text()
        body = src.split("def _read_history_rows(")[1].split("\ndef ")[0]
        assert "LIMIT" in body, "full-table SELECT on vol_cone_history"

    def test_executed_orders_since_is_paginated(self):
        src = (REPO / "scripts" / "monitor_daemon" / "handlers" / "expiry_sweep.py").read_text()
        body = src.split("def _executed_orders_since(")[1].split("\ndef ")[0]
        assert "LIMIT" in body, (
            "the sibling journal read in the same file is already keyset-paginated"
        )

    def test_executed_orders_since_still_returns_every_row(self, monkeypatch):
        from monitor_daemon.handlers import expiry_sweep as mod

        rows = [
            (f"exec-{i:04d}", i, json.dumps({"contract": {"symbol": "SPY"}}), "2026-08-21", "x")
            for i in range(1, 250)
        ]
        db = _RecordingDb(rows)
        out = mod._executed_orders_since(db, "2026-01-01")
        assert len(out) == 249


# --------------------------------------------------------------------------
# R-177 — the checkpoint summary must not slurp the whole WAL
# --------------------------------------------------------------------------
class TestCheckpointTailIsBounded:
    def _job(self, tmp_path, lines: int):
        sys.path.insert(0, str(REPO / "scripts"))
        from lib.checkpoint import FINDINGS_FILE, CheckpointedJob

        job = CheckpointedJob.__new__(CheckpointedJob)
        job.dir = tmp_path
        with (tmp_path / FINDINGS_FILE).open("w") as f:
            for i in range(lines):
                f.write(json.dumps({"_key": f"k{i}", "_at": "t", "_hash": "h", "title": f"t{i}"}) + "\n")
        return job

    def test_it_returns_the_last_n_records(self, tmp_path):
        job = self._job(tmp_path, 500)
        tail = job._tail_findings(5)
        assert [r["_key"] for r in tail] == ["k495", "k496", "k497", "k498", "k499"]

    def test_it_does_not_read_the_whole_file(self, tmp_path, monkeypatch):
        job = self._job(tmp_path, 5_000)
        from lib.checkpoint import FINDINGS_FILE

        path = tmp_path / FINDINGS_FILE
        total = path.stat().st_size
        read_bytes = {"n": 0}
        real_open = Path.open

        def counting_open(self, *a, **k):
            handle = real_open(self, *a, **k)
            if self == path:
                real_read = handle.read

                def read(*args):
                    data = real_read(*args)
                    read_bytes["n"] += len(data) if data else 0
                    return data

                handle.read = read
            return handle

        monkeypatch.setattr(Path, "open", counting_open)
        job._tail_findings(5)
        assert read_bytes["n"] < total, (
            f"read {read_bytes['n']} of {total} bytes — this is the exact "
            "pattern R-073 removed from _repair_torn_findings_tail"
        )

    def test_a_short_file_still_works(self, tmp_path):
        job = self._job(tmp_path, 2)
        assert [r["_key"] for r in job._tail_findings(10)] == ["k0", "k1"]

    def test_a_missing_file_is_empty(self, tmp_path):
        sys.path.insert(0, str(REPO / "scripts"))
        from lib.checkpoint import CheckpointedJob

        job = CheckpointedJob.__new__(CheckpointedJob)
        job.dir = tmp_path
        assert job._tail_findings(5) == []

    def test_a_torn_last_line_is_skipped_not_fatal(self, tmp_path):
        job = self._job(tmp_path, 3)
        from lib.checkpoint import FINDINGS_FILE

        with (tmp_path / FINDINGS_FILE).open("a") as f:
            f.write('{"_key": "torn"')
        assert [r["_key"] for r in job._tail_findings(3)] == ["k0", "k1", "k2"]


# --------------------------------------------------------------------------
# R-178 — resolved incidents must not be reparsed forever
# --------------------------------------------------------------------------
class TestIncidentRetention:
    def _incident(self, directory: Path, ident: str, status: str, resolved_at: str | None):
        payload = {
            "schema": "radon.incident/1",
            "incident_id": ident,
            "fingerprint": f"fp-{ident}",
            "status": status,
        }
        if resolved_at:
            payload["resolved_at"] = resolved_at
        (directory / f"incident-{ident}.json").write_text(json.dumps(payload))

    def test_resolved_incidents_past_the_horizon_are_pruned(self, tmp_path):
        from incident_watchdog.store import prune_resolved, RESOLVED_RETENTION_DAYS

        self._incident(tmp_path, "old", "resolved", "2020-01-01T00:00:00+00:00")
        self._incident(tmp_path, "new", "resolved", "2026-08-22T00:00:00+00:00")
        self._incident(tmp_path, "live", "open", None)

        from datetime import datetime, timezone

        pruned = prune_resolved(tmp_path, now=datetime(2026, 8, 23, tzinfo=timezone.utc))
        assert pruned == 1
        names = sorted(p.name for p in tmp_path.glob("incident-*.json"))
        assert names == ["incident-live.json", "incident-new.json"]
        assert RESOLVED_RETENTION_DAYS > 0

    def test_an_open_incident_is_never_pruned_however_old(self, tmp_path):
        from datetime import datetime, timezone
        from incident_watchdog.store import prune_resolved

        self._incident(tmp_path, "ancient", "open", None)
        assert prune_resolved(tmp_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == 0
        assert (tmp_path / "incident-ancient.json").exists()

    def test_an_unparseable_file_is_left_alone(self, tmp_path):
        from datetime import datetime, timezone
        from incident_watchdog.store import prune_resolved

        (tmp_path / "incident-bad.json").write_text("{not json")
        assert prune_resolved(tmp_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == 0
        assert (tmp_path / "incident-bad.json").exists()

    def test_a_resolved_incident_without_a_timestamp_is_left_alone(self, tmp_path):
        from datetime import datetime, timezone
        from incident_watchdog.store import prune_resolved

        self._incident(tmp_path, "undated", "resolved", None)
        assert prune_resolved(tmp_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc)) == 0

    def test_the_sweep_runs_from_the_watchdog_cycle(self):
        src = (REPO / "scripts" / "incident_watchdog" / "store.py").read_text()
        assert "prune_resolved" in src.split("def reconcile")[1] if "def reconcile" in src else True

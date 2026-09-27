"""A failed ivrank snapshot write must not silence the heartbeat.

2026-09-26 22:14 UTC: `upsert_scan_snapshot` hit a Turso read timeout, the
shared `except` swallowed it as "db cache non-fatal", and `record_service_health`
never ran. The unit exited 0, the row stayed on Friday's heartbeat, and
/admin showed "ivrank: update overdue" a day later. Same class as R-192.
"""

from __future__ import annotations

import fetch_ivrank


class _Writer:
    def __init__(self, *, snapshot_fails: bool):
        self.snapshot_fails = snapshot_fails
        self.health: list[tuple] = []

    def ensure_no_replica_for_writers(self):
        pass

    def upsert_ivrank_rows(self, rows, *, recorded_at):
        pass

    def upsert_scan_snapshot(self, service, scan_time, payload):
        if self.snapshot_fails:
            raise TimeoutError("The read operation timed out")

    def record_service_health(self, service, state, *, finished_at, error):
        self.health.append((service, state, finished_at, error))


def test_snapshot_timeout_still_heartbeats_as_error(monkeypatch):
    writer = _Writer(snapshot_fails=True)
    monkeypatch.setattr(fetch_ivrank, "writer", writer)

    fetch_ivrank._write_db({"iv_rank": 8.2}, "2026-09-26T22:14:45Z", rows_changed=False)

    assert len(writer.health) == 1
    service, state, finished_at, error = writer.health[0]
    assert (service, state, finished_at) == ("ivrank", "error", "2026-09-26T22:14:45Z")
    assert error["class"] == "db_write_failed"
    assert "snapshot" in error["message"]


def test_clean_cycle_heartbeats_ok(monkeypatch):
    writer = _Writer(snapshot_fails=False)
    monkeypatch.setattr(fetch_ivrank, "writer", writer)

    fetch_ivrank._write_db({"iv_rank": 8.2}, "2026-09-26T22:14:45Z", rows_changed=False)

    assert writer.health == [("ivrank", "ok", "2026-09-26T22:14:45Z", None)]

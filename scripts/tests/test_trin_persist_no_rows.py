"""REL-053 / R-125 writer half — `persist_result` bumps `scan_time` and
records `ok` on a run that added nothing.

The `_nothing_to_persist` guard fires only when the CACHED payload is also
empty, so an ordinary cycle that produced no new sample and no new daily row
— IB unreachable, off-hours, the source unchanged — still upserts a snapshot
carrying a fresh `scan_time` and heartbeats `ok`. Every downstream freshness
judgement is made against that timestamp, so the writer itself is what makes
the route-side `fresh` gate (REL-052 / R-125's other half) unable to tell a
live sample from a re-serialised cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


class _Writer:
    def __init__(self) -> None:
        self.snapshots: list[tuple] = []
        self.health: list[tuple] = []
        self.samples: list = []
        self.daily: list = []

    def ensure_no_replica_for_writers(self):
        return None

    def upsert_trin_samples(self, rows, recorded_at=None):
        self.samples.append((rows, recorded_at))

    def upsert_trin_daily_rows(self, rows, recorded_at=None):
        self.daily.append((rows, recorded_at))

    def upsert_scan_snapshot(self, service, scan_time, payload):
        self.snapshots.append((service, scan_time, payload))

    def record_service_health(self, service, state, **kw):
        self.health.append((service, state, kw.get("error")))


def _payload_with_history() -> dict:
    return {
        "scan_time": "2026-08-23T18:00:00Z",
        "hourly": [{"bucket": "2026-08-21T14:30", "ts": "2026-08-21T14:35:00Z", "trin": 0.9}],
        "daily": [("2026-08-21", 0.95)],
        "current": {"ts": "2026-08-21T14:35:00Z", "trin": 0.9},
    }


class TestNoNewRowsIsNotAFreshScan:
    def test_it_does_not_bump_the_snapshot_timestamp(self, monkeypatch, tmp_path):
        import fetch_trin as trin

        writer = _Writer()
        monkeypatch.setattr(trin, "writer", writer, raising=False)
        monkeypatch.setattr(trin, "TRIN_JSON", tmp_path / "trin.json")

        trin.persist_result(_payload_with_history(), [], [])

        assert writer.snapshots == [], (
            "a cycle that added nothing still upserted a snapshot with a fresh "
            "scan_time, which is the timestamp every freshness gate reads"
        )

    def test_it_STILL_heartbeats_so_a_silent_writer_is_noticed(self, monkeypatch, tmp_path):
        """The heartbeat and the snapshot answer different questions. The
        row is "did this writer run" (feedback_service_health_heartbeat — a
        service that skips it on a nochange cycle goes silently dead); the
        snapshot's `scan_time` is "how old is the DATA". R-125 is only about
        the second one, so the heartbeat must survive."""
        import fetch_trin as trin

        writer = _Writer()
        monkeypatch.setattr(trin, "writer", writer, raising=False)
        monkeypatch.setattr(trin, "TRIN_JSON", tmp_path / "trin.json")

        trin.persist_result(_payload_with_history(), [], [])

        assert writer.health and writer.health[-1][1] == "ok"
        assert writer.snapshots == []

    def test_a_real_sample_still_writes_everything(self, monkeypatch, tmp_path):
        import fetch_trin as trin

        writer = _Writer()
        monkeypatch.setattr(trin, "writer", writer, raising=False)
        monkeypatch.setattr(trin, "TRIN_JSON", tmp_path / "trin.json")
        sample = {
            "ts": "2026-08-21T15:30:00Z", "session_date": "2026-08-21", "trin": 0.7,
            "adv": 1, "dec": 1, "up_vol": 1.0, "down_vol": 1.0, "source": "ib",
        }

        trin.persist_result(_payload_with_history(), [sample], [])

        assert len(writer.snapshots) == 1
        assert writer.health[-1][1] == "ok"

    def test_a_degraded_run_with_no_rows_still_records_the_error(self, monkeypatch, tmp_path):
        """A dead source must still page (REL-049 / R-098) — the no-rows
        short-circuit may not swallow an error heartbeat."""
        import fetch_trin as trin

        writer = _Writer()
        monkeypatch.setattr(trin, "writer", writer, raising=False)
        monkeypatch.setattr(trin, "TRIN_JSON", tmp_path / "trin.json")

        trin.persist_result(
            _payload_with_history(), [], [],
            {"message": "trin: live IB sample unavailable", "class": "source_down"},
        )

        assert writer.health[-1][1] == "error"
        assert writer.snapshots == []

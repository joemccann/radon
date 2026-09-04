"""Error blobs without a `message` key must still render their text.

`drift_audit.py` writes `{"summary": ..., "drift_count": ...}`; the
watchdog rendered every one of those as `in error state: unknown`, so a
22-cycle config-drift page named no drifted file.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

_NOW = datetime.now(timezone.utc).replace(microsecond=0)

_DRIFT_BLOB = {
    "summary": "config drift: fleet-dropin",
    "drift_count": 1,
    "drifts": [{"id": "fleet-dropin", "detail": "checksum mismatch"}],
}


def _seed_service_health(db_conn, service: str, state: str, updated_at: datetime, error: dict):
    db_conn.execute(
        """
        INSERT OR REPLACE INTO service_health
          (service, state, last_attempt_started_at, last_attempt_finished_at,
           last_error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            service,
            state,
            None,
            updated_at.isoformat().replace("+00:00", "Z"),
            json.dumps(error),
            updated_at.isoformat().replace("+00:00", "Z"),
        ),
    )
    db_conn.commit()


class TestErrorMessageExtraction:
    def test_summary_only_blob_renders_in_check_message(self, db_conn):
        from watchdog import check

        _seed_service_health(
            db_conn, "config-drift", "error", _NOW - timedelta(minutes=1), _DRIFT_BLOB
        )
        outcome = check.check_service(
            service="config-drift", kind="error", now=_NOW, market_state="closed"
        )
        assert outcome.message == "in error state: config drift: fleet-dropin"

    def test_summary_only_blob_renders_in_grouping_reason(self, db_conn):
        from watchdog import check, grouping

        _seed_service_health(
            db_conn, "config-drift", "error", _NOW - timedelta(minutes=1), _DRIFT_BLOB
        )
        outcome = check.check_service(
            service="config-drift", kind="error", now=_NOW, market_state="closed"
        )
        reason = grouping._failure_reason(outcome)
        assert "fleet-dropin" in reason
        assert "unknown" not in reason
